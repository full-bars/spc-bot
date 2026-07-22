# config.py
import os
import json
import logging
import pytz
import subprocess
from dotenv import load_dotenv


_cached_version = None


def get_version() -> str:
    """Get version from git tag, VERSION file, or fallback (cached)."""
    global _cached_version
    if _cached_version:
        return _cached_version

    try:
        # Try to read from git tag (most accurate)
        version = subprocess.check_output(
            ["git", "describe", "--tags", "--always"],
            cwd=os.path.dirname(__file__),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()
        # Remove leading 'v' if present (v5.16.1 → 5.16.1)
        if version.startswith("v"):
            version = version[1:]
        _cached_version = version
        return version
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Fall back to VERSION file for non-git deployments
    version_file = os.path.join(os.path.dirname(__file__), "VERSION")
    if os.path.exists(version_file):
        try:
            with open(version_file) as f:
                v = f.read().strip()
                _cached_version = v
                return v
        except Exception:
            pass

    # Final fallback
    _cached_version = "unknown"
    return "unknown"


def __getattr__(name):
    if name == "__version__":
        return get_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


load_dotenv()

logger = logging.getLogger("spc_bot")


def _require_int(name: str) -> int:
    """Require an integer environment variable — fail fast if missing."""
    val = os.getenv(name)
    if not val:
        raise ValueError(f"{name} environment variable not set")
    try:
        return int(val)
    except ValueError:
        raise ValueError(f"{name}={val!r} must be an integer")


def _optional_int(*names: str) -> int:
    """Return the int value of the first env var that is set.

    Validates the value if set (raises ValueError with a helpful message).
    The last name is treated as required if none of the preceding names are set.
    """
    for name in names[:-1]:
        val = os.getenv(name)
        if val:
            try:
                return int(val)
            except ValueError:
                raise ValueError(f"{name}={val!r} must be an integer")
    return _require_int(names[-1])


CONFIG = {
    "token": os.getenv("DISCORD_TOKEN"),
    "models_channel_id": _require_int("MODELS_CHANNEL_ID"),
    "spc_channel_id": _require_int("SPC_CHANNEL_ID"),
    "health_channel_id": _optional_int("HEALTH_CHANNEL_ID", "SPC_CHANNEL_ID"),
    "sounding_channel_id": _optional_int("SOUNDING_CHANNEL_ID", "SPC_CHANNEL_ID"),
    "warnings_channel_id": _optional_int("WARNINGS_CHANNEL_ID", "SPC_CHANNEL_ID"),
    "tor_channel_id": _optional_int("TOR_CHANNEL_ID", "WARNINGS_CHANNEL_ID", "SPC_CHANNEL_ID"),
    "svr_channel_id": _optional_int("SVR_CHANNEL_ID", "WARNINGS_CHANNEL_ID", "SPC_CHANNEL_ID"),
    "ffw_channel_id": _optional_int("FFW_CHANNEL_ID", "WARNINGS_CHANNEL_ID", "SPC_CHANNEL_ID"),
    "sps_channel_id": _optional_int("SPS_CHANNEL_ID", "WARNINGS_CHANNEL_ID", "SPC_CHANNEL_ID"),
    "surveys_channel_id": _optional_int(
        "SURVEYS_CHANNEL_ID", "WARNINGS_CHANNEL_ID", "SPC_CHANNEL_ID"
    ),
    "dev_channel_id": _optional_int("DEV_CHANNEL_ID", "HEALTH_CHANNEL_ID", "SPC_CHANNEL_ID"),
    "tropical_channel_id": int(os.getenv("TROPICAL_CHANNEL_ID") or 981540312688230420),
    "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
    "opencode_api_key": os.getenv("OPENCODE_API_KEY", ""),
    "ai_model": os.getenv("AI_MODEL", "deepseek-v4-pro"),
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
TOR_CHANNEL_ID = CONFIG["tor_channel_id"]
SVR_CHANNEL_ID = CONFIG["svr_channel_id"]
FFW_CHANNEL_ID = CONFIG["ffw_channel_id"]
SPS_CHANNEL_ID = CONFIG["sps_channel_id"]
SURVEYS_CHANNEL_ID = CONFIG["surveys_channel_id"]
DEV_CHANNEL_ID = CONFIG["dev_channel_id"]
TROPICAL_CHANNEL_ID = CONFIG["tropical_channel_id"]
GEMINI_API_KEY = CONFIG["gemini_api_key"]
OPENCODE_API_KEY = CONFIG["opencode_api_key"]
AI_MODEL = CONFIG["ai_model"]
MANUAL_CACHE_FILE = os.path.join(CONFIG["cache_file_dir"], CONFIG["manual_cache_file"])
AUTO_CACHE_FILE = os.path.join(CONFIG["cache_file_dir"], CONFIG["auto_cache_file"])
NWWS_FIREHOSE_LOG = os.path.join(CONFIG["cache_file_dir"], CONFIG["nwws_firehose_log"])
GUILD_ID = CONFIG["guild_id"]
CACHE_DIR: str = str(CONFIG["cache_file_dir"])

# Forensics and Archive paths
RECORDING_DIR = os.path.join(CACHE_DIR, "vad_recordings")
ARCHIVE_DIR = os.path.join(CACHE_DIR, "event_archive")

# rclone backup config
RCLONE_REMOTE = os.getenv("RCLONE_REMOTE", "gdrive")
RCLONE_DEST_DIR = os.getenv("RCLONE_DEST_DIR", "spc-bot-forensics")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(RECORDING_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

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
IEMBOT_NHC_URL = _P["iembot_nhc_url"]
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
