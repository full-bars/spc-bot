# config.py
import os
import json
import logging
import pytz
from dotenv import load_dotenv

# Single source of truth for the release displayed in /help.
# Bump this in the same commit as the git tag.
__version__ = "5.12.2"

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
    "health_channel_id": int(os.getenv("HEALTH_CHANNEL_ID") or os.getenv("SPC_CHANNEL_ID")),
    "sounding_channel_id": int(os.getenv("SOUNDING_CHANNEL_ID") or os.getenv("SPC_CHANNEL_ID")),
    "warnings_channel_id": int(os.getenv("WARNINGS_CHANNEL_ID") or os.getenv("SPC_CHANNEL_ID")),
    "dev_channel_id": int(os.getenv("DEV_CHANNEL_ID") or os.getenv("HEALTH_CHANNEL_ID") or os.getenv("SPC_CHANNEL_ID")),
    "manual_cache_file": os.getenv("MANUAL_CACHE_FILE", "posted_records.json"),
    "auto_cache_file": os.getenv("AUTO_CACHE_FILE", "auto_posted_records.json"),
    "guild_id": _require_int("GUILD_ID"),
    "cache_file_dir": os.getenv("CACHE_DIR", "cache"),
    "log_file": os.getenv("LOG_FILE", "spc_bot.log"),
    "nwws_firehose_log": os.getenv("NWWS_FIREHOSE_LOG", "nwws_firehose.log"),
}

if not CONFIG["token"]:
    raise ValueError("DISCORD_TOKEN environment variable not set")

TOKEN = CONFIG["token"]
MODELS_CHANNEL_ID = CONFIG["models_channel_id"]
SPC_CHANNEL_ID = CONFIG["spc_channel_id"]
HEALTH_CHANNEL_ID = CONFIG["health_channel_id"]
SOUNDING_CHANNEL_ID = CONFIG["sounding_channel_id"]
WARNINGS_CHANNEL_ID = CONFIG["warnings_channel_id"]
DEV_CHANNEL_ID = CONFIG["dev_channel_id"]
MANUAL_CACHE_FILE = os.path.join(CONFIG["cache_file_dir"], CONFIG["manual_cache_file"])
AUTO_CACHE_FILE = os.path.join(CONFIG["cache_file_dir"], CONFIG["auto_cache_file"])
NWWS_FIREHOSE_LOG = os.path.join(CONFIG["cache_file_dir"], CONFIG["nwws_firehose_log"])
GUILD_ID = CONFIG["guild_id"]
CACHE_DIR = CONFIG["cache_file_dir"]

os.makedirs(CACHE_DIR, exist_ok=True)

# ── Load Product Logic ────────────────────────────────────────────────────────
# Use absolute path to the root directory
_base_dir = os.path.dirname(os.path.abspath(__file__))
_products_file = os.path.join(_base_dir, "config", "products.json")

if not os.path.exists(_products_file):
    raise FileNotFoundError(
        f"Product config file not found at {_products_file}. "
        f"This file is required — the hardcoded fallback has been removed "
        f"to prevent silent drift between the JSON and code."
    )

with open(_products_file, "r", encoding="utf-8") as f:
    _P = json.load(f)

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
NWS_ALERTS_WARNINGS_URL = _P["nws_alerts_warnings_url"]
IEMBOT_FEED_URL = _P["iembot_feed_url"]
IEMBOT_BOTSTALK_URL = _P["iembot_botstalk_url"]
IEM_NWSTEXT_URL = _P["iem_nwstext_url"]
WXNEXT_BASE = _P["wxnext_base_url"]
WXNEXT_PAGE = _P["wxnext_page_url"]
SPC_DAY1_CATEGORICAL_GEOJSON_URL = _P["spc_day1_categorical_geojson_url"]

# NWWS-OI
NWWS_USER = os.getenv("NWWS_USER", "")
NWWS_PASSWORD = os.getenv("NWWS_PASSWORD", "")
NWWS_SERVER = os.getenv("NWWS_SERVER", "nwws-oi.weather.gov")

# Timezones
CENTRAL = pytz.timezone("America/Chicago")
PACIFIC = pytz.timezone("US/Pacific")
