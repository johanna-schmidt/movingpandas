# -*- coding: utf-8 -*-

"""
***************************************************************************
    overlay.py
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

#import os
#import sys
import pandas as pd
from geopandas import GeoDataFrame
from shapely.geometry import Point, LineString, shape
from shapely.affinity import translate
from datetime import timedelta

#sys.path.append(os.path.dirname(__file__))


def _connect_points(row):
    pt0 = row['prev_pt']
    pt1 = row['geometry']
    if type(pt0) != Point:
        return None
    if pt0 == pt1:
        # to avoid intersection issues with zero length lines
        pt1 = translate(pt1, 0.00000001, 0.00000001)
    return LineString(list(pt0.coords) + list(pt1.coords))

def _to_line_df(traj):
    line_df = traj.df.copy()
    line_df['prev_pt'] = line_df['geometry'].shift()
    line_df['t'] = traj.df.index
    line_df['prev_t'] = line_df['t'].shift()
    line_df['line'] = line_df.apply(_connect_points, axis=1)
    return line_df.set_geometry('line')[1:]

def _get_spatiotemporal_ref(row):
    #print(type(row['geo_intersection']))
    if type(row['geo_intersection']) == LineString:
        pt0 = Point(row['geo_intersection'].coords[0])
        ptn = Point(row['geo_intersection'].coords[-1])
        t = row['prev_t']
        t_delta = row['t'] - t
        length = row['line'].length
        t0 = t + (t_delta * row['line'].project(pt0)/length)
        tn = t + (t_delta * row['line'].project(ptn)/length)
        # to avoid intersection issues with zero length lines
        if ptn == translate(pt0, 0.00000001, 0.00000001):
            t0 = row['prev_t']
            tn = row['t']
        # to avoid numerical issues with timestamps
        if is_equal(tn, row['t']):
            tn = row['t']
        if is_equal(t0, row['prev_t']):
            t0 = row['prev_t']
        return {'pt0':pt0, 'ptn':ptn, 't0':t0, 'tn':tn}
    else:
        return None

def _dissolve_ranges(ranges):
    if len(ranges) == 0:
        raise ValueError("Nothing to dissolve (received empty ranges)!")
    new = []
    start = None
    end = None
    pt0 = None
    ptn = None
    for r in ranges:
        if start is None:
            start = r[0]
            end = r[1]
            pt0 = r[2]
            ptn = r[3]
        elif end == r[0]:
            end = r[1]
            ptn = r[3]
        elif r[0] > end and is_equal(r[0], end):
            end = r[1]
            ptn = r[3]
        else:
            new.append((start, end, pt0, ptn))
            start = r[0]
            end = r[1]
            pt0 = r[2]
            ptn = r[3]
    new.append((start, end, pt0, ptn))
    return new

def is_equal(t1, t2):
    return abs(t1 - t2) < timedelta(milliseconds=10)

def intersects(traj, polygon):
    try:
        line = traj.to_linestring()
    except:
        return False
    return line.intersects(polygon)

def clip(traj, polygon):
    #pd.set_option('display.max_colwidth', -1)
    if not intersects(traj, polygon):
        return []
    j = 0
    ranges = []
    intersections = [] # list of trajectories

    line_df = _to_line_df(traj)

    # The following for loop creates wrong results if there
    # is no other column besides the geometry column.
    has_dummy = False
    if len(traj.df.columns) < 2:
        traj.df['dummy_that_stops_things_from_breaking'] = 1
        has_dummy = True

    spatial_index = line_df.sindex
    if spatial_index:
        possible_matches_index = list(spatial_index.intersection(polygon.bounds))
        possible_matches = line_df.iloc[possible_matches_index].sort_index()
    else:
        possible_matches = line_df

    # Note: If the trajectory contains consecutive rows without location change
    #       these will result in zero length lines that return an empty
    #       intersection.
    possible_matches['geo_intersection'] = possible_matches.intersection(polygon)
    possible_matches['intersection'] = possible_matches.apply(_get_spatiotemporal_ref, axis=1)

    for index, row in possible_matches.iterrows():
        x = row['intersection']
        if x is None:
            continue
        ranges.append((x['t0'], x['tn'], x['pt0'], x['ptn']))

    if len(ranges) > 0:
        ranges = _dissolve_ranges(ranges)
    for the_range in ranges:
        t0, tn, pt0, ptn = the_range[0], the_range[1], the_range[2], the_range[3]
        # Create row at entry point with attributes from previous row = pad
        row0 = traj.df.iloc[traj.df.index.get_loc(t0, method='pad')]
        row0['geometry'] = pt0
        # Create row at exit point
        rown = traj.df.iloc[traj.df.index.get_loc(tn, method='pad')]
        rown['geometry'] = ptn
        # Insert rows
        try:
            traj.df.loc[t0] = row0
            traj.df.loc[tn] = rown
        except:
            print("Failed to insert row")
            continue
        traj.df = traj.df.sort_index()

        try:
            intersection = traj.get_segment_between(the_range[0], the_range[1])
        except RuntimeError as e:
            print(e)
            continue
        intersection.crs = traj.crs
        intersection.id = "{}_{}".format(traj.id, j)
        intersection.parent = traj

        intersections.append(intersection)
        j += 1

    if has_dummy:
        intersection.df.drop(columns=['dummy_that_stops_things_from_breaking'], axis=1, inplace=True)

    return intersections

def intersection(traj, feature):
    if type(feature) != dict:
        raise TypeError("Trajectories can only be intersected with a Shapely feature!")
    try:
        geometry = shape(feature['geometry'])
        properties = feature['properties']
    except:
        raise TypeError("Trajectories can only be intersected with a Shapely feature!")

    intersections = clip(traj, geometry)

    result = []
    for intersection in intersections:
        for key, value in properties.items():
            intersection.df['intersecting_'+key] = value
        result.append(intersection)

    return result
