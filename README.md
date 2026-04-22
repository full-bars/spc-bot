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

* Python 3.12+ (matches the Dockerfile; 3.10/3.11 may still work but CI runs on 3.12)
* A Discord bot token and application ([Discord Developer Portal](https://discord.com/developers/applications))
* Channel IDs and Guild ID for where the bot should post
* An [Upstash Redis](https://upstash.com/) instance (free tier is sufficient) — shared state for the primary/standby pair

## Setup

### 🐳 Docker (Recommended for most users)

The bot is now available as a pre-built image on GitHub Container Registry. This is the easiest way to run the bot with all scientific dependencies (MetPy, SounderPy) pre-configured.

1.  **Download configuration:**
    ```bash
    mkdir spc-bot && cd spc-bot
    curl -O https://raw.githubusercontent.com/full-bars/spc-bot/main/docker-compose.yml
    curl -O https://raw.githubusercontent.com/full-bars/spc-bot/main/.env.example
    cp .env.example .env
    ```
2.  **Configure:** Edit `.env` with your Discord token and channel IDs.
3.  **Launch:** `docker compose up -d`

### ⚡ Automatic Install (Linux / Ubuntu)

A portable deploy script is included that creates a virtual environment, configures your `.env` interactively, and installs a systemd service. The bot runs as your current user for seamless code and log management.

```bash
git clone https://github.com/full-bars/spc-bot.git
cd spc-bot
sudo ./deploy.sh
```

The script will prompt you for your Discord bot token and setup the following aliases in your `.bashrc`:

```bash
spcon        # start the bot
spcoff       # stop the bot
spcrestart   # restart the bot
spcstatus    # show status dashboard
spclog       # follow live logs
spclog50     # show last 50 log lines
spcupdate    # pull latest code and restart
```

## Project Structure

```
spc-bot/
├── main.py                  # Bot entrypoint, watchdog, and signal handling
├── deploy.sh                # Portable one-command deployment script
├── Dockerfile               # Debian-based scientific stack image
├── docker-compose.yml       # Docker orchestration
├── install-hooks.sh         # Installs pre-push git hooks (syntax + test checks)
├── config.py                # Configuration and centralized URL constants
├── requirements.txt         # Runtime dependencies (what the bot needs to run)
├── requirements-dev.txt     # Runtime + pytest/pytest-asyncio/pytest-cov/ruff for development & CI
├── .env.example             # Template for required environment variables
├── CREDITS.md               # Third-party attributions
├── scripts/
│   └── migrate_sqlite_to_upstash.py  # One-shot migration of local SQLite into Upstash
├── utils/
│   ├── http.py              # Async HTTP session management (centralized pooling, retry, conditional GET)
│   ├── change_detection.py  # Content hashing and placeholder-image detection
│   ├── cache.py             # Download orchestration; conditional-GET poll path (validators persist across restarts)
│   ├── state.py             # BotState — HashStore + PostingLog + TimingTracker sub-stores
│   ├── state_store.py       # Upstash Redis facade: read-through cache → Upstash → SQLite fallback;
│   │                        # double-writes both backends, retries failed Upstash writes via a reconciler
│   ├── spc_urls.py          # SPC outlook URL resolution
│   ├── backoff.py           # Exponential backoff tracker for task loops
│   └── db.py                # Async SQLite backend used internally by state_store as the durable mirror; also home of http_validators
├── cogs/
│   ├── outlooks.py          # SPC Day 1-3 and Day 4-8 auto-posting
│   ├── mesoscale.py         # SPC MD monitoring with watch probability detection and IEM fallbacks
│   ├── iembot.py            # IEM iembot feed poller with persistent text-product caching
│   ├── watches.py           # SPC watch monitoring via NWS API (stores affected_zones)
│   ├── scp.py               # NIU/Gensini SCP graphics, twice daily
│   ├── csu_mlp.py           # CSU-MLP consolidated /csu command with Choice dropdown
│   ├── ncar.py              # NCAR WxNext2 AI severe weather forecast
│   ├── sounding.py          # RAOB+ACARS sounding plots; auto-posts near active watches
│   ├── sounding_utils.py    # Location resolution, IEM fetch (all hours), ACARS fetch, plot generation
│   ├── sounding_views.py    # Discord UI: CombinedSoundingView, IEMTimeSelectionView, ACARS views
│   ├── hodograph.py         # VWP hodograph generation via /hodograph
│   ├── failover.py          # Leader election via an Upstash lease (no HTTP tunnel — v5+)
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
└── tests/                   # pytest suite (169+ tests, see CONTRIBUTING.md)
    ├── conftest.py          # Fixtures: fake_bot (real BotState), isolated_db, opt-in patches
    ├── test_fixtures.py     # Fixture invariants
    ├── test_utils.py        # Utility and sounding parsing
    ├── test_watches.py      # Watch VTEC parsing
    ├── test_integration.py  # BotState, cog instantiation, function signatures
    ├── test_state_split.py  # HashStore / PostingLog / TimingTracker delegation
    ├── test_state_store.py  # Upstash-backed state store (cache, reconciler, SQLite fallback)
    ├── test_db.py           # SQLite backend roundtrips
    ├── test_http.py         # HTTP retry + conditional GET
    ├── test_cache_conditional.py  # Partial-update poll with ETag/If-Modified-Since
    ├── test_backoff.py      # TaskBackoff delay and alert logic
    ├── test_main_lifecycle.py  # Shutdown guard, watchdog restart, startup smoke
    ├── test_failover_coverage.py  # Lease election, promotion, demotion
    ├── test_hodograph.py    # Hodograph cog
    └── test_iem_races.py    # IEM/SPC race logic and watch-triggered soundings
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
