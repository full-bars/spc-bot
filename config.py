# config.py
import os
import json
import logging
import pytz
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("spc_bot")

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

# ── Load Product Logic ────────────────────────────────────────────────────────
# Use absolute path to the root directory
_base_dir = os.path.dirname(os.path.abspath(__file__))
_products_file = os.path.join(_base_dir, "config", "products.json")

# Default values if JSON load fails
_P = {
    "spc_schedule": {"1": [1, 6, 13, 20], "2": [2, 13], "3": [3, 15]},
    "spc_outlook_base": "https://www.spc.noaa.gov/products/outlook",
    "spc_urls_fallback": {
        "1": [
            "https://www.spc.noaa.gov/products/outlook/day1otlk.gif",
            "https://www.spc.noaa.gov/products/outlook/day1probotlk_torn.gif",
            "https://www.spc.noaa.gov/products/outlook/day1probotlk_wind.gif",
            "https://www.spc.noaa.gov/products/outlook/day1probotlk_hail.gif"
        ],
        "2": [
            "https://www.spc.noaa.gov/products/outlook/day2otlk.gif",
            "https://www.spc.noaa.gov/products/outlook/day2probotlk_torn.gif",
            "https://www.spc.noaa.gov/products/outlook/day2probotlk_wind.gif",
            "https://www.spc.noaa.gov/products/outlook/day2probotlk_hail.gif"
        ],
        "3": [
            "https://www.spc.noaa.gov/products/outlook/day3otlk.gif",
            "https://www.spc.noaa.gov/products/outlook/day3prob.gif"
        ],
        "48": ["https://www.spc.noaa.gov/products/exper/day4-8/day48prob.gif"]
    },
    "scp_image_urls": [
        "https://atlas.niu.edu/forecast/scp/cfs_week1.png",
        "https://atlas.niu.edu/forecast/scp/cfs_week2.png",
        "https://atlas.niu.edu/forecast/scp/cfs_week3.png",
        "https://atlas.niu.edu/forecast/scp/gefs_week1__CTRL.png",
        "https://atlas.niu.edu/forecast/scp/gefs_week2__CTRL.png"
    ],
    "wpc_image_urls": [
        "https://www.wpc.ncep.noaa.gov/qpf/94ewbg.gif",
        "https://www.wpc.ncep.noaa.gov/qpf/98ewbg.gif",
        "https://www.wpc.ncep.noaa.gov/qpf/99ewbg.gif"
    ],
    "spc_md_index_url": "https://www.spc.noaa.gov/products/md/",
    "spc_watch_index_url": "https://www.spc.noaa.gov/products/watch/",
    "spc_valid_watches_url": "https://www.spc.noaa.gov/products/watch/validww.png",
    "nws_alerts_url": "https://api.weather.gov/alerts/active?event=Severe%20Thunderstorm%20Watch,Tornado%20Watch&status=actual",
    "iembot_feed_url": "https://weather.im/iembot-json/room/spcchat",
    "iem_nwstext_url": "https://mesonet.agron.iastate.edu/api/1/nwstext/{product_id}",
    "wxnext_base_url": "https://www2.mmm.ucar.edu/projects/ncar_ensemble/ainwp/img",
    "wxnext_page_url": "https://www2.mmm.ucar.edu/projects/ncar_ensemble/ainwp/"
}

if os.path.exists(_products_file):
    try:
        with open(_products_file, "r") as f:
            _P.update(json.load(f))
    except Exception as e:
        logger.error(f"Failed to load product config from {_products_file}: {e}")
else:
    logger.warning(f"Product config file NOT FOUND at {_products_file} — using hardcoded defaults")

# Exported constants used by cogs
SPC_SCHEDULE = {int(k): v for k, v in _P["spc_schedule"].items()}
SPC_OUTLOOK_BASE = _P.get("spc_outlook_base")
SPC_URLS_FALLBACK = {str(k): v for k, v in _P["spc_urls_fallback"].items()}
SPC_URLS = SPC_URLS_FALLBACK
SCP_IMAGE_URLS = _P["scp_image_urls"]
WPC_IMAGE_URLS = _P["wpc_image_urls"]
SPC_MD_INDEX_URL = _P["spc_md_index_url"]
SPC_WATCH_INDEX_URL = _P["spc_watch_index_url"]
SPC_VALID_WATCHES_URL = _P["spc_valid_watches_url"]
NWS_ALERTS_URL = _P["nws_alerts_url"]
IEMBOT_FEED_URL = _P["iembot_feed_url"]
IEM_NWSTEXT_URL = _P["iem_nwstext_url"]
WXNEXT_BASE = _P["wxnext_base_url"]
WXNEXT_PAGE = _P["wxnext_page_url"]

# Timezones
CENTRAL = pytz.timezone("America/Chicago")
PACIFIC = pytz.timezone("US/Pacific")
