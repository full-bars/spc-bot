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
_products_file = os.path.join(os.path.dirname(__file__), "config", "products.json")
try:
    with open(_products_file, "r") as f:
        _P = json.load(f)
except Exception as e:
    logger.error(f"Failed to load product config from {_products_file}: {e}")
    # Minimal hardcoded fallback to prevent complete failure
    _P = {
        "spc_schedule": {"1": [1, 6, 13, 20], "2": [2, 13], "3": [3, 15]},
        "spc_urls_fallback": {},
        "scp_image_urls": [],
        "wpc_image_urls": [],
        "spc_md_index_url": "https://www.spc.noaa.gov/products/md/",
        "spc_watch_index_url": "https://www.spc.noaa.gov/products/watch/",
        "spc_valid_watches_url": "https://www.spc.noaa.gov/products/watch/validww.png",
        "nws_alerts_url": "https://api.weather.gov/alerts/active"
    }

# Exported constants used by cogs
# Convert string keys from JSON to integers for schedule
SPC_SCHEDULE = {int(k): v for k, v in _P["spc_schedule"].items()}
SPC_OUTLOOK_BASE = _P.get("spc_outlook_base", "https://www.spc.noaa.gov/products/outlook")
# Convert string keys from JSON to integers for fallback URLs
SPC_URLS_FALLBACK = {int(k) if k.isdigit() else k: v for k, v in _P["spc_urls_fallback"].items()}
SPC_URLS = SPC_URLS_FALLBACK
SCP_IMAGE_URLS = _P["scp_image_urls"]
WPC_IMAGE_URLS = _P["wpc_image_urls"]
SPC_MD_INDEX_URL = _P["spc_md_index_url"]
SPC_WATCH_INDEX_URL = _P["spc_watch_index_url"]
SPC_VALID_WATCHES_URL = _P["spc_valid_watches_url"]
NWS_ALERTS_URL = _P["nws_alerts_url"]

# Timezones
CENTRAL = pytz.timezone("America/Chicago")
PACIFIC = pytz.timezone("US/Pacific")
