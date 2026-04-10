from __future__ import print_function

import numpy as np
import sys
import os

# Allow running as a subprocess from any working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from lib.vad_plotter.vad_reader import download_vad, VADFile
from lib.vad_plotter.params import compute_parameters
from lib.vad_plotter.plot import plot_hodograph
from lib.vad_plotter.wsr88d import build_has_name
from lib.vad_plotter.asos import get_asos_surface_wind

import re
import argparse
from datetime import datetime, timedelta
import json
import glob

"""
vad.py
Author:     Tim Supinie (tsupinie@ou.edu)
Modified:   2026 - HTTPS fix, ASOS auto surface wind, extra parameters, spc-bot integration
"""

def is_vector(vec_str):
    return bool(re.match(r"[\d]{3}/[\d]{2}", vec_str))

def parse_vector(vec_str):
    return tuple(int(v) for v in vec_str.strip().split("/"))

def parse_time(time_str):
    no_my = False
    now = datetime.utcnow()
    if '-' not in time_str:
        no_my = True
        year = now.year
        month = now.month
        time_str = "%d-%d-%s" % (year, month, time_str)

    plot_time = datetime.strptime(time_str, '%Y-%m-%d/%H%M')

    if plot_time > now:
        if no_my:
            if month == 1:
                month = 12
                year -= 1
            else:
                month -= 1
            time_str = "%d-%d-%s" % (year, month, time_str)
            plot_time = datetime.strptime(time_str, '%Y-%m-%d/%H%M')
        else:
            raise ValueError("Time '%s' is in the future." % time_str)

    return plot_time


def vad_plotter(radar_id, storm_motion='right-mover', sfc_wind=None, time=None,
                fname=None, local_path=None, cache_path=None, web=False, fixed=False):
    plot_time = None
    if time:
        plot_time = parse_time(time)
    elif local_path is not None:
        raise ValueError("'-t' ('--time') argument is required when loading from the local disk.")

    if not web:
        print("Plotting VAD for %s ..." % radar_id)

    if local_path is None:
        vad = download_vad(radar_id, time=plot_time, cache_path=cache_path)
    else:
        iname = build_has_name(radar_id, plot_time)
        vad = VADFile(open("%s/%s" % (local_path, iname), 'rb'))

    vad.rid = radar_id

    if not web:
        print("Valid time:", vad['time'].strftime("%d %B %Y %H%M UTC"))

    sfc_wind_str = None
    if sfc_wind:
        sfc_wind_vec = parse_vector(sfc_wind)
        vad.add_surface_wind(sfc_wind_vec)
        sfc_wind_str = "Surface Wind: %s" % sfc_wind
    else:
        try:
            radar_lat = vad._radar_latitude
            radar_lon = vad._radar_longitude
            asos = get_asos_surface_wind(radar_lat, radar_lon, vwp_time=vad['time'])
            if asos is not None:
                wdir, wspd, sid = asos
                vad.add_surface_wind((wdir, wspd))
                sfc_wind_str = "Surface Wind: %03d/%02d (%s)" % (wdir, wspd, sid)
                if not web:
                    print(sfc_wind_str)
        except Exception as e:
            if not web:
                print("Could not fetch ASOS surface wind: %s" % e)

    params = compute_parameters(vad, storm_motion)
    plot_hodograph(vad, params, fname=fname, web=web, fixed=fixed,
                   archive=(local_path is not None), sfc_wind_str=sfc_wind_str)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('radar_id')
    ap.add_argument('-m', '--storm-motion', dest='storm_motion', default='right-mover')
    ap.add_argument('-s', '--sfc-wind', dest='sfc_wind')
    ap.add_argument('-t', '--time', dest='time')
    ap.add_argument('-f', '--img-name', dest='img_name')
    ap.add_argument('-p', '--local-path', dest='local_path')
    ap.add_argument('-c', '--cache-path', dest='cache_path')
    ap.add_argument('-w', '--web-mode', dest='web', action='store_true')
    ap.add_argument('-x', '--fixed-frame', dest='fixed', action='store_true')
    args = ap.parse_args()

    np.seterr(all='ignore')

    try:
        vad_plotter(args.radar_id,
            storm_motion=args.storm_motion,
            sfc_wind=args.sfc_wind,
            time=args.time,
            fname=args.img_name,
            local_path=args.local_path,
            cache_path=args.cache_path,
            web=args.web,
            fixed=args.fixed
        )
    except Exception:
        if args.web:
            print(json.dumps({'error': 'error'}))
        else:
            raise

if __name__ == "__main__":
    main()
