"""
utils/map_utils.py — Local map rendering for tornado tracks and geometry.
"""

import matplotlib
matplotlib.use("Agg") # Headless
import matplotlib.pyplot as plt
import matplotlib.patheffects as patheffects
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cartopy.io.img_tiles as cimgt
import logging
from typing import List, Tuple

logger = logging.getLogger("spc_bot")

def render_tornado_track(paths: List[List[Tuple[float, float]]], output_path: str):
    """
    Render a professional map image showing tornado track paths using terrain tiles.
    
    :param paths: List of paths, each path is a list of (lat, lon) tuples.
    :param output_path: Path to save the resulting PNG image.
    """
    if not paths:
        return

    # 1. Setup projection and tiles
    # We use OSM tiles as a reliable base
    request = cimgt.OSM() 
    proj = request.crs
    data_proj = ccrs.PlateCarree()
    
    fig = plt.figure(figsize=(12, 9), dpi=120)
    ax = plt.axes(projection=proj)
    
    # 2. Determine Extent
    all_lats = []
    all_lons = []
    for path in paths:
        if not path: continue
        lats, lons = zip(*path)
        all_lats.extend(lats)
        all_lons.extend(lons)

    if not all_lats or not all_lons:
        plt.close(fig)
        return

    min_lat, max_lat = min(all_lats), max(all_lats)
    min_lon, max_lon = min(all_lons), max(all_lons)
    
    lat_span = max_lat - min_lat
    lon_span = max_lon - min_lon
    
    # Minimum 0.1 degree span for tiny tracks
    if lat_span < 0.1:
        mid = (min_lat + max_lat) / 2
        min_lat, max_lat = mid - 0.05, mid + 0.05
    if lon_span < 0.1:
        mid = (min_lon + max_lon) / 2
        min_lon, max_lon = mid - 0.05, mid + 0.05

    # Set extent with 30% margin
    margin = 0.3
    ax.set_extent([min_lon - (lon_span * margin), max_lon + (lon_span * margin), 
                   min_lat - (lat_span * margin), max_lat + (lat_span * margin)], 
                  crs=data_proj)

    # 3. Add Tiles and Features
    # Zoom level 11 is a good balance for tornado tracks
    ax.add_image(request, 11)
    
    # Add County lines (High Detail)
    counties = cfeature.NaturalEarthFeature(
        category='cultural',
        name='admin_2_counties',
        scale='10m',
        facecolor='none'
    )
    ax.add_feature(counties, edgecolor='#555555', linewidth=0.6, linestyle=':')
    ax.add_feature(cfeature.STATES, edgecolor='black', linewidth=1.0)
    
    # 4. Plot paths
    for path in paths:
        if not path: continue
        lats, lons = zip(*path)
        # Plot the main track with a white outline for visibility over tiles
        ax.plot(lons, lats, color='#ff0000', linewidth=3.5, transform=data_proj, 
                zorder=10, solid_capstyle='round', path_effects=[
                    patheffects.withStroke(linewidth=5, foreground='white')
                ])
        # Add start/end markers
        ax.scatter(lons[0], lats[0], color='#00ff00', s=60, edgecolors='black', 
                   transform=data_proj, zorder=15, label="Start")
        ax.scatter(lons[-1], lats[-1], color='#000000', s=60, edgecolors='white', 
                   transform=data_proj, zorder=15, label="End")
        
    # 5. Save
    plt.title("NWS Damage Assessment Toolkit - Survey Track", 
              fontsize=16, fontweight='bold', pad=20, backgroundcolor='white')
    
    plt.savefig(output_path, bbox_inches='tight', dpi=120)
    plt.close(fig)
    logger.info(f"[MAP] Rendered high-detail track to {output_path}")
