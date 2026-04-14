# config.py
import os

from dotenv import load_dotenv

load_dotenv()


def _require_int(name: str) -> int:
    """Require an integer environment variable — fail fast if missing."""
    val = os.getenv(name)
    if not val:
        raise ValueError(f"{name} environment variable not set")
    return int(val)


CONFIG = {
    "token": os.getenv("DISCORD_TOKEN"),
    "models_channel_id": _require_int("MODELS_CHANNEL_ID"),
    "spc_channel_id": _require_int("SPC_CHANNEL_ID"),
    "sounding_channel_id": int(os.getenv("SOUNDING_CHANNEL_ID") or os.getenv("SPC_CHANNEL_ID")),
    "manual_cache_file": os.getenv("MANUAL_CACHE_FILE", "posted_records.json"),
    "auto_cache_file": os.getenv("AUTO_CACHE_FILE", "auto_posted_records.json"),
    "guild_id": _require_int("GUILD_ID"),
    "cache_file_dir": os.getenv("CACHE_DIR", "cache"),
    "log_file": os.getenv("LOG_FILE", "spc_bot.log"),
}

if not CONFIG["token"]:
    raise ValueError("DISCORD_TOKEN environment variable not set")

TOKEN = CONFIG["token"]
MODELS_CHANNEL_ID = CONFIG["models_channel_id"]
SPC_CHANNEL_ID = CONFIG["spc_channel_id"]
SOUNDING_CHANNEL_ID = CONFIG["sounding_channel_id"]
MANUAL_CACHE_FILE = os.path.join(CONFIG["cache_file_dir"], CONFIG["manual_cache_file"])
AUTO_CACHE_FILE = os.path.join(CONFIG["cache_file_dir"], CONFIG["auto_cache_file"])
GUILD_ID = CONFIG["guild_id"]
CACHE_DIR = CONFIG["cache_file_dir"]

os.makedirs(CACHE_DIR, exist_ok=True)

# Timezones
import pytz

CENTRAL = pytz.timezone("America/Chicago")
PACIFIC = pytz.timezone("US/Pacific")

# SPC update schedule (Central hours)
SPC_SCHEDULE = {
    1: [1, 6, 13, 20],
    2: [2, 13],
    3: [3, 15],
}

# SPC URLs
SPC_OUTLOOK_BASE = "https://www.spc.noaa.gov/products/outlook"

SPC_URLS_FALLBACK = {
    1: [
        "https://www.spc.noaa.gov/products/outlook/day1otlk.gif",
        "https://www.spc.noaa.gov/products/outlook/day1probotlk_torn.gif",
        "https://www.spc.noaa.gov/products/outlook/day1probotlk_wind.gif",
        "https://www.spc.noaa.gov/products/outlook/day1probotlk_hail.gif",
    ],
    2: [
        "https://www.spc.noaa.gov/products/outlook/day2otlk.gif",
        "https://www.spc.noaa.gov/products/outlook/day2probotlk_torn.gif",
        "https://www.spc.noaa.gov/products/outlook/day2probotlk_wind.gif",
        "https://www.spc.noaa.gov/products/outlook/day2probotlk_hail.gif",
    ],
    3: [
        "https://www.spc.noaa.gov/products/outlook/day3otlk.gif",
        "https://www.spc.noaa.gov/products/outlook/day3prob.gif",
    ],
    "48": ["https://www.spc.noaa.gov/products/exper/day4-8/day48prob.gif"],
}

SPC_URLS = SPC_URLS_FALLBACK

# SCP (Supercell Composite Parameter) — NIU/Gensini
SCP_IMAGE_URLS = [
    "https://atlas.niu.edu/forecast/scp/cfs_week1.png",
    "https://atlas.niu.edu/forecast/scp/cfs_week2.png",
    "https://atlas.niu.edu/forecast/scp/cfs_week3.png",
    "https://atlas.niu.edu/forecast/scp/gefs_week1__CTRL.png",
    "https://atlas.niu.edu/forecast/scp/gefs_week2__CTRL.png",
]

# WPC
WPC_IMAGE_URLS = [
    "https://www.wpc.ncep.noaa.gov/qpf/94ewbg.gif",
    "https://www.wpc.ncep.noaa.gov/qpf/98ewbg.gif",
    "https://www.wpc.ncep.noaa.gov/qpf/99ewbg.gif",
]

# SPC product index URLs
SPC_MD_INDEX_URL = "https://www.spc.noaa.gov/products/md/"
SPC_WATCH_INDEX_URL = "https://www.spc.noaa.gov/products/watch/"
SPC_VALID_WATCHES_URL = "https://www.spc.noaa.gov/products/watch/validww.png"

# NWS alerts API
NWS_ALERTS_URL = (
    "https://api.weather.gov/alerts/active"
    "?event=Severe%20Thunderstorm%20Watch,Tornado%20Watch&status=actual"
)
