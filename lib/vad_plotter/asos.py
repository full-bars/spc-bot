import json
import math
import logging
from datetime import datetime, timezone
from utils.http import http_get_bytes

logger = logging.getLogger("spc_bot")

def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

async def get_asos_surface_wind(radar_lat, radar_lon, vwp_time=None, radius_km=150):
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

    # Use aiohttp via our shared utility
    query = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{query}"
    
    try:
        content, status = await http_get_bytes(
            full_url, 
            headers={'User-Agent': 'WxAlertSPCBot/1.0'},
            retries=2,
            timeout=10
        )
        if status != 200 or not content:
            logger.warning(f"[ASOS] Fetch failed with status {status}")
            return None
        stations = json.loads(content)
    except Exception as e:
        logger.warning(f"[ASOS] Fetch failed: {e}")
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
