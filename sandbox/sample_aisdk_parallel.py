# -*- coding: utf-8 -*-

"""
***************************************************************************
    sample_aisdk_parallel.py
    ---------------------
    Date                 : December 2018
    Copyright            : (C) 2018 by Anita Graser
    Email                : anitagraser@gmx.at
***************************************************************************
*                                                                         *
*   This program is free software; you can redistribute it and/or modify  *
*   it under the terms of the GNU General Public License as published by  *
*   the Free Software Foundation; either version 2 of the License, or     *
*   (at your option) any later version.                                   *
*                                                                         *
***************************************************************************
"""

import os
import sys 
import fiona
import pickle
import pandas as pd 
import multiprocessing as mp
from geopandas import GeoDataFrame
from shapely.geometry import Point
from datetime import timedelta, datetime
from itertools import repeat
from random import shuffle

import warnings
warnings.filterwarnings('ignore')

sys.path.append(os.path.join(os.path.dirname(__file__),".."))

from trajectory import Trajectory 
from trajectory_sampler import TrajectorySampler


pd.set_option('display.max_colwidth', -1)

XMIN, YMIN, XMAX, YMAX = 9.5, 56.6, 13, 58.7
FILTER_BY_SHIPTYPE = True
SHIPTYPE = 'Passenger'
DESIRED_NO_SAMPLES = 10
PAST_MINUTES = [1,3,5]
FUTURE_MINUTES = [1,5,10,15,20]
FUTURE_TRAJ_DURATION = timedelta(hours=1)

DATA_PATH = "E:/Geodata/AISDK/raw_ais" # '/media/agraser/Elements/AIS_DK/2018/aisdk_20180101.csv' # 
TEMP_EXTRACT = 'E:/Geodata/AISDK/extract.csv' # '/home/agraser/tmp/extract.csv' # 
GRID = 'E:/Geodata/AISDK/grid40.gpkg' # '/home/agraser/tmp/grid.gpkg' #
OUTPUT = 'E:/Geodata/AISDK/sample.csv' # '/home/agraser/tmp/sample.csv' # 

if FILTER_BY_SHIPTYPE:
    TEMP_INTERSECTIONS = 'E:/Geodata/AISDK/intersections_{}.pickle'.format(SHIPTYPE)
    #TEMP_INTERSECTIONS = '/home/agraser/tmp/intersections_{}.pickle'.format(SHIPTYPE)
else: 
    TEMP_INTERSECTIONS = 'E:/Geodata/AISDK/intersections.pickle'
    #TEMP_INTERSECTIONS = '/home/agraser/tmp/intersections.pickle'
    

def intersection_worker(feature, trajectories):
    print("Initializing intersection worker ...")
    result = []
    for traj in trajectories:
        if len(result) >= 5 * DESIRED_NO_SAMPLES:  # used to run out of memory without this :(
            break
        for intersection in traj.intersection(feature):
            print(len(result))
            if len(result) >= 5*DESIRED_NO_SAMPLES: # used to run out of memory without this :(
                break
            intersection.context = feature['id']
            result.append(intersection)
    print("Finished intersection worker ({} intersections)!".format(len(result)))
    return feature, result

def sampling_worker(trajectories, past, future):
    min_starting_speed_ms = 1
    past_timedelta = timedelta(minutes=past)
    future_timedelta = timedelta(minutes=future)
    samples = []
    counter = 0
    shuffle(trajectories)
    #print("Got {} trajectories!".format(len(trajectories)))
    for traj in trajectories:
        if counter >= DESIRED_NO_SAMPLES:
            break
        sampler = TrajectorySampler(traj, timedelta(seconds=10))
        try:
            sample = sampler.get_sample(past_timedelta, future_timedelta, min_starting_speed_ms, True, FUTURE_TRAJ_DURATION)
            samples.append(sample)
            counter +=1
            #print(traj.id)
        except RuntimeError as e:
            pass #print(e)
    print("Got {} trajectories, extracted {} samples!".format(len(trajectories),len(samples)))
    return samples   

def filter_df_by_bbox(df, XMIN, XMAX, YMIN, YMAX):
    df = df[df['Latitude'] > YMIN]
    df = df[df['Latitude'] < YMAX]
    df = df[df['Longitude'] > XMIN]
    df = df[df['Longitude'] < XMAX]
    df = df[df['SOG'] > 1]
    return df
    
def create_trajectories(df):
    print("Creating time index ...")
    df['# Timestamp'] = pd.to_datetime(df['# Timestamp'], format='%m/%d/%Y %H:%M:%S')
    df = df.set_index('# Timestamp')
    
    print("Creating geometries ...")
    geometry = [Point(xy) for xy in zip(df.Longitude, df.Latitude)]
    df = GeoDataFrame(df, geometry=geometry, crs={'init': '4326'})
        
    print("Creating trajectories ...")
    trajectories = []
    for key, values in df.groupby(['MMSI']):
        try:
            for t in Trajectory(key, values).split():
                trajectories.append(t)
        except ValueError:
            print("Failed to create trajectory!")

    print("Created {} trajectories!".format(len(trajectories)))
    shuffle(trajectories)
    return trajectories
    
def compute_intersections(trajectories, polygon_file, pool):
    print("Computing intersections for future use (this can take a while!) ...")
    results = {}
    for cell, intersections in pool.starmap(intersection_worker, zip(polygon_file, repeat(trajectories))):
        results[cell['id']] = intersections
    return results

        
def prepare_data(pool):
    try:
        print("Loading filtered data from {} ...".format(TEMP_EXTRACT))
        df = pd.read_csv(TEMP_EXTRACT)
    except:
        print("Failed to load filtered data from {}!".format(TEMP_EXTRACT))
        print("Extracting data based on bbox {} ...".format([XMIN, XMAX, YMIN, YMAX]))
        dfs = []
        for filename in os.listdir(DATA_PATH):
            if filename.endswith(".csv"):
                print("Processing {} ...".format(filename))
                # Timestamp,Type of mobile,MMSI,Latitude,Longitude,Navigational status,ROT,SOG,COG,Heading,IMO,Callsign,Name,Ship type,Cargo type,Width,Length,Type of position fixing device,Draught,Destination,ETA,Data source type
                df = pd.read_csv(os.path.join(DATA_PATH,filename), usecols=['# Timestamp','MMSI','SOG','Ship type','Latitude','Longitude'])
                df = filter_df_by_bbox(df, XMIN, XMAX, YMIN, YMAX)
                dfs.append(df)
        df = pd.concat(dfs)  
        df.to_csv(TEMP_EXTRACT, index = False)
        
    if FILTER_BY_SHIPTYPE:
        print("Filtering: Only {} vessels ...".format(SHIPTYPE))
        df = df[df['Ship type'] == SHIPTYPE]        
    
    trajectories = create_trajectories(df)
    polygon_file = fiona.open(GRID, 'r')
    intersections_per_grid_cell = compute_intersections(trajectories, polygon_file, pool)            
    polygon_file.close()
    
    print("Writing intersections to {} ...".format(TEMP_INTERSECTIONS))
    with open(TEMP_INTERSECTIONS, 'wb') as output: 
        pickle.dump(intersections_per_grid_cell, output)

    return intersections_per_grid_cell
    
def create_sample(intersections_per_grid_cell, past, future, pool):
    all_samples = []
    with open(OUTPUT.replace('sample.csv','sample_{}_{}_{}.csv'.format(SHIPTYPE, past, future)), 'w') as output:
        output.write("id;start_secs;past_secs;future_secs;past_traj;future_pos;future_traj\n")
        jobs = zip(intersections_per_grid_cell.values(), repeat(past), repeat(future))
        for samples in pool.starmap(sampling_worker, jobs):
            for sample in samples:
                sample.id = len(all_samples)
                all_samples.append(sample)
                try:
                    output.write(str(sample))
                except:
                    pass
                output.write('\n')   
    output.close()    
    with open(OUTPUT.replace('sample.csv','sample_{}_{}_{}.pickle'.format(SHIPTYPE, past, future)), 'wb') as output:
        pickle.dump(all_samples, output)   
    output.close()    

if __name__ == '__main__':   
    print("{} Started! ...".format(datetime.now()))
    script_start = datetime.now()   
    pool = mp.Pool(3) # running out of memory :(
    
    try:
        print("Loading pickled data from {} ...".format(TEMP_INTERSECTIONS))
        with open(TEMP_INTERSECTIONS, 'rb') as f:
            intersections_per_grid_cell = pickle.load(f)
    except:
        print("Failed to load pickled data from {}!".format(TEMP_INTERSECTIONS))
        print("Preparing data ...")
        intersections_per_grid_cell = prepare_data(pool)
    
    pool = mp.Pool(mp.cpu_count()-1)
    
    for past in PAST_MINUTES:
        for future in FUTURE_MINUTES:
            print("Extracting samples ({},{}) ...".format(past, future))
            create_sample(intersections_per_grid_cell, past, future, pool)
    
    print("{} Finished! ...".format(datetime.now()))
    print("Runtime: {}".format(datetime.now()-script_start))
    
    