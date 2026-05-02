"""
utils/map_utils.py — Local map rendering for tornado tracks and geometry.
"""

import matplotlib
matplotlib.use("Agg") # Headless
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import logging
from typing import List, Tuple

logger = logging.getLogger("spc_bot")

def render_tornado_track(paths: List[List[Tuple[float, float]]], output_path: str):
    """
    Render a local map image showing tornado track paths.
    
    :param paths: List of paths, each path is a list of (lat, lon) tuples.
    :param output_path: Path to save the resulting PNG image.
    """
    if not paths:
        return
        
    # 1. Setup projection
    proj = ccrs.Mercator()
    data_proj = ccrs.PlateCarree()
    
    fig = plt.figure(figsize=(10, 8), dpi=100)
    ax = plt.axes(projection=proj)
    
    # 2. Add features
    ax.add_feature(cfeature.LAND, facecolor='#f9f9f9')
    ax.add_feature(cfeature.OCEAN, facecolor='#e0f2ff')
    ax.add_feature(cfeature.COASTLINE, linewidth=0.5, edgecolor='black')
    ax.add_feature(cfeature.BORDERS, linewidth=0.5, edgecolor='black')
    ax.add_feature(cfeature.STATES, linewidth=0.5, edgecolor='gray')
    
    # 3. Plot paths
    all_lats = []
    all_lons = []
    for path in paths:
        if not path: continue
        lats, lons = zip(*path)
        # Transform points for the projection
        ax.plot(lons, lats, color='#cc0000', linewidth=2.5, transform=data_proj, 
                label="Damage Track", solid_capstyle='round')
        # Add start/end points
        ax.scatter(lons[0], lats[0], color='green', s=30, transform=data_proj, zorder=5)
        ax.scatter(lons[-1], lats[-1], color='black', s=30, transform=data_proj, zorder=5)
        
        all_lats.extend(lats)
        all_lons.extend(lons)
        
    # 4. Zoom to track with reasonable padding
    if all_lats and all_lons:
        min_lat, max_lat = min(all_lats), max(all_lats)
        min_lon, max_lon = min(all_lons), max(all_lons)
        
        lat_span = max_lat - min_lat
        lon_span = max_lon - min_lon
        
        # Ensure a minimum span for visibility (approx 10km)
        min_span = 0.1 
        if lat_span < min_span:
            mid = (min_lat + max_lat) / 2
            min_lat, max_lat = mid - min_span/2, mid + min_span/2
        if lon_span < min_span:
            mid = (min_lon + max_lon) / 2
            min_lon, max_lon = mid - min_span/2, mid + min_span/2
            
        margin = 0.2 # 20% margin
        ax.set_extent([min_lon - (lon_span * margin), max_lon + (lon_span * margin), 
                       min_lat - (lat_span * margin), max_lat + (lat_span * margin)], 
                      crs=data_proj)
                       
    # 5. Labels and Save
    plt.title("NWS Tornado Damage Survey Track", fontsize=14, fontweight='bold', pad=15)
    plt.savefig(output_path, bbox_inches='tight', dpi=100)
    plt.close(fig)
    logger.info(f"[MAP] Rendered tornado track to {output_path}")
