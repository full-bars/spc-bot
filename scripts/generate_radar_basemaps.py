"""
Generate dark-themed circular radar basemaps for all NEXRAD sites.
Run once: python scripts/generate_radar_basemaps.py
Output: cache/radar_basemaps/{SITE}.png
"""
import os

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

RANGE_KM = 460
SIZE = 800
DPI = 100
OUT_DIR = "cache/radar_basemaps"

SITES = {
    "KABX": (35.1497, -106.8239), "KABR": (45.4558, -98.4130),
    "KAKQ": (36.9839, -77.0075), "KAMA": (35.2333, -101.7093),
    "KAMX": (25.6105, -80.4130), "KAPX": (44.9063, -84.7195),
    "KARX": (43.8228, -91.1911), "KATX": (48.1946, -122.4957),
    "KBBX": (39.4961, -121.6316), "KBGM": (42.1997, -75.9847),
    "KBHX": (40.4983, -124.2922), "KBIS": (46.7708, -100.7603),
    "KBLX": (45.8538, -108.6068), "KBMX": (33.1719, -86.7697),
    "KBOX": (41.9558, -71.1369), "KBRO": (25.9155, -97.4186),
    "KBUF": (42.9486, -78.7369), "KBYX": (24.5969, -81.7033),
    "KCAE": (33.9486, -81.1186), "KCBW": (46.0392, -67.8064),
    "KCBX": (43.4902, -116.2360), "KCCX": (40.9231, -78.0039),
    "KCLE": (41.4130, -81.8600), "KCLX": (32.6555, -81.0422),
    "KCRP": (27.7839, -97.5108), "KCXX": (44.5111, -73.1664),
    "KCYS": (41.1519, -104.8061), "KDAX": (38.5011, -121.6778),
    "KDDC": (37.7608, -99.9689), "KDFX": (29.2725, -100.2803),
    "KDGX": (32.2800, -89.9844), "KDIX": (39.9469, -74.4107),
    "KDLH": (46.8369, -92.2097), "KDMX": (41.7311, -93.7228),
    "KDOX": (38.8255, -75.4400), "KDTX": (42.7000, -83.4717),
    "KDVN": (41.6117, -90.5808), "KDYX": (32.5383, -99.2542),
    "KEAX": (38.8102, -94.2645), "KEMX": (31.8936, -110.6303),
    "KENX": (42.5866, -74.0641), "KEOX": (31.4606, -85.4594),
    "KEPZ": (31.8730, -106.6980), "KESX": (35.7011, -114.8914),
    "KEVX": (30.5650, -85.9217), "KEWX": (29.7040, -98.0286),
    "KEYX": (35.0978, -117.5607), "KFCX": (37.0242, -80.2742),
    "KFDR": (34.3622, -98.9767), "KFDX": (34.6342, -103.6189),
    "KFFC": (33.3633, -84.5658), "KFSD": (43.5878, -96.7289),
    "KFSX": (34.5743, -111.1984), "KFTG": (39.7866, -104.5458),
    "KFWS": (32.5728, -97.3031), "KGGW": (48.2064, -106.6247),
    "KGJX": (39.0622, -108.2138), "KGLD": (39.3669, -101.7003),
    "KGRB": (44.4986, -88.1111), "KGRK": (30.7217, -97.3828),
    "KGRR": (42.8939, -85.5449), "KGSP": (34.8833, -82.2198),
    "KGWX": (33.8969, -88.3292), "KGYX": (43.8913, -70.2564),
    "KHDC": (30.5196, -90.4074), "KHDX": (33.0770, -106.1200),
    "KHGX": (29.4719, -95.0789), "KHNX": (36.3142, -119.6321),
    "KHPX": (36.7367, -87.2850), "KHTX": (34.9305, -86.0836),
    "KICT": (37.6544, -97.4430), "KICX": (37.5910, -112.8622),
    "KILN": (39.4203, -83.8217), "KILX": (40.1505, -89.3368),
    "KIND": (39.7075, -86.2803), "KINX": (36.1750, -95.5641),
    "KIWA": (33.2892, -111.6700), "KIWX": (41.3586, -85.7000),
    "KJAX": (30.4846, -81.7019), "KJGX": (32.6750, -83.3511),
    "KJKL": (37.5908, -83.3131), "KLBB": (33.6541, -101.8142),
    "KLCH": (30.1253, -93.2159), "KLGX": (47.1169, -124.1066),
    "KLNX": (41.9579, -100.5762), "KLOT": (41.6044, -88.0844),
    "KLRX": (40.7397, -116.8028), "KLSX": (38.6989, -90.6828),
    "KLTX": (33.9892, -78.4292), "KLVX": (37.9753, -85.9439),
    "KLWX": (38.9761, -77.4875), "KLZK": (34.8364, -92.2619),
    "KMAF": (31.9435, -102.1892), "KMAX": (42.0811, -122.7173),
    "KMBX": (48.3925, -100.8644), "KMHX": (34.7758, -76.8764),
    "KMKX": (42.9678, -88.5506), "KMLB": (28.1131, -80.6544),
    "KMOB": (30.6794, -88.2397), "KMPX": (44.8489, -93.5655),
    "KMQT": (46.5311, -87.5483), "KMRX": (36.1683, -83.4019),
    "KMSX": (47.0411, -113.9861), "KMTX": (41.2628, -112.4478),
    "KMUX": (37.1552, -121.8984), "KMVX": (47.5281, -97.3250),
    "KMXX": (32.5366, -85.7897), "KNKX": (32.9189, -117.0419),
    "KNQA": (35.3447, -89.8733), "KOAX": (41.3203, -96.3668),
    "KOHX": (36.2472, -86.5625), "KOKX": (40.8655, -72.8639),
    "KOTX": (47.6805, -117.6258), "KPAH": (37.0683, -88.7719),
    "KPBZ": (40.5317, -80.2179), "KPDT": (45.6906, -118.8529),
    "KPOE": (31.1553, -92.9758), "KPUX": (38.4594, -104.1814),
    "KRAX": (35.6653, -78.4900), "KRGX": (39.7540, -119.4620),
    "KRIW": (43.0661, -108.4773), "KRLX": (38.3111, -81.7228),
    "KRTX": (45.7150, -122.9650), "KSFX": (43.1056, -112.6861),
    "KSGF": (37.2353, -93.4003), "KSHV": (32.4508, -93.8412),
    "KSJT": (31.3711, -100.4922), "KSOX": (33.8177, -117.6360),
    "KSRX": (35.2904, -94.3619), "KTBW": (27.7053, -82.4019),
    "KTFX": (47.4597, -111.3853), "KTLH": (30.3975, -84.3289),
    "KTLX": (35.3331, -97.2777), "KTWX": (38.9969, -96.2325),
    "KTYX": (43.7558, -75.6800), "KUDX": (44.1247, -102.8300),
    "KUEX": (40.3208, -98.4419), "KVAX": (30.8903, -83.0018),
    "KVBX": (34.8385, -120.3979), "KVNX": (36.7408, -98.1275),
    "KVTX": (34.4117, -119.1786), "KVWX": (38.2602, -87.7245),
    "KYUX": (32.4953, -114.6567),
}

CITIES = [
    # (lat, lon, name, pop_rank)
    (40.7128, -74.0060, "NYC", 0), (34.0522, -118.2437, "Los Angeles", 0),
    (41.8781, -87.6298, "Chicago", 0), (29.7604, -95.3698, "Houston", 0),
    (33.4484, -112.0740, "Phoenix", 0), (39.7392, -104.9903, "Denver", 0),
    (29.4241, -98.4936, "San Antonio", 0), (32.7157, -117.1611, "San Diego", 0),
    (32.7767, -96.7970, "Dallas", 0), (37.3382, -121.8863, "San Jose", 0),
    (30.2672, -97.7431, "Austin", 0), (30.3322, -81.6557, "Jacksonville", 0),
    (32.7555, -97.3308, "Ft Worth", 0), (35.4676, -97.5164, "Oklahoma City", 0),
    (39.9612, -82.9988, "Columbus", 0), (35.2271, -80.8431, "Charlotte", 0),
    (35.1495, -90.0490, "Memphis", 1), (42.3601, -71.0589, "Boston", 1),
    (38.9072, -77.0369, "Washington", 1), (36.1627, -86.7816, "Nashville", 1),
    (36.0680, -95.9378, "Tulsa", 1), (37.6872, -97.3371, "Wichita", 1),
    (35.2220, -101.8310, "Amarillo", 1), (34.6040, -98.3960, "Lawton", 1),
    (36.3960, -97.8780, "Enid", 1), (34.7460, -92.2900, "Little Rock", 1),
    (37.1000, -94.5800, "Kansas City", 1), (39.0480, -95.6780, "Topeka", 1),
    (35.0850, -106.6060, "Albuquerque", 1), (32.5250, -93.7500, "Shreveport", 1),
    (38.6270, -90.1990, "St Louis", 1), (33.7490, -84.3880, "Atlanta", 1),
    (44.9780, -93.2650, "Minneapolis", 1), (41.2560, -95.9340, "Omaha", 1),
    (36.1740, -86.5620, "Nashville", 1), (35.9600, -83.9200, "Knoxville", 1),
    (32.2980, -90.1840, "Jackson", 1), (30.4210, -91.1530, "Baton Rouge", 1),
    (29.9490, -90.0710, "New Orleans", 1), (33.4640, -112.0800, "Mesa", 1),
    (36.7380, -97.1280, "Ponca City", 1), (35.5960, -97.3990, "Edmond", 1),
    (34.5030, -98.4090, "Wichita Falls", 1), (32.4600, -98.7200, "Dallas Area", 1),
    (41.7000, -93.5940, "Des Moines", 1), (40.8070, -96.7000, "Lincoln", 1),
    (43.6100, -96.1690, "Sioux Falls", 1), (46.8770, -96.7890, "Fargo", 1),
    (46.8270, -100.7790, "Bismarck", 1), (44.0830, -103.2310, "Rapid City", 1),
    (41.5930, -102.0910, "Scottsbluff", 1), (41.1400, -104.8200, "Cheyenne", 1),
    (40.5870, -105.0450, "Fort Collins", 1), (38.8340, -104.8210, "Colorado Springs", 1),
    (35.6870, -105.9380, "Santa Fe", 1), (32.2220, -110.9260, "Tucson", 1),
    (36.1140, -115.1730, "Las Vegas", 1), (40.7590, -111.8880, "Salt Lake City", 1),
    (43.6200, -116.2140, "Boise", 1), (47.6060, -122.3320, "Seattle", 1),
    (45.5150, -122.6790, "Portland", 1), (38.5810, -121.4940, "Sacramento", 1),
    (37.7740, -122.4190, "San Francisco", 1), (36.8820, -76.2730, "Norfolk", 1),
    (38.6270, -90.1990, "St Louis", 1),
]


def generate(site: str, lat: float, lon: float):
    path = os.path.join(OUT_DIR, f"{site}.png")
    if os.path.exists(path):
        return

    fig = plt.figure(figsize=(SIZE / DPI, SIZE / DPI), dpi=DPI)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.AzimuthalEquidistant(central_longitude=lon, central_latitude=lat))

    half = RANGE_KM * 1000
    ax.set_xlim(-half, half)
    ax.set_ylim(-half, half)
    ax.axis("off")
    ax.set_facecolor("#0d1117")

    scale = "50m"
    ax.add_feature(cfeature.LAND.with_scale(scale), color="#121620", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale(scale), color="#0a0c12", zorder=0)
    ax.add_feature(cfeature.LAKES.with_scale(scale), color="#0a0c12", edgecolor="#1a2a3e", linewidth=0.3, zorder=1)
    ax.add_feature(cfeature.RIVERS.with_scale(scale), edgecolor="#1a2a3e", linewidth=0.25, zorder=1)
    ax.add_feature(cfeature.STATES.with_scale(scale), edgecolor="#2a3a4e", linewidth=0.6, zorder=2)
    ax.add_feature(cfeature.COASTLINE.with_scale(scale), edgecolor="#3a4a6e", linewidth=0.8, zorder=2)

    # Range rings
    for km in [50, 100, 150, 200, 300, 400]:
        c = plt.Circle((0, 0), radius=km * 1000, color="#3a4050", fill=False, linewidth=0.4, linestyle="-", transform=ccrs.AzimuthalEquidistant(central_longitude=lon, central_latitude=lat))
        ax.add_patch(c)

    # Cities
    for clat, clon, name, rank in CITIES:
        dx = (clon - lon) * 111320 * np.cos(np.radians((clat + lat) / 2))
        dy = (clat - lat) * 111320
        d_km = np.sqrt(dx**2 + dy**2) / 1000
        if d_km < RANGE_KM * 1.1:
            ax.plot(clon, clat, "o", color="#c8d4e6", markersize=3 if rank == 0 else 2,
                    transform=ccrs.Geodetic(), zorder=3)
            ax.text(clon, clat, name, fontsize=5.5 if rank == 0 else 4.5,
                    color="#96a4c3", transform=ccrs.Geodetic(), zorder=3)

    fig.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=0, facecolor="#0d1117")
    plt.close(fig)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    sites_list = sorted(SITES.items())
    total = len(sites_list)
    print(f"Generating {total} circular basemaps in {OUT_DIR}/ ...")
    for i, (site, (lat, lon)) in enumerate(sites_list, 1):
        generate(site, lat, lon)
        print(f"  [{i}/{total}] {site}", flush=True)
    size_kb = sum(os.path.getsize(os.path.join(OUT_DIR, f)) for f in os.listdir(OUT_DIR) if f.endswith(".png")) / 1024
    print(f"Done. Total: {size_kb:.0f} KB across {total} sites.")


if __name__ == "__main__":
    main()
