#-------------------------------------------------------------------------------
# Name:        GAR analysis for Boreal Caribou herds (project GR_2024_528)
#
# Purpose:     This script generates summary statistics for GAR orders 
#              by TSA, Caribou herd and PBCPR Mgmt type
#              
# Input(s):    (1) json file (H:\config\db_config.json) containing BCGW connection params.
#              (2) PBCPR Mgmt type (featureclass)(condifential).
#              (3) workspace- folder location to create db and outputs.           
#
# Author:      Moez Labiadh - GeoBC, GSS
#
# Created:     2024-06-12
# Updated:     test
#-------------------------------------------------------------------------------

import warnings
warnings.simplefilter(action='ignore')

import os
import json
import timeit
import cx_Oracle
import duckdb
import pandas as pd
import geopandas as gpd
from shapely import wkb, wkt
from datetime import datetime


class OracleConnector:
    def __init__(self, dbname='BCGW'):
        self.dbname = dbname
        self.cnxinfo = self.get_db_cnxinfo()

    def get_db_cnxinfo(self):
        """ Retrieves db connection params from the config file"""
        with open(r'H:\config\db_config.json', 'r') as file:
            data = json.load(file)
        
        if self.dbname in data:
            return data[self.dbname]
        
        raise KeyError(f"Database '{self.dbname}' not found.")
    
    def connect_to_db(self):
        """ Connects to Oracle DB and create a cursor"""
        try:
            self.connection = cx_Oracle.connect(self.cnxinfo['username'], 
                                                self.cnxinfo['password'], 
                                                self.cnxinfo['hostname'], 
                                                encoding="UTF-8")
            self.cursor = self.connection.cursor()
            print  ("..Successffuly connected to the database")
        except Exception as e:
            raise Exception(f'..Connection failed: {e}')

    def disconnect_db(self):
        """Close the Oracle connection and cursor"""
        if hasattr(self, 'cursor') and self.cursor:
            self.cursor.close()
        if hasattr(self, 'connection') and self.connection:
            self.connection.close()
            print("....Disconnected from the database")


class DuckDBConnector:
    def __init__(self, db=':memory:'):
        self.db = db
        self.conn = None
    
    def connect_to_db(self):
        """Connects to a DuckDB database and installs spatial extension."""
        self.conn = duckdb.connect(self.db)
        self.conn.install_extension('spatial')
        self.conn.load_extension('spatial')
        return self.conn
    
    def disconnect_db(self):
        """Disconnects from the DuckDB database."""
        if self.conn is not None:
            self.conn.close()
            self.conn = None
            

def process_pbcpr_data (pbcpr_gdb):
    """Return a gdf of PBCPR Mgmt types"""
    gdf= gpd.read_file(pbcpr_gdb, layer='PBCPR_Cleaned')
    gdf= gdf.dissolve(by='Management').reset_index()
    gdf['geometry'] = gdf['geometry'].apply(
                lambda geom: wkb.loads(
                        wkb.dumps(geom, output_dimension=2)))
    gdf = gdf.loc[gdf['Management'].str.contains('MGMT')]
    gdf['MGMT_TYPE']= gdf['Management'].str[:11]
    gdf= gdf[['MGMT_TYPE', 'geometry']]
    
    env = gdf.geometry.unary_union.envelope
    gdf_env = gpd.GeoDataFrame(geometry=[env], crs= gdf.crs)
    
    return gdf, gdf_env


def read_query(connection,cursor,query,bvars):
    "Returns a df containing SQL Query results"
    cursor.execute(query, bvars)
    names = [x[0] for x in cursor.description]
    rows = cursor.fetchall()
    df = pd.DataFrame(rows, columns=names)
    
    return df


 
def get_wkb_srid(gdf):
    """Returns SRID and WKB objects from gdf"""
    srid = gdf.crs.to_epsg()
    geom = gdf['geometry'].iloc[0]

    wkb_aoi = wkb.dumps(geom, output_dimension=2)
        
    return wkb_aoi, srid


def load_Orc_sql():
    orSql= {}
    orSql['herds'] = """
        SELECT
            HERD_NAME,
            SDO_UTIL.TO_WKTGEOMETRY(GEOMETRY) AS GEOMETRY 
            
        FROM 
            WHSE_WILDLIFE_INVENTORY.GCPB_CARIBOU_POPULATION_SP
        WHERE
            HERD_NAME IN ('Calendar','Maxhamish','Snake-Sahtaneh','Westside Fort Nelson')
                    """

    orSql['tsa'] = """
        SELECT
            TSA_NUMBER_DESCRIPTION,
            SDO_UTIL.TO_WKTGEOMETRY(GEOMETRY) AS GEOMETRY 
            
        FROM 
            WHSE_ADMIN_BOUNDARIES.FADM_TSA
        WHERE
            TSB_NUMBER is null
            AND  TSA_NUMBER_DESCRIPTION IN ('Fort Nelson TSA','Fort St. John TSA')
                    """
                        
                        
    orSql['uwr'] = """
        SELECT
            UWR_NUMBER,
            TIMBER_HARVEST_CODE,
            SPECIES_1,
            SDO_UTIL.TO_WKTGEOMETRY(GEOMETRY) AS GEOMETRY 
        FROM
            WHSE_WILDLIFE_MANAGEMENT.WCP_UNGULATE_WINTER_RANGE_SP
        WHERE
            SDO_WITHIN_DISTANCE (GEOMETRY, 
                                 SDO_GEOMETRY(:wkb_aoi, :srid), 'distance=500 unit=m') = 'TRUE'
                    """

    orSql['wha'] = """
        SELECT
            wha.TAG,
            wha.TIMBER_HARVEST_CODE,
            SDO_UTIL.TO_WKTGEOMETRY(wha.GEOMETRY) AS GEOMETRY 
        FROM
            WHSE_WILDLIFE_MANAGEMENT.WCP_WILDLIFE_HABITAT_AREA_POLY wha
        WHERE
            SDO_WITHIN_DISTANCE (wha.GEOMETRY, 
                                 SDO_GEOMETRY(:wkb_aoi, :srid), 'distance=500 unit=m') = 'TRUE'
                    """

    orSql['ogm'] = """
        SELECT
            LEGAL_OGMA_PROVID,
            SDO_UTIL.TO_WKTGEOMETRY(GEOMETRY) AS GEOMETRY 
        FROM
            WHSE_LAND_USE_PLANNING.RMP_OGMA_LEGAL_CURRENT_SVW
        WHERE
            SDO_WITHIN_DISTANCE (GEOMETRY, 
                                 SDO_GEOMETRY(:wkb_aoi, :srid), 'distance=500 unit=m') = 'TRUE'
                    """    
    orSql['fda'] = """
        SELECT
            CURRENT_PRIORITY_DEFERRAL_ID,
            SDO_UTIL.TO_WKTGEOMETRY(SHAPE) AS GEOMETRY 
        FROM
            WHSE_FOREST_VEGETATION.OGSR_PRIORITY_DEF_AREA_CUR_SP
        WHERE
            SDO_WITHIN_DISTANCE (SHAPE, 
                                 SDO_GEOMETRY(:wkb_aoi, :srid), 'distance=500 unit=m') = 'TRUE'
                    """ 
                    
    return orSql

def read_oracle_data (orcCnx, orcCur, dckCnx, dict_sqls, wkb_aoi, srid, data_dict):
    counter = 1
    for k, v in dict_sqls.items():
        print(f'....table {counter} of {len(dict_sqls)}: {k}')
        if ':wkb_aoi' in v:
            orcCur.setinputsizes(wkb_aoi=cx_Oracle.BLOB)
            bvars = {'wkb_aoi':wkb_aoi,'srid':srid}
            df = read_query(orcCnx, orcCur, v ,bvars)
        else:
            df = pd.read_sql(v, orcCnx)
        
        data_dict[k]= df
        
        counter+=1
    
    return data_dict


def read_local_data(loc_dict, data_dict):
    counter = 1
    for k, v in loc_dict.items():
        print(f'..table {counter} of {len(loc_dict)}: {k}')
        df= v
        df['GEOMETRY']= df['geometry'].apply(lambda x: wkt.dumps(x, output_dimension=2))
        df = df.drop(columns=['geometry'])
        
        data_dict[k]= df
        
        counter+=1
        
    return data_dict    
        

def add_data_to_duckdb(data_dict):
    counter = 1
    for k, v in data_dict.items():
        print(f'..table {counter} of {len(data_dict)}: {k}')
        dck_tab_list= dckCnx.execute('SHOW TABLES').df()['name'].to_list()
        
        if k in dck_tab_list:
            dck_row_count= dckCnx.execute(f'SELECT COUNT(*) FROM {k}').fetchone()[0]
            dck_col_nams= dckCnx.execute(f"""SELECT column_name 
                                             FROM INFORMATION_SCHEMA.COLUMNS 
                                             WHERE table_name = '{k}'""").df()['column_name'].to_list()
                
            if (dck_row_count != len(v)) or (set(list(v.columns)) != set(dck_col_nams)):
                print (f'....import to Duckdb ({v.shape[0]} rows)')
                create_table_query = f"""
                CREATE OR REPLACE TABLE {k} AS
                  SELECT * EXCLUDE geometry, ST_GeomFromText(geometry) AS GEOMETRY
                  FROM v;
                """
                dckCnx.execute(create_table_query)
            else:
                print('....data already in db: skip importing')
                pass
        
        else:
            print (f'....import to Duckdb ({v.shape[0]} rows)')
            create_table_query = f"""
            CREATE OR REPLACE TABLE {k} AS
              SELECT * EXCLUDE geometry, ST_GeomFromText(geometry) AS GEOMETRY
              FROM v;
            """
            dckCnx.execute(create_table_query)
         
        counter += 1    


def load_dck_sql():
    dkSql= {}
    
    dkSql['mgt_q']="""
        SELECT
            mgm.MGMT_TYPE,
            tsa.TSA_NUMBER_DESCRIPTION AS TSA,
            ROUND(ST_Area(
                ST_Intersection(mgm.geometry, tsa.geometry))/10000, 2) AS MGMT_TYPE_AREA_HA
        FROM 
            mgmt_types AS mgm
            JOIN 
            tsa ON ST_Intersects(mgm.geometry, tsa.geometry)
        ORDER BY 
            mgm.MGMT_TYPE;
                    """   

    dkSql['hrd_mgt_q']="""
        SELECT
            tsa.TSA_NUMBER_DESCRIPTION AS TSA,
            hrd.HERD_NAME,
            mgm.MGMT_TYPE,
            ROUND(ST_Area(ST_Intersection(
                ST_Intersection(hrd.geometry, mgm.geometry), tsa.geometry))/10000, 2) AS OVERLAP_HA
        FROM 
            herds AS hrd
            JOIN 
                mgmt_types AS mgm ON ST_Intersects(hrd.geometry, mgm.geometry)
            JOIN 
                tsa ON ST_Intersects(hrd.geometry, tsa.geometry)
                    AND ST_Intersects(mgm.geometry, tsa.geometry)
        ORDER BY 
            tsa.TSA_NUMBER_DESCRIPTION,
            hrd.HERD_NAME;
                    """
                    
    dkSql['uwr_q']="""
        SELECT 
            tsa.TSA_NUMBER_DESCRIPTION AS TSA,
            hrd.HERD_NAME,
            mgm.MGMT_TYPE,
            uwr.TIMBER_HARVEST_CODE,
            ROUND(ST_Area(
                ST_Intersection(
                    ST_Intersection(
                        ST_Intersection(hrd.geometry, mgm.geometry), uwr.geometry), tsa.geometry))/10000, 2) AS INTERSECT_HA
        FROM 
            herds AS hrd
            JOIN 
                mgmt_types AS mgm ON ST_Intersects(hrd.geometry, mgm.geometry)
            JOIN 
                uwr ON ST_Intersects(hrd.geometry, uwr.geometry) 
                    AND ST_Intersects(mgm.geometry, uwr.geometry)
            JOIN 
                tsa ON ST_Intersects(hrd.geometry, tsa.geometry)
                    AND ST_Intersects(mgm.geometry, tsa.geometry)
                    AND ST_Intersects(uwr.geometry, tsa.geometry);
                    """

    dkSql['wha_q']="""
        SELECT 
            tsa.TSA_NUMBER_DESCRIPTION AS TSA,
            hrd.HERD_NAME,
            mgm.MGMT_TYPE,
            wha.TIMBER_HARVEST_CODE,
            ROUND(ST_Area(
                ST_Intersection(
                    ST_Intersection(
                        ST_Intersection(hrd.geometry, mgm.geometry), wha.geometry), tsa.geometry))/10000, 2) AS INTERSECT_HA
        FROM 
            herds AS hrd
            JOIN 
                mgmt_types AS mgm ON ST_Intersects(hrd.geometry, mgm.geometry)
            JOIN 
                wha ON ST_Intersects(hrd.geometry, wha.geometry) 
                    AND ST_Intersects(mgm.geometry, wha.geometry)
            JOIN 
                tsa ON ST_Intersects(hrd.geometry, tsa.geometry)
                    AND ST_Intersects(mgm.geometry, tsa.geometry)
                    AND ST_Intersects(wha.geometry, tsa.geometry);
                    """

    dkSql['fda_q']="""
        SELECT
            tsa.TSA_NUMBER_DESCRIPTION AS TSA,
            hrd.HERD_NAME,
            mgm.MGMT_TYPE,
            fda.CURRENT_PRIORITY_DEFERRAL_ID,
            ROUND(ST_Area(
                ST_Intersection(
                    ST_Intersection(
                        ST_Intersection(hrd.geometry, mgm.geometry), fda.geometry), tsa.geometry))/10000, 2) AS INTERSECT_HA
        FROM 
            herds AS hrd
            JOIN 
                mgmt_types AS mgm ON ST_Intersects(hrd.geometry, mgm.geometry)
            JOIN 
                fda ON ST_Intersects(hrd.geometry, fda.geometry)
                    AND ST_Intersects(mgm.geometry, fda.geometry)
            JOIN 
                tsa ON ST_Intersects(hrd.geometry, tsa.geometry)
                    AND ST_Intersects(mgm.geometry, tsa.geometry)
                    AND ST_Intersects(fda.geometry, tsa.geometry);
                    """
                    
                 
    return dkSql


def run_duckdb_queries (dckCnx, dict_sqls):
    """Run duckdb queries """
    results= {}
    counter = 1
    for k, v in dict_sqls.items():
        print(f'..running query {counter} of {len(dict_sqls)}: {k}')
        results[k]= dckCnx.execute(v).df()
        
        counter+= 1
        
    return results


def generate_report (workspace, df_list, sheet_list,filename):
    """ Exports dataframes to multi-tab excel spreasheet"""
    outfile= os.path.join(workspace, filename + '.xlsx')

    writer = pd.ExcelWriter(outfile,engine='xlsxwriter')

    for dataframe, sheet in zip(df_list, sheet_list):
        dataframe = dataframe.reset_index(drop=True)
        dataframe.index = dataframe.index + 1

        dataframe.to_excel(writer, sheet_name=sheet, index=False, startrow=0 , startcol=0)

        worksheet = writer.sheets[sheet]
        #workbook = writer.book

        worksheet.set_column(0, dataframe.shape[1], 23)

        col_names = [{'header': col_name} for col_name in dataframe.columns[1:-1]]
        col_names.insert(0,{'header' : dataframe.columns[0], 'total_string': 'Total'})
        col_names.append ({'header' : dataframe.columns[-1], 'total_function': 'sum'})


        worksheet.add_table(0, 0, dataframe.shape[0]+1, dataframe.shape[1]-1, {
            'total_row': True,
            'columns': col_names})

    writer.save()
    writer.close()
    
    
if __name__ == "__main__":
    start_t = timeit.default_timer() #start time 
    
    wks= r'W:\srm\nr\crp\projects\2024\GR_2024_528'

    print ('Connecting to databases')    
    
    print ('..connect to BCGW') 
    Oracle = OracleConnector()
    Oracle.connect_to_db()
    orcCnx= Oracle.connection
    orcCur= Oracle.cursor
    
    print ('..connect to Duckdb') 
    projDB= os.path.join(wks, 'work', 'GR_2024_528_mgmt_type_GAR_analysis', 'habitat_types_analysis.db')
    Duckdb= DuckDBConnector(db= projDB)
    Duckdb.connect_to_db()
    dckCnx= Duckdb.conn
    
    print ('\nReading the Mgmt types dataset')   
    pbcpr_gdb= gdb= os.path.join(wks, 'source data' ,'incoming','BorealCaribouRecovery.gdb')
    gdf, gdf_env= process_pbcpr_data (pbcpr_gdb)
    
    wkb_aoi, srid= get_wkb_srid(gdf_env)
    
    try:
        print ('\nReading input data')
        orSql_dict= load_Orc_sql ()
        loc_dict={}
        loc_dict['mgmt_types']= gdf
        
        data_dict= {}
        print('..reading from Oracle')
        data_dict= read_oracle_data (orcCnx, orcCur, dckCnx, orSql_dict, wkb_aoi, srid, data_dict)
        
        print('\n..reading from Local files')
        data_dict= read_local_data(loc_dict, data_dict)
        
        print('\nWriting data to duckdb')
        add_data_to_duckdb(data_dict)
        
        print ('\nRunning queries')
        dksql= load_dck_sql()
        q_rslt= run_duckdb_queries (dckCnx, dksql) 
        

    except Exception as e:
        raise Exception(f"Error occurred: {e}")  

    finally: 
        Oracle.disconnect_db()
        Duckdb.disconnect_db()
    
      
    print ('\nComputing summary stats')
    #uwr
    df_uwr= q_rslt['uwr_q']
    df_uwr= df_uwr.groupby(['TSA','HERD_NAME', 
                            'MGMT_TYPE', 'TIMBER_HARVEST_CODE'])['INTERSECT_HA'].sum().reset_index() 
    
    df_uwr.loc[df_uwr['TIMBER_HARVEST_CODE'] == 'NO HARVEST ZONE', 'GAR_TYPE'] = 'UWR_NO_HARVEST'
    df_uwr.loc[df_uwr['TIMBER_HARVEST_CODE'] == 'CONDITIONAL HARVEST ZONE', 'GAR_TYPE'] = 'UWR_CNDTL_HARVEST'
    df_uwr.drop(columns=['TIMBER_HARVEST_CODE'], inplace=True)
    
    #wha
    df_wha= q_rslt['wha_q']
    df_wha= df_wha.groupby(['TSA','HERD_NAME', 
                            'MGMT_TYPE', 'TIMBER_HARVEST_CODE'])['INTERSECT_HA'].sum().reset_index() 
    
    df_wha.loc[df_wha['TIMBER_HARVEST_CODE'] == 'NO HARVEST ZONE', 'GAR_TYPE'] = 'WHA_NO_HARVEST'
    df_wha.loc[df_wha['TIMBER_HARVEST_CODE'] == 'CONDITIONAL HARVEST ZONE', 'GAR_TYPE'] = 'WHA_CNDTL_HARVEST'
    df_wha.drop(columns=['TIMBER_HARVEST_CODE'], inplace=True)
    
    df_fda= q_rslt['fda_q']
    df_fda= df_fda.groupby(['TSA', 'HERD_NAME', 'MGMT_TYPE'])['INTERSECT_HA'].sum().reset_index()  
    df_fda['GAR_TYPE'] = 'PRIORITY_DEF_AREA'
    
    #concatinate dfs
    df= pd.concat([df_uwr, df_wha, df_fda]).reset_index(drop= True)  
    
    '''
    #pivot table
    df= pd.pivot_table(df_all, 
                       values='INTERSECT_HA', 
                       index=['HERD_NAME', 'MGMT_TYPE'],
                       columns=['TYPE']).reset_index()
    
    df.fillna(0, inplace= True)
    '''
    
    #change col order
    df= df[['TSA','HERD_NAME','MGMT_TYPE', 'GAR_TYPE', 'INTERSECT_HA']]
    
    
    #add mgmt type area
    df_mgt= q_rslt['mgt_q']
    
    #add herd/mgmt type overlap
    df_hrd_mgt= q_rslt['hrd_mgt_q']
    
    #remove zeros
    dfs= [df_mgt, df_hrd_mgt, df]
    for i in range(len(dfs)):
        df = dfs[i]
        df = df[(df != 0).all(axis=1)]
        dfs[i] = df
    
    print ('\nGenerating a report')
    outloc= os.path.join(wks, 'deliverables', 'GAR_analysis')
    today = datetime.today().strftime('%Y%m%d')
    filename= today + '_borealCaribou_pbcprMgmtTypes'
    
    sheets= ['MGMT TYPES', 'HERD-MGMT TYPE OVERLAP', 'GAR ANALYSIS SUMMARY']
    generate_report (outloc, dfs, sheets, filename)
  
    finish_t = timeit.default_timer() #finish time
    t_sec = round(finish_t-start_t)
    mins = int (t_sec/60)
    secs = int (t_sec%60)
    print (f'\nProcessing Completed in {mins} minutes and {secs} seconds')  