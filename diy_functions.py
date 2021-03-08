#!/usr/bin/env python
"""
Bulk download Ocean Color images.

MIT License

Copyright (c) 2019 Nils Haentjens & Guillaume Bourdin
Updated by Kelsey Bisson, 2021
"""

import csv
import sys
from datetime import datetime, timedelta
from getpass import getpass
import requests
from requests.auth import HTTPBasicAuth
import re
import os
from time import sleep
from pandas import DataFrame, read_csv
import socket
import math
# import timeout_decorator
# from signal import signal
# from multiprocessing import Process, Event, Lock

__version__ = "0.6.0"
verbose = False

# Set constants
URL_L12BROWSER = 'https://oceancolor.gsfc.nasa.gov/cgi/browse.pl'
URL_DIRECT_ACCESS = 'https://oceandata.sci.gsfc.nasa.gov/'
URL_SEARCH_API = 'https://oceandata.sci.gsfc.nasa.gov/api/file_search'
URL_GET_FILE_CGI = 'https://oceandata.sci.gsfc.nasa.gov/cgi/getfile/'
URL_CMR = 'https://cmr.earthdata.nasa.gov/search/granules.json?provider=OB_DAAC'
URL_GET_FILE_CMR = 'https://oceandata.sci.gsfc.nasa.gov/cmr/getfile/'
URL_COPERNICUS = 'https://scihub.copernicus.eu/dhus/search?q='
URL_SEARCH_CREODIAS = 'https://finder.creodias.eu/resto/api/collections/'
URL_CREODIAS_LOGIN = 'https://auth.creodias.eu/auth/realms/DIAS/protocol/openid-connect/token'
URL_CREODIAS_GET_FILE = 'https://zipper.creodias.eu/download'

# Documentation of Ocean Color Data Format Specification
#   https://oceancolor.gsfc.nasa.gov/products/
INSTRUMENT_FILE_ID = {'SeaWiFS': 'S', 'MODIS-Aqua': 'A', 'MODIS-Terra': 'T', 'OCTS': 'O', 'CZCS': 'C',
                      'MERIS': 'M', 'VIIRSN': 'V', 'VIIRSJ1': 'V', 'HICO': 'H', 'OLCI': 'Sentinel3', 'SLSTR': 'Sentinel3', 'MSI': 'Sentinel2'}
INSTRUMENT_QUERY_ID = {'SeaWiFS': 'MLAC', 'MODIS-Aqua': 'amod', 'MODIS-Terra': 'tmod', 'OCTS': 'oc', 'CZCS': 'cz',
                       'MERIS': 'RR', 'VIIRSN': 'vrsn', 'VIIRSJ1': 'vrj1', 'HICO': 'hi', 'OLCI': 'OL', 'MSI': 'MSI', 'SLSTR': 'SL'}
DATA_TYPE_ID = {'SeaWiFS': 'LAC', 'MODIS-Aqua': 'LAC', 'MODIS-Terra': 'LAC', 'OCTS': 'LAC', 'CZCS': '',
                'MERIS': 'RR', 'VIIRSN': 'SNPP', 'VIIRSJ1': 'JPSS1','HICO': 'ISS', 'OLCI_L1_ERR': 'ERR', 'OLCI_L1_EFR': 'EFR', 
                'SLSTR_L1_RBT': 'RBT', 'OLCI_L2_WRR': 'WRR', 'OLCI_L2_WFR': 'WFR', 'SLSTR_L2_WCT': 'WCT', 'SLSTR_L2_WST': 'WST',
                'MSI_L1C': 'L1C', 'MSI_L2A': 'L2A'} # copernicus 'MSI_L2A': 'S2MSI2A'
LEVEL_CREODIAS = {'L1': 'LEVEL1', 'L2': 'LEVEL2', 'L1C': 'LEVEL1C', 'L2A': 'LEVEL2A'}
SEARCH_CMR = {'SeaWiFS': 'SEAWIFS', 'MODIS-Aqua': 'MODISA', 'MODIS-Terra': 'MODIST',
              'OCTS': 'OCTS', 'CZCS': 'CZCS', 'VIIRSN': 'VIIRSN', 'VIIRSJ1': 'VIIRSJ1'}
EXTENSION_L1A = {'MODIS-Aqua': '','MODIS-Terra': '', 'VIIRSN': '.nc', 'VIIRSJ1': '.nc'}


def get_platform(dates, instrument, level):
    # Get acces plateform depending on product and date:
    # - COPERNICUS: MSI-L2A < 12 month, OLCI # DEPRECATED
    # - CREODIAS: MSI, OLCI, SLSTR (L1 and L2)
    # - Common Metadata Repository (CMR): MODISA, MODIST, VIIRS, SeaWiFS, OCTS, CZCS (L2 and L3)
    # - L1/L2browser Ocean Color (requires 1s delay => slow): MODISA, MODIST, VIIRS, SeaWiFS, OCTS, CZCS (L0 and L1) / MERIS, HICO (all levels)
    # Note: if any query point dedicated to CMR is less than 60 days old, the entire query will be redirected to L1/L2browser (delay of storage on CMR)

    delta_today = datetime.today() - dates
    # if instrument == 'MSI' and level == 'L2A' and all(delta_today > timedelta(days=365)): # DEPRECATED
    #     raise ValueError(instrument + "level " + level + " supported only for online products on Copernicus (< 1 year old)") # DEPRECATED
    # elif instrument == 'MSI' and level == 'L2A': #instrument == 'OLCI' or  # DEPRECATED
    #     if instrument == 'MSI' and any(delta_today < timedelta(days=365)): # DEPRECATED
    #         print('Warning: query older than 12 month old will be ignored (offline products unavailable for bulk download)') # DEPRECATED
    #     access_platform = 'copernicus' # DEPRECATED
    #     password = getpass(prompt='Copernicus Password: ', stream=None) # DEPRECATED
    if instrument == 'MSI' or instrument == 'SLSTR' or instrument == 'OLCI':
        access_platform = 'creodias'
        password = getpass(prompt='Creodias Password: ', stream=None)
    elif level == 'L0' or level == 'L1A' or level == 'GEO' or instrument == 'MERIS' or instrument == 'HICO' or any(delta_today < timedelta(days=60)):
        access_platform = 'L1L2_browser'
        password = getpass(prompt='EarthData Password: ', stream=None)
    else:
        access_platform = 'cmr'
        password = getpass(prompt='EarthData Password: ', stream=None)
    return access_platform,password


def set_query_string(access_platform, instrument, level='L2', product='OC'):
    # Set query url specific to access plateform:
    image_names = list()
    # Get parameters to build query
    if instrument in INSTRUMENT_FILE_ID.keys():
        if access_platform == 'copernicus': # DEPRECATED
            # check which spatial resolution for OLCI, if not input choose lower resolution ERR
            if 'ERR' not in level and 'EFR' not in level and instrument == 'OLCI':
                level = level + '_EFR'
                timeliness = '%20AND%20timeliness:"Non%20Time%20Critical"'
            elif instrument != 'OLCI': # delete EFR and ERR if mistakenly input for other sensors
                level = level.replace('EFR','')
                level = level.replace('ERR','')
                level = level.replace('_','')
                timeliness = ''
            else:
                timeliness = ''
            dattyp = instrument + '_' + level

            if dattyp in DATA_TYPE_ID:
                sen = 'producttype:' + DATA_TYPE_ID[dattyp]
            else:
                raise ValueError("level " + level + " not supported for " + instrument + " sensor")
            query_string = sen + '%20AND%20' + 'instrumentshortname:' + instrument + timeliness + '%20AND%20'

        elif access_platform == 'creodias':
            # https://finder.creodias.eu/resto/api2/collections/Sentinel2/search.json?instrument=MSI&productType=L2A&processingLevel=LEVEL2A
            # check which spatial resolution for SLSTR and OLCI, if not input choose default:
            if 'L1' in level and 'ERR' not in level and 'EFR' not in level and instrument == 'OLCI':
                level = level + '_EFR'
            if 'L1' in level and 'RBT' not in level and instrument == 'SLSTR':
                level = level + '_RBT'
            if 'L2' in level and 'WFR' not in level and 'WRR' not in level and instrument == 'OLCI':
                level = level + '_WFR'
            if 'L2' in level and 'WST' not in level and 'WCT' not in level and instrument == 'SLSTR':
                level = level + '_WST'
            sat = INSTRUMENT_FILE_ID[instrument]
            dattyp = instrument + '_' + level
            if dattyp not in DATA_TYPE_ID:
                raise ValueError("level " + level + " not supported for " + instrument + " sensor")
            else:
                query_string = sat + '/search.json?instrument=' + INSTRUMENT_QUERY_ID[instrument] + '&productType=' + DATA_TYPE_ID[dattyp] + '&processingLevel=' + LEVEL_CREODIAS[level.split('_')[0]]

        elif access_platform == 'L1L2_browser':
            sen = '&sen=' + INSTRUMENT_QUERY_ID[instrument]
            sen_pre = INSTRUMENT_FILE_ID[instrument]
            if level == 'L2':
                # Level 2, need to specify product, adjust day|night
                sen_pos = level + '_' + DATA_TYPE_ID[instrument] + '_' + product + '.nc'
                if product in ['OC', 'IOP']:
                    dnm = 'D'
                    prm = 'CHL'
                elif product in ['SST']:
                    dnm = 'D@N'
                    prm = 'SST'
                elif product in ['SST4']:
                    dnm = 'N'
                    prm = 'SST4'
                else:
                    if verbose:
                        print('product not supported.')
                    return None
                sub = 'level1or2list'
            elif level in ['L0', 'L1A']:
                # Level 1A specify daily data only
                sen_pos = level + '_' + DATA_TYPE_ID[instrument] + EXTENSION_L1A[instrument]
                dnm = 'D'
                prm = 'TC'
                sub = 'level1or2list'
            # elif level == 'L3':
            # sub = 'level3'
            elif level in ['GEO']:
                sen_pos = 'GEO-M' + '_' + DATA_TYPE_ID[instrument] + '.nc'
                dnm = 'D'
                prm = 'TC'
                sub = 'level1or2list'
            else:
                raise ValueError("level not supported: '" + level + "'")
            query_string = '?sub=' + sub + sen + '&dnm=' + dnm + '&prm=' + prm

        elif access_platform == 'cmr':
            sen = SEARCH_CMR[instrument]
            query_string = '&short_name=' + sen + '_' + level + '_' + product

        return query_string

    else:
        raise ValueError("instrument not supported:'" + instrument + "'")


def format_dtlatlon_query(poi,access_platform):
    # Add some room using bounding box option (or default = 60 nautical miles) around the given location, and wrap longitude into [-180:180]
    if poi['lat'] + options.bounding_box_sz / 60 > 90:
        n = str(90)
    else:
        n = str(poi['lat'] + options.bounding_box_sz / 60)
    if poi['lat'] - options.bounding_box_sz / 60 < -90:
        s = str(-90)
    else:
        s = str(poi['lat'] - options.bounding_box_sz / 60)

    lon_box = options.bounding_box_sz / 60 / (math.cos(poi['lat'] * math.pi / 180))
    if poi['lon'] - lon_box < -180:
        w = str(poi['lon'] - lon_box + 360)
    else:
        w = str(poi['lon'] - lon_box)
    if poi['lon'] + lon_box > 180:
        e = str(poi['lon'] + lon_box - 360)
    else:
        e = str(poi['lon'] + lon_box)

    if access_platform == 'L1L2_browser':
        day = str((poi['dt'] - datetime(1970, 1, 1)).days)
        return w,s,e,n,day
    else:
        day_st = poi['dt'] - timedelta(hours=12, minutes=0)
        day_end = poi['dt'] + timedelta(hours=12, minutes=0)
        return w,s,e,n,day_st,day_end


def get_login_key(username, password): # get login key for creodias download
    login_data = {'client_id': 'CLOUDFERRO_PUBLIC','username': username,'password': password, 'grant_type': 'password'}
    login_key = requests.post(URL_CREODIAS_LOGIN, data=login_data).json()
    try:
        return login_key['access_token']
    except KeyError:
        raise RuntimeError('Unable to get login key. Response was ' + {login_key})


def get_image_list_copernicus(pois, access_platform, username, password, query_string, instrument, level='L1'): # DEPRECATED
    # Add column to points of interest data frame
    pois['image_names'] = [[] for _ in range(len(pois))]
    pois['url'] = [[] for _ in range(len(pois))]
    pois['prod_entity'] = [[] for _ in range(len(pois))] # only for copernicus, to check online status & metadata

    for i, poi in pois.iterrows():
        if verbose:
            print('[' + str(i + 1) + '/' + str(len(pois)) + ']   Querying ' + str(poi['id']) + ' ' +
                  instrument + ' ' + level + ' on Copernicus' + '    ' + str(poi['dt']) + '    ' + "%.5f" % poi['lat'] + '  ' + "%.5f" % poi['lon'])
        # get polygon around poi and date
        w,s,e,n,day_st,day_end = format_dtlatlon_query(poi, access_platform)
        # Build Query
        query = URL_COPERNICUS + query_string + 'beginposition:[' + day_st.strftime("%Y-%m-%dT%H:%M:%S.000Z") + '%20TO%20' + \
            day_end.strftime("%Y-%m-%dT%H:%M:%S.000Z") + ']%20AND%20' + 'footprint:"Intersects(POLYGON((' + w + '%20' + s + ',' + e + '%20' + s + \
            ',' + e + '%20' + n + ',' + w + '%20' + n + ',' + w + '%20' + s + ')))"&rows=100'
        r = requests.get(query, auth=HTTPBasicAuth(username, password))

        # r = s.get(url_dwld[i], auth=(username, password), stream=True, timeout=30)
        if i == 0 and 'Full authentication is required to access this resource' in r.text:
            raise Error('Unable to login to Copernicus, check username/password')
        # extract image name from response
        imlistraw = re.findall(r'<entry>\n<title>(.*?)</title>\n<', r.text)
        # extract url from response
        url_list = re.findall(r'\n<link href="(.*?)"/>\n<link rel="alternative"', r.text)
        # extract product meta data from response to check online status
        prod_meta = re.findall(r'\n<link rel="alternative" href="(.*?)"/>\n<link rel="icon"', r.text)
        # populate lists with image name and url
        pois.at[i, 'image_names'] = [s + '.zip' for s in imlistraw]
        pois.at[i, 'url'] = url_list
        pois.at[i, 'prod_entity'] = prod_meta

    return pois

def get_image_list_creodias(pois, access_platform, username, password, query_string, instrument, level='L1C'):
    # Add column to points of interest data frame
    pois['image_names'] = [[] for _ in range(len(pois))]
    pois['url'] = [[] for _ in range(len(pois))]
    pois['prod_entity'] = [[] for _ in range(len(pois))] # only for copernicus, to check online status & metadata

    for i, poi in pois.iterrows():
        if verbose:
            print('[' + str(i + 1) + '/' + str(len(pois)) + ']   Querying ' + str(poi['id']) + ' ' +
                  instrument + ' ' + level + ' on Creodias' + '    ' + str(poi['dt']) + '    ' + "%.5f" % poi['lat'] + '  ' + "%.5f" % poi['lon'])
        # get polygon around poi and date
        w,s,e,n,day_st,day_end = format_dtlatlon_query(poi, access_platform)
        # Build Query
        query = URL_SEARCH_CREODIAS + query_string + '&startDate=' + day_st.strftime("%Y-%m-%d") + \
            '&completionDate=' + day_end.strftime("%Y-%m-%d") + '&box=' + w + ',' + s + ',' + e + ',' + n
        r = requests.get(query)
        # extract image name from response
        imlistraw = re.findall(r'"parentIdentifier":null,"title":"(.*?)","description"', r.text)
        # extract url from response
        fid_list = re.findall(r'"download":{"url":"https:\\/\\/zipper.creodias.eu\\/download\\/(.*?)","mimeType"', r.text)

        # populate lists with image name and url
        pois.at[i, 'image_names'] = [sub.replace('.SAFE', '') + '.zip' for sub in imlistraw]
        # pois.at[i, 'image_names'] = imlistraw
        pois.at[i, 'url'] = [URL_CREODIAS_GET_FILE + '/' + s + '?token=' for s in fid_list]

    return pois


def get_image_list_l12browser(pois, access_platform, query_string, instrument, level='L2', product='OC', query_delay=1):
    # Add column to points of interest data frame
    pois['image_names'] = [[] for _ in range(len(pois))]
    pois['url'] = [[] for _ in range(len(pois))]
    pois['prod_entity'] = [[] for _ in range(len(pois))] # only for copernicus, to check online status & metadata

    for i, poi in pois.iterrows():
        if verbose:
            print('[' + str(i + 1) + '/' + str(len(pois)) + ']   Querying ' + str(poi['id']) + ' ' +
                  instrument + ' ' + level + ' on L1L2_browser' + '    ' + str(poi['dt']) + '    ' + "%.5f" % poi['lat'] + '  ' + "%.5f" % poi['lon'])
        # get polygon around poi and date
        w,s,e,n,day = format_dtlatlon_query(poi, access_platform)
        # Build Query
        query = URL_L12BROWSER + query_string + '&per=DAY&day=' + day + '&n=' + n + '&s=' + s + '&w=' + w + '&e=' + e
        r = requests.get(query)
        # extract image name from response
        if 'href="https://oceandata.sci.gsfc.nasa.gov/ob/getfile/' in r.text: # if one image
            imlistraw = re.findall(r'href="https://oceandata.sci.gsfc.nasa.gov/ob/getfile/(.*?)">', r.text)
            imlistraw = [ x for x in imlistraw if level in x ]
        else: # if multiple images
            imlistraw = re.findall(r'title="(.*?)"\nwidth="70"', r.text)
            if instrument == 'MODIS-Aqua' or instrument == 'MODIS-Terra' and level == 'L1A':
                # add missing extension when multiple reuslts
                imlistraw = [s + '.bz2' for s in imlistraw]
                # remove duplicates
                imlistraw = list(dict.fromkeys(imlistraw))

        # append VIIRS GEO file names at the end of the list
        if instrument == 'VIIRS' and level == 'L1A':
            imlistraw = imlistraw + [sub.replace('L1A', 'GEO-M') for sub in imlistraw]

        # Delay next query (might get kicked by server otherwise)
        sleep(query_delay)

        # populate lists with image name and url
        pois.at[i, 'image_names'] = imlistraw

        # populate url list
        pois.at[i, 'url'] = [URL_GET_FILE_CGI + s for s in pois.at[i, 'image_names']]

    return pois


def get_image_list_cmr(pois, access_platform, query_string, instrument, level='L2', product='OC'):
    # Add column to points of interest data frame
    pois['image_names'] = [[] for _ in range(len(pois))]
    pois['url'] = [[] for _ in range(len(pois))]
    pois['prod_entity'] = [[] for _ in range(len(pois))] # only for copernicus, to check online status & metadata

    for i, poi in pois.iterrows():
        if verbose:
            print('[' + str(i + 1) + '/' + str(len(pois)) + ']   Querying ' + str(poi['id']) + ' ' +
                  instrument + ' ' + level + ' on CMR' + '    ' + str(poi['dt']) + '    ' + "%.5f" % poi['lat'] + '  ' + "%.5f" % poi['lon'])
        # get polygon around poi and date
        w,s,e,n,day_st,day_end = format_dtlatlon_query(poi, access_platform)
        # Build Query
        query = URL_CMR + query_string + '&bounding_box=' + w + ',' +  s + ',' + e + ',' + n + \
                '&temporal=' + day_st.strftime("%Y-%m-%dT%H:%M:%SZ,") + day_end.strftime("%Y-%m-%dT%H:%M:%SZ") + '&page_size=2000&page_num=1'
        r = requests.get(query)
        # extract image name from response
        imlistraw = re.findall(r'https://oceandata.sci.gsfc.nasa.gov/cmr/getfile/(.*?)"},', r.text)
        # Reformat VIIRS image name
        if instrument == 'VIIRS' and product == 'SST':
            imlistraw = [ x for x in imlistraw if "SNPP_VIIRS." in x ]

        # populate lists with image name and url
        pois.at[i, 'image_names'] = imlistraw
        pois.at[i, 'url'] = [URL_GET_FILE_CMR + s for s in imlistraw]

    return pois


def request_platform(s, image_names, url_dwld, access_platform, username, password, login_key):
    if access_platform == 'copernicus': # DEPRECATED
        login_key = None
        headers = {'Range':'bytes=' + str(os.stat(image_names).st_size) + '-'}
        r = s.get(url_dwld, auth=(username, password), stream=True, timeout=900, headers=headers)
        if r.status_code != 200 and r.status_code != 206:
            if 'offline products retrieval quota exceeded' in r.text:
                print('Unable to download from https://scihub.copernicus.eu/\n'
                  '\t- User offline products retrieval quota exceeded (1 fetch max)')
                return None
            else:
                print(r.status_code)
                print(r.text)
                print('Unable to download from https://scihub.copernicus.eu/\n'
                  '\t- Check login/username\n'
                  '\t- Invalid image name?')
        return r,login_key
    elif access_platform == 'creodias':
        headers = {'Range':'bytes=' + str(os.stat(image_names).st_size) + '-'}
        r = s.get(url_dwld + login_key, stream=True, timeout=900, headers=headers)
        if r.status_code != 200 and r.status_code != 206:
            if r.text == 'Expired signature!':
                print('Login expired, reconnection ...')
                # get login key to include it into url
                login_key = get_login_key(username, password)
                r = s.get(url_dwld + login_key, stream=True, timeout=900, headers=headers)
            else:
                print(r.status_code)
                print(r.text)
                print('Unable to download from https://auth.creodias.eu/\n'
                  '\t- Check login/username\n'
                  '\t- Invalid image name?')
        return r,login_key
    else:
        # modify header to hide requests query and mimic web browser
        headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/68.0.3440.106 Safari/537.36',}
        login_key = None
        s.auth = (username, password)
        # headers = {'Range':'bytes=' + str(os.stat(image_names).st_size) + '-'}
        r1 = s.request('get', url_dwld)
        r = s.get(r1.url, auth=(username, password), stream=True, timeout=900, headers=headers)
        return r,login_key

# def chunk_download(image_names, r):
#     handle = open(image_names, "wb")
#     for chunk in r.iter_content(chunk_size=512):
#         if chunk:
#             handle.write(chunk)
#     handle.close()
#     return None


def login_download(image_names, url_dwld, instrument, access_platform, username, password):
    # Login to Earth Data and Download image
    if url_dwld is None and image_names is None:
        if verbose:
            print('No image to download.')
        return None
    if access_platform == 'creodias':
        # get login key to include it into url
        login_key = get_login_key(username, password)
    else:
        login_key = None
    for i in range(len(url_dwld)):
        if os.path.isfile(image_names[i]):
            if verbose:
                print('Skip ' + image_names[i])
        else:
            MAX_RETRIES = 3
            WAIT_SECONDS = 30
            for j in range(MAX_RETRIES):
                try:
                    # Open session
                    with requests.Session() as s:
                        handle = open(image_names[i], "wb")
                        r,login_key = request_platform(s, image_names[i], url_dwld[i], access_platform, username, password, login_key)
                        r.raise_for_status()
                        if access_platform == 'creodias':
                            expected_length = int(r.headers.get('Content-Length'))
                            while os.stat(image_names[i]).st_size < expected_length: # complete the file even if connection is cut while downloading and file is incomplete
                                print('Downloading ' + image_names[i] + ' 0%')
                                r,login_key = request_platform(s, image_names[i], url_dwld[i], access_platform, username, password, login_key)
                                r.raise_for_status()
                                trump_shutup = 0
                                with open(image_names[i], "ab") as handle:
                                    for chunk in r.iter_content(chunk_size=16*1024):
                                        if chunk:
                                            handle.write(chunk)
                                            if verbose:
                                                biden_president = round(float(os.stat(image_names[i]).st_size)/expected_length*100)
                                                if biden_president > trump_shutup:
                                                    sys.stdout.write('\rDownloading ' + image_names[i] + '      ' + str(biden_president) + '%')
                                                    trump_shutup = biden_president
                                if handle.closed:
                                    handle = open(image_names[i], "ab")
                                handle.flush()
                            if os.stat(image_names[i]).st_size < expected_length:
                                raise IOError('incomplete read ({} bytes read, {} more expected)'.format(actual_length, expected_length - actual_length))
                            handle.close()
                            print()
                            break
                        else:
                            if verbose:
                                print('Downloading ' + image_names[i])
                            with open(image_names[i], "ab") as handle:
                                for chunk in r.iter_content(chunk_size=16*1024):
                                    if chunk:
                                        handle.write(chunk)
                            handle.close()
                            break
                        # handle = open(image_names[i], "wb")
                        # for chunk in r.iter_content(chunk_size=512):
                        #     if chunk:
                        #         handle.write(chunk)
                        # handle.close()
                        # break

                        # if r.ok:
                        #     if verbose:
                        #         print('Downloading ' + image_names[i])
                        #     handle = open(image_names[i], "wb")
                        #     for chunk in r.iter_content(chunk_size=512):
                        #         if chunk:
                        #             handle.write(chunk)
                        #     handle.close()
                        #     break
                        # else:
                        #     print('Unable to download from EarthData.\n'
                        #       '\t- Did you accept the End User License Agreement for this dataset ?\n'
                        #       '\t- Check login/username\n'
                        #       '\t- Invalid image name?')
                        #     return None
                except requests.exceptions.HTTPError as e:
                    # Whoops it wasn't a 200
                    print('Requests error: ' + str(e) + '.\n'
                      '\tAttempt [' + str(j+2) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                # except TimeoutError:
                #     print('Chunk download timeout, attempt [' + str(j) + '/' + MAX_RETRIES + 'reconnection ...')
                    handle.close()
                except requests.exceptions.ConnectionError:
                    print('Build https connection failed: download failed, attempt [' + str(j+2) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                    handle.close()
                except requests.exceptions.ProxyError:
                    print('Proxy error: download failed, attempt [' + str(j+2) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                    handle.close()
                except requests.exceptions.SSLError:
                    print('SSL error: download failed, attempt [' + str(j+2) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                    handle.close()
                except requests.exceptions.Timeout:
                    print('Request timed out: download failed, attempt [' + str(j+2) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                    handle.close()
                except requests.exceptions.ReadTimeout:
                    print('Read timed out: download failed, attempt [' + str(j+2) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                    handle.close()
                except requests.exceptions.ConnectTimeout:
                    print('Connection timed out: download failed, attempt [' + str(j+2) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                    handle.close()
                except requests.exceptions.RequestException:
                    print('Unknown error: download failed, attempt [' + str(j+2) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                    handle.close()
                except requests.exceptions.InvalidURL:
                    print('URL not valid: download failed, attempt [' + str(j+2) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                    handle.close()
                except requests.exceptions.ChunkedEncodingError:
                    print('The server declared chunked encoding but sent an invalid chunk: download failed, attempt [' + str(j) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                    handle.close()
                except socket.timeout:
                    print('Connetion lost: download failed, attempt [' + str(j+2) + '/' + str(MAX_RETRIES) + '] reconnection ...')
                    handle.close()
                if j+2 == MAX_RETRIES:
                    return None
                sleep(WAIT_SECONDS)
            else:
                print('%d All connection attempts failed, download aborted.\n'
                    '\t- Did you accept the End User License Agreement for this dataset ?\n'
                    '\t- Check login/username.\n'
                    '\t- Check image name/url in *.csv file\n'
                    '\t- Check for connection problems \n' # for Earthdata download check https://oceancolor.gsfc.nasa.gov/forum/oceancolor/topic_show.pl?tid=6447
                    '\t- Check for blocked IP \n') # (for Earthdata download connection_problems@oceancolor.gsfc.nasa.gov)
                return None


if __name__ == "__main__":
    from optparse import OptionParser

    parser = OptionParser(usage="Usage: getOC.py [options] [filename]", version="getOC " + __version__)
    parser.add_option("-i", "--instrument", action="store", dest="instrument",
                      help="specify instrument, available options are: VIIRS, MODIS-Aqua, MODIS-Terra, OCTS, CZCS, MERIS, HICO, "
                      "OLCI (L1 only), SLSTR (L1 only), MSI (L1C and L2A < 12 month) and SeaWiFS (L3 only)")
    parser.add_option("-l", "--level", action="store", dest="level", default='L2',
                      help="specify processing level, available options are: GEO, L1A, L1C (MSI only), L2A (MSI only), L2, "
                      "L3BIN, and L3SMI, append '_ERR' to level for lower OLCI resoltion or '_EFR' for full resoltuion")
    # Level 2 specific option
    parser.add_option("-p", "--product", action="store", dest="product", default='OC',
                      help="specify product identifier (only for L2), available options are: OC, SST, and IOP, "
                      "not available for Copernicus (OLCI, SLSTR and MSI) queries")
    parser.add_option("-d", "--delay", action="store", dest="query_delay", type='float', default=1,
                      help="Delay between queries only needed to query L1L2_browser")
    # Level 3 specific options
    parser.add_option("-s", "--start-period", action="store", dest="start_period",
                      help="specify start period date (only for L3), yyyymmdd")
    parser.add_option("-e", "--end-period", action="store", dest="end_period",
                      help="specify end period date (only for L3), yyyymmdd")
    parser.add_option("-b", "--binning-period", action="store", dest="binning_period", default='8D',
                      help="specify binning period (only for L3), available options are: DAY, 8D, MO, and YR")
    parser.add_option("-g", "--geophysical-parameter", action="store", dest="geophysical_parameter", default='GSM',
                      help="specify geophysical parameter (only for L3), available options are for L3BIN: "
                           "CHL, GSM, IOP, KD490, PAR, PIC, POC, QAA, RRS, and ZLEE "
                           "MODIS also accept SST, SST4, and NSST;"
                           "example of options for L3SMI are:"
                           "CHL_chl_ocx_4km, CHL_chlor_a_4km, GSM_bbp_443_gsm_9km,"
                           "GSM_chl_gsm_9km, IOP_bb_678_giop_9km, KD490_Kd_490_9km")
    # credential specific options
    parser.add_option("-u", "--username", action="store", dest="username", default=None,
                      help="specify username to login to Copernicus (OLCI / SLSTR), Creodias (MSI) or EarthData (any other plateform")
    # Other options
    parser.add_option("-w", "--write-image-links", action="store_true", dest="write_image_links", default=False,
                      help="Write query results image names and corresponding url into csv file.")
    parser.add_option("-r", "--read-image-list", action="store_true", dest="read_image_list", default=False,
                      help="Read previous query from csv file")
    parser.add_option("-q", "--quiet", action="store_false", dest="verbose", default=True)
    parser.add_option("--box", "--bounding-box-size", action="store", dest="bounding_box_sz", type='float', default=60,
                      help="specify bounding box size in nautical miles")
    (options, args) = parser.parse_args()


    verbose = options.verbose
    if options.instrument is None:
        print(parser.usage)
        print('getOC.py: error: option -i, --instrument is required')
        sys.exit(-1)
    if 'L3' not in options.level and options.username is None:
        print(parser.usage)
        print('getOC.py: error: option -u, --username is required')
        sys.exit(-1)

    if len(args) < 1 and options.level not in ['L3BIN', 'L3SMI']:
        print(parser.usage)
        print('getOC.py: error: argument filename is required for Level GEO, L1A, or L2')
        sys.exit(-1)
    elif len(args) > 2:
        print(parser.usage)
        print('getOC.py: error: too many arguments')
        sys.exit(-1)


    image_names = list()
    # Get list of images to download
    if options.read_image_list:
        if os.path.isfile(os.path.splitext(args[0])[0] + '_' + options.instrument + '_' +
                                  options.level + '_' + options.product + '.csv'):
            pois = read_csv(os.path.splitext(args[0])[0] + '_' + options.instrument + '_' +
                                      options.level + '_' + options.product + '.csv',
                                      names=['id', 'dt', 'lat', 'lon', 'image_names', 'url', 'prod_entity'], parse_dates=[1])
            pois.dropna(subset=['image_names'], axis=0, inplace= True)
            points_of_interest = pois.copy()

            access_platform,password = get_platform(points_of_interest['dt'], options.instrument, options.level)

            # Parse image_names and url
            image_names = list()
            url_dwld = list()
            for index, record in pois.iterrows():
                # Convert 'stringified' list to list
                imli = record['image_names'].split(';')
                urli = record['url'].split(';')
                for im in range(len(imli)):
                    image_names.append(imli[im])
                    url_dwld.append(urli[im])
        else:
            if verbose:
                print('IOError: [Errno 2] File ' + os.path.splitext(args[0])[0] + '_' + options.instrument + '_' +
                                  options.level + '_' + options.product + '.csv' + ' does not exist, select option -w (write) instead of -r (read)')
            sys.exit(0)
    else:
        # Parse csv file containing points of interest
        points_of_interest = read_csv(args[0], names=['id', 'dt', 'lat', 'lon'], parse_dates=[1])

        access_platform,password = get_platform(points_of_interest['dt'], options.instrument, options.level)
        query_string = set_query_string(access_platform, options.instrument, options.level, options.product)

        # if access_platform == 'copernicus': # DEPRECATED
        #     pois = get_image_list_copernicus(points_of_interest, access_platform, options.username, password,
        #                     query_string, options.instrument, options.level)
        if access_platform == 'creodias':
            pois = get_image_list_creodias(points_of_interest, access_platform, options.username, password,
                            query_string, options.instrument, options.level)
        elif access_platform == 'L1L2_browser':
            pois = get_image_list_l12browser(points_of_interest, access_platform, query_string, options.instrument,
                            options.level, options.product, options.query_delay)
        elif access_platform == 'cmr':
            pois = get_image_list_cmr(points_of_interest, access_platform, query_string, options.instrument,
                            options.level, options.product)

        points_of_interest = pois.copy()
        # parse image_names
        image_names = list()
        url_dwld = list()
        prod_meta = list()
        for _, pois in pois.iterrows():
            image_names.extend(pois['image_names'])
            url_dwld.extend(pois['url'])
            prod_meta.extend(pois['prod_entity'])

    # Write image names
    if options.write_image_links:
        # Reformat image names & url
        for i, poi in points_of_interest.iterrows():
            points_of_interest.at[i, 'image_names'] = ';'.join(poi['image_names'])
            points_of_interest.at[i, 'url'] = ';'.join(poi['url'])
            points_of_interest.at[i, 'prod_entity'] = ';'.join(poi['prod_entity'])
        points_of_interest.to_csv(os.path.splitext(args[0])[0] + '_' + options.instrument + '_' +
                                  options.level + '_' + options.product + '.csv',
                                  date_format='%Y/%m/%d %H:%M:%S', header=False, index=False, float_format='%.5f')

    # Download images from url list
    login_download(image_names, url_dwld, options.instrument, access_platform, options.username, password)

    print('Download completed')



def get_poi_from_nc(filename, dtoi, latoi, lonoi, delta_dt=3, roi=2.5, l2_oc_flags=786, geophysical_variables=None):
    # filename: path to image (netCDF)
    # dtoi: date & time of interest
    # latoi: latitude of interest
    # lonoi: longitude of interest
    # delta_dt: maximum delay between image and dt of interest in hours
    # roi: region of interest in km
    # l2_oc_flags: level 2 Ocean Color flags used to reject data (default: 786 = L2 Default Mask ON)
    # geophysical_varibles: specify geophysical variables to extract (None means all of them)

    print(filename)

    with Dataset(filename, 'r') as fid:

        # Print information regarding image
        # print(fid)

        # Get Relevant metadata
        # fid.instrument           # MODIS
        # fid.platform             # Aqua
        # fid.title                # HMODISA Level-2 Data
        # fid.processing_version   # 2014.0.1QL
        # fid.id                   # 2014.0.1QL/L2/A2017321232500.L2_LAC_OC.nc
        # fid.identifier_product_doi_authority  # http://dx.doi.org
        # fid.identifier_product_doi            #10.5067/AQUA/MODIS_OC.2014.0

        # Get start and end date
        sdt = datetime.strptime(fid.time_coverage_start, '%Y-%m-%dT%H:%M:%S.%fZ')
        edt = datetime.strptime(fid.time_coverage_end, '%Y-%m-%dT%H:%M:%S.%fZ')
        # dt = sdt + (edt - sdt) / 2

        # Check if image match time of interest
        if abs((dtoi - sdt).total_seconds() / 3600) > delta_dt and \
                abs((dtoi - edt).total_seconds() / 3600) > delta_dt:
            # print('Outside time of interest.')
            return None

        # Get boundaries
        nlat = fid.northernmost_latitude
        slat = fid.southernmost_latitude
        elon = fid.easternmost_longitude
        wlon = fid.westernmost_longitude
        # Check if region of interest is within the boundaries
        if not (slat <= latoi <= nlat and
                (wlon <= elon and wlon <= lonoi <= elon) or
                (wlon > elon and wrap_to_360(wlon) <= wrap_to_360(lonoi) <= wrap_to_360(elon))):  # special case at 180th meridian
            # print('Outside of location of interest.')
            return None

        # Extract flags and position from netcdf (data type is numpy.ndarray)
        plat = fid.groups['navigation_data'].variables['latitude'][:]
        plon = fid.groups['navigation_data'].variables['longitude'][:]
        pflags = fid.groups['geophysical_data'].variables['l2_flags'][:]

        # Select data of interest
        # init selection matrix
        sel = np.zeros(pflags.shape, dtype=bool)
        # Select data with no user selected flags on
        # this means that pflags and l2_oc_flags have no bit in common => no flag triggered
        sel[pflags & l2_oc_flags == 0] = True

        # Compute distance between pixels and point of interest
        pd = geo_distance(plat, plon, latoi, lonoi)
        # Update selection to area of interest
        sel[sel & (pd > roi)] = False

        # Check if some data is left
        n_sel = sel.sum()
        if n_sel == 0:
            # print('No data.')
            return None

        if geophysical_variables is None:
            geophysical_variables = fid.groups['geophysical_data'].variables
        geophysical_data_to_skip = ['l2_flags']
        poi = {'sdt': sdt, 'edt': edt}
        found_data = False
        for key in geophysical_variables:
            if key in geophysical_data_to_skip:
                continue
            data = fid.groups['geophysical_data'].variables[key][:]
            # Use numpy mask to ignore _FillValue due to pixels that could not be processed
            #   do not use np.median as it add _fillValue(-32767) to the average.
            n_mask = sum(~data[sel].mask)
            if n_mask == 0:
                # All data is masked (with NumPy mask)
                poi[key] = np.NaN
                poi[key + '_sd'] = np.NaN
                poi[key + '_n'] = 0
            else:
                if n_mask != n_sel:
                    print('WARNING: ' + key + ' Additional data was flagged by l2gen.')
                found_data = True
                # Compute median and standard error
                poi[key] = np.ma.median(data[sel])  # np.ma.median
                poi[key + '_sd'] = np.ma.std(data[sel])# / sqrt(n_mask)  # np.ma.std
                poi[key + '_n'] = n_mask

        if found_data:
            return poi
        else:
            # print('No data.')
            return None


def extract_matchups(path_to_poi, path_to_data, path_to_output=None, delta_dt=3, roi=2.5, l2_oc_flags=786, geophysical_variables=None, nc_dt_fmt='%Y%j', data_ext='.nc'):
    # Parse csv file
    # pois = DataFrame([], columns=['id', 'dt', 'lat', 'lon'])
    # with open(path_to_poi) as fid:
    #     for l in csv.reader(fid, delimiter=','):
    #         pois = pois.append({'id': l[0], 'dt':datetime.strptime(l[1], '%Y/%m/%d %H:%M:%S'),
    #                             'lat':float(l[2]), 'lon':float(l[3])}, ignore_index=True)
    pois = read_csv(path_to_poi, names=['id', 'dt', 'lat', 'lon'], parse_dates=[1])

    # List nc images
    images = [s for s in os.listdir(path_to_data) if s.endswith(data_ext)]

    # For each poi average good data
    init_matchups = True
    for i, poi in pois.iterrows():
        # Get short list of images matching +/- 1 day
        short_list = [s for s in images if
               poi['dt'].strftime(nc_dt_fmt) in s or (poi['dt'] + timedelta(days=1)).strftime(nc_dt_fmt) in s or (
                              poi['dt'] - timedelta(days=1)).strftime(nc_dt_fmt) in s]

        for image in short_list:
            # Get average data for each geophysical parameter
            data = get_poi_from_nc(os.path.join(path_to_data, image), poi['dt'], poi['lat'], poi['lon'],
                                   delta_dt=delta_dt, roi=roi, l2_oc_flags=l2_oc_flags,
                                   geophysical_variables=geophysical_variables)
            # Add to Data frame
            if data is not None:
                if init_matchups:
                    init_matchups = False
                    # Re-arrange variable order (for nice export to csv file)
                    data_variables = [e for e in data.keys() if e not in ['sdt', 'edt']]
                    data_variables_n = sorted_nicely([e for e in data_variables if '_n' in e])
                    data_variables_sd = sorted_nicely([e for e in data_variables if '_sd' in e])
                    data_variables_avg = sorted_nicely([e for e in data_variables if e not in data_variables_n and e not in data_variables_sd])
                    matchups = DataFrame(dict(poi.to_dict(), **data), index=[0],
                                         columns=['id', 'dt', 'lat', 'lon', 'sdt', 'edt'] +
                                         data_variables_avg + data_variables_sd + data_variables_n)
                else:
                    matchups = matchups.append(dict(poi.to_dict(), **data), ignore_index=True)

    # Write output for each sensor
    if path_to_output is not None:
        matchups.to_csv(path_to_output, float_format='%.5f')  # %g

    return matchups


if __name__ == "__main__":
    extract_matchups(PATH_TO_POI, PATH_TO_DATA, PATH_TO_OUTPUT, delta_dt=MAX_DELAY, roi=RADIUS, data_ext=DATA_EXT, nc_dt_fmt=DT_FORMAT)
