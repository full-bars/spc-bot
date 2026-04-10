import requests
import math
from datetime import datetime, timezone

def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def get_asos_surface_wind(radar_lat, radar_lon, vwp_time=None, radius_km=150):
    """
    Find the nearest ASOS surface wind valid at or just before the VWP scan time.
    vwp_time: datetime object (UTC) of the VWP scan. If None, uses current time.
    """
    deg = radius_km / 111.0
    url = "https://aviationweather.gov/api/data/metar"
    params = {
        'bbox': f"{radar_lat-deg},{radar_lon-deg},{radar_lat+deg},{radar_lon+deg}",
        'format': 'json',
        'hours': 2,
    }

    try:
        r = requests.get(url, params=params, timeout=10,
                         headers={'User-Agent': 'hodobot/1.0'})
        r.raise_for_status()
        stations = r.json()
    except Exception as e:
        print(f"ASOS fetch failed: {e}")
        return None

    if vwp_time is None:
        ref_ts = datetime.now(timezone.utc).timestamp()
    else:
        ref_ts = vwp_time.replace(tzinfo=timezone.utc).timestamp()

    best_by_station = {}
    for s in stations:
        sid = s.get('icaoId', '')
        obs_ts = s.get('obsTime', 0)
        wdir = s.get('wdir')
        wspd = s.get('wspd')
        lat = s.get('lat')
        lon = s.get('lon')

        if lat is None or lon is None or wdir is None or wspd is None:
            continue
        if str(wdir).upper() == 'VRB':
            wdir = 0

        if obs_ts > ref_ts - 600:
            continue
        if obs_ts < ref_ts - 5400:
            continue

        dist = _haversine_km(radar_lat, radar_lon, lat, lon)

        if sid not in best_by_station or obs_ts > best_by_station[sid]['obs_ts']:
            best_by_station[sid] = {
                'obs_ts': obs_ts,
                'wdir': int(wdir),
                'wspd': int(wspd),
                'dist': dist,
                'sid': sid,
            }

    if not best_by_station:
        print("No ASOS stations found with valid wind data")
        return None

    candidates = sorted(best_by_station.values(), key=lambda x: x['dist'])
    best = candidates[0]
    obs_dt = datetime.fromtimestamp(best['obs_ts'], tz=timezone.utc).strftime('%H%M')
    print(f"ASOS surface wind: {best['sid']} {best['wdir']:03d}/{best['wspd']:02d}kt "
          f"({best['dist']:.0f} km, ob time {obs_dt}UTC)")
    return (best['wdir'], best['wspd'], best['sid'])
