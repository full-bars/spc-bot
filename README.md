# WxAlert / SPCBot

A Discord bot for severe weather enthusiasts. Auto-posts SPC convective outlooks, mesoscale discussions, and tornado/severe thunderstorm watches in real time. Includes a NEXRAD Level 2 radar downloader pulling from the NOAA AWS S3 archive and a VWP hodograph generator for any NEXRAD or TDWR site.

## Features

* SPC convective outlooks (Day 1, 2, 3, Day 4-8) with dynamic URL resolution
* SPC mesoscale discussions with cancellation tracking and **high-probability watch detection for proactive sounding pre-warming**
* Tornado and severe thunderstorm watch alerts via NWS API with IEM iembot real-time feed for sub-second text **delivery via persistent database-backed pre-caching**
* NIU/Gensini CFSv2/GEFS supercell composite parameter (SCP) graphics, twice daily
* CSU-MLP machine learning severe weather forecasts (Days 1-8 + 6-panel summaries), auto-posted daily with `/csu` slash command (interactive dropdown)
* NCAR WxNext2 Mean AI convective hazard forecast (Days 1-8), auto-posted daily with `/wxnext` slash command
* Observed RAOB sounding plots via SounderPy with `/sounding` — supports city names, radar site codes, and station IDs with interactive station and time selection
* Auto-posts soundings for RAOB stations near active SPC watches — immediately on watch issuance (any hour via IEM) and at 00z/12z synoptic cycles
* VWP hodograph generation for any NEXRAD or TDWR site (200 sites) via `/hodograph`, with auto ASOS surface wind and storm parameter table
* NEXRAD Level 2 radar downloader from NOAA AWS S3
  * Single or multi-site downloads with per-site ZIP packaging
  * Z-to-Z range, start+duration, explicit datetime, or N most recent files
* **Enhanced Observability**: Detailed `/status` dashboard showing node roles (Primary/Standby), real-time task health, RSS memory usage, and feed synchronization state

## Prerequisites

* Python 3.10+
* A Discord bot token and application ([Discord Developer Portal](https://discord.com/developers/applications))
* Channel IDs and Guild ID for where the bot should post

## Setup

### Automatic (recommended)

A deploy script is included that creates a virtual environment, configures your `.env` interactively, and installs a systemd service that starts automatically on boot. The bot runs as your current user for easy management.

```bash
git clone https://github.com/full-bars/spc-bot.git
cd spc-bot
sudo ./deploy.sh
```

The script will prompt you for your Discord bot token, channel IDs, and guild ID, then handle everything else.

The bot is installed to wherever you cloned it (e.g., `~/spc-bot`) and runs as your current user. The following aliases are added automatically:

```bash
spcon        # start the bot
spcoff       # stop the bot
spcrestart   # restart the bot
spcstatus    # show status
spclog       # follow live logs
spclog50     # show last 50 log lines
spcupdate    # pull latest code and restart
```


### Docker (Pre-built Image)

The easiest way to run the bot is using the pre-built image from GitHub Container Registry:

1. Create a directory for the bot and download the `docker-compose.yml` and `.env.example`:
   ```bash
   mkdir spc-bot && cd spc-bot
   curl -O https://raw.githubusercontent.com/full-bars/spc-bot/main/docker-compose.yml
   curl -O https://raw.githubusercontent.com/full-bars/spc-bot/main/.env.example
   cp .env.example .env
   ```
2. Edit `.env` with your Discord token and channel IDs.
3. Start the bot:
   ```bash
   docker compose up -d
   ```

### Docker (Build from Source)

1. Clone the repository:
   ```bash
   git clone https://github.com/full-bars/spc-bot.git
   cd spc-bot
   ```
2. Edit `.env` with your Discord token and channel IDs.
3. Build and run:
   ```bash
   docker compose up -d --build
   ```

### Manual Installation (No Docker)
   ```
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and fill in your values:
   ```
   cp .env.example .env
   ```

5. Run the bot:
   ```
   python main.py
   ```

## Running Tests

```
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

## Project Structure

```
spc-bot/
├── main.py                  # Bot entrypoint, watchdog, signal handling
├── deploy.sh                # One-command deployment script
├── install-hooks.sh         # Installs pre-push git hooks (syntax + test checks)
├── config.py                # Configuration and centralized URL constants
├── requirements.txt         # Python dependencies
├── .env.example             # Template for required environment variables
├── CREDITS.md               # Third-party attributions
├── utils/
│   ├── http.py              # Async HTTP session management (centralized pooling)
│   ├── change_detection.py  # HEAD-based change detection, hashing
│   ├── cache.py             # Download orchestration, legacy globals (deprecated)
│   ├── state.py             # BotState class — single source of truth for in-memory state
│   ├── spc_urls.py          # SPC outlook URL resolution
│   ├── backoff.py           # Exponential backoff tracker for task loops
│   └── db.py                # Async SQLite state manager (aiosqlite) with persistent product text caching
├── cogs/
│   ├── outlooks.py          # SPC Day 1-3 and Day 4-8 auto-posting
│   ├── mesoscale.py         # SPC MD monitoring with watch probability detection
│   ├── iembot.py            # IEM iembot feed poller with persistent DB-backed text cache
│   ├── watches.py           # SPC watch monitoring via NWS API (stores affected_zones)
│   ├── scp.py               # NIU/Gensini SCP graphics, twice daily
│   ├── csu_mlp.py           # CSU-MLP consolidated /csu command with Choice dropdown
│   ├── ncar.py              # NCAR WxNext2 AI severe weather forecast
│   ├── sounding.py          # RAOB+ACARS sounding plots; auto-posts near active watches
│   ├── sounding_utils.py    # Location resolution, IEM fetch (all hours), ACARS fetch, plot generation
│   ├── sounding_views.py    # Discord UI: CombinedSoundingView, IEMTimeSelectionView, ACARS views
│   ├── hodograph.py         # VWP hodograph generation via /hodograph
│   ├── failover.py          # HTTP failover: cloudflared tunnel, Upstash coordination, primary/standby logic
│   ├── status.py            # Bot status and manual slash commands
│   └── radar/
│       ├── __init__.py      # Radar cog: /download with quick-start site+time+count params
│       ├── s3.py            # S3 client, file listing, time parsing
│       ├── downloads.py     # Download orchestration, zipping, progress
│       └── views.py         # Discord UI views and modals
├── lib/
│   └── vad_plotter/         # Hodograph library (vad-plotter by Tim Supinie)
│       ├── vad.py           # Main entry point, called as subprocess
│       ├── vad_reader.py    # NEXRAD VWP binary parser
│       ├── plot.py          # Hodograph plotting with matplotlib
│       ├── params.py        # Storm parameter computations
│       ├── wsr88d.py        # Radar site info and filename utilities
│       ├── asos.py          # ASOS surface wind fetching
│       └── utils.py         # Shared exception types
└── tests/
    ├── conftest.py          # Test environment setup
    ├── test_utils.py        # Unit tests for utilities and sounding parsing
    ├── test_watches.py      # Unit tests for watch VTEC parsing
    ├── test_integration.py  # Integration tests: BotState, cog instantiation, function signatures
    ├── test_hodograph.py    # Unit tests for hodograph cog
    └── test_iem_races.py    # Tests for IEM/SPC race logic and watch-triggered soundings
```

## Status

Work in progress. Actively developed in my free time, expect some bugs.

## Built With

* [discord.py](https://github.com/Rapptz/discord.py)
* [aiohttp](https://github.com/aio-libs/aiohttp)
* [aioboto3](https://github.com/aio-libs/aioboto3)
* [aiosqlite](https://github.com/omnilib/aiosqlite)
* [sounderpy](https://github.com/kylejgillett/sounderpy)
* [MetPy](https://github.com/Unidata/MetPy)
* [numpy](https://numpy.org)
* [matplotlib](https://matplotlib.org)
* [requests](https://requests.readthedocs.io)
* [pytz](https://github.com/stub42/pytz)
* [vad-plotter](https://github.com/tsupinie/vad-plotter) by Tim Supinie
