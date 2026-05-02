"""
utils/dat_api.py — Interface for the NWS Damage Assessment Toolkit (DAT) ArcGIS API.
"""

import logging
from typing import Optional, List, Tuple
from utils.http import http_get_json

logger = logging.getLogger("spc_bot")

DAT_BASE_URL = "https://services.dat.noaa.gov/arcgis/rest/services/nws_damageassessmenttoolkit/DamageViewer/FeatureServer"
TRACK_LAYER_ID = 1

async def fetch_dat_track_geometry(guid: str) -> Optional[List[List[Tuple[float, float]]]]:
    """
    Fetch the polyline geometry for a tornado track from DAT.
    Returns a list of paths, where each path is a list of (lat, lon) tuples.
    """
    # Query Layer 1 (Lines) for this event_id. 
    # outSR=4326 ensures we get standard WGS84 (Lat/Lon) coordinates.
    query_url = (
        f"{DAT_BASE_URL}/{TRACK_LAYER_ID}/query"
        f"?where=event_id='{guid}'&outFields=*&returnGeometry=true&outSR=4326&f=json"
    )
    
    try:
        data = await http_get_json(query_url, retries=1, timeout=15)
        if not data or "features" not in data:
            return None
            
        features = data["features"]
        if not features:
            logger.debug(f"[DAT-API] No track geometry found for GUID {guid}")
            return None
            
        # Standard ArcGIS Polyline geometry: {"paths": [[[x1, y1], [x2, y2]], ...]}
        # ArcGIS uses [lon, lat] (x, y) format.
        all_paths = []
        for feat in features:
            geom = feat.get("geometry")
            if not geom or "paths" not in geom:
                continue
                
            for path in geom["paths"]:
                # Convert [lon, lat] to (lat, lon) for bot-wide consistency
                converted_path = [(float(p[1]), float(p[0])) for p in path]
                all_paths.append(converted_path)
                
        if all_paths:
            logger.info(f"[DAT-API] Successfully fetched {len(all_paths)} path(s) for GUID {guid}")
            return all_paths
            
    except Exception as e:
        logger.warning(f"[DAT-API] Error fetching track geometry for {guid}: {e}")
        
    return None
