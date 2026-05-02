# WxAlert / SPCBot

A Discord bot for severe weather enthusiasts. Auto-posts SPC convective outlooks, mesoscale discussions, and tornado/severe thunderstorm watches in real time. Includes a NEXRAD Level 2 radar downloader pulling from the NOAA AWS S3 archive and a VWP hodograph generator for any NEXRAD or TDWR site.

## Features

### SPC Products
| Feature | Details |
|---|---|
| Convective Outlooks | Day 1, 2, 3 and Day 4–8 with dynamic URL resolution |
| Mesoscale Discussions | Cancellation tracking, high-probability watch detection, proactive sounding pre-warming |
| SCP Graphics | NIU/Gensini CFSv2/GEFS supercell composite parameter maps, twice daily |
| CSU-MLP Forecasts | Days 1–8 + 6-panel summaries, auto-posted daily; `/csu` slash command with interactive dropdown |
| NCAR WxNext2 AI | Mean AI convective hazard Days 1–8, auto-posted daily; `/wxnext` slash command |

### Real-time Alerts
| Feature | Details |
|---|---|
| NWWS-OI (XMPP) | **Gold Standard** authority source; pushes raw NWS text products via XMPP with near-zero latency, beating API polling by up to 60s |
| Watch Alerts | Tornado and severe thunderstorm watches via NWWS/IEM fast-paths; NWS API backup; persistent DB-backed pre-caching |
| NWS Warnings | Immediate Tornado, Severe Tstorm, and Flash Flood warning posts with IEM Autoplot 208 maps; specialized PDS and Emergency formatting |
| Update Pipeline | Real-time tracking of warning status changes (`CON`, `EXT`, `EXA`); automatically posts concise updates for storms changing intensity or moving into new counties; includes full support for Severe Weather Statements (`SVS`) and Flash Flood Statements (`FFS`) |
| Tornado Dashboard | Single-card, chronological dashboard for `/recenttornadoes` and `/sigtor` with EF-rating distinctions, warning-to-report **Lead Time** tracking, and [Tornado Archive](https://tornadoarchive.com/) integration |
| Tornado Surveys | DAMAGE SURVEY PNS detection; Autoplot 253 tornado-track maps; automatic linking to [NWS Damage Assessment Toolkit (DAT)](https://apps.dat.noaa.gov/stormdamage/damageviewer/) tracks with an interactive **Photo Carousel** of official damage photos |

### Soundings & Analytics
| Feature | Details |
|---|---|
| `/sounding` | Observed RAOB plots via SounderPy; supports city names, radar codes, and station IDs with interactive time selection |
| Watch-triggered soundings | Auto-posts soundings for RAOB stations near active watches — on issuance (any hour via IEM) and at 00z/12z synoptic cycles |
| MDT/HIGH risk sweep | On Moderate or High Risk days sweeps every RAOB station and ACARS airport inside the categorical polygon (100 km buffer) as new soundings arrive |
| `/hodograph` | VWP hodograph for any of 200 NEXRAD/TDWR sites; auto ASOS surface wind and storm parameter table |
| Analytics Cog | Comprehensive suite including `/topstats` (leaderboards), `/verify` (storm-based warning metrics via IEM Cow), `/riskmap` (historical risk frequency), `/dayssince`, and `/tornadoheatmap` |
| Radar Downloader | NEXRAD Level 2 from NOAA AWS S3 — single or multi-site ZIPs; Z-to-Z range, start+duration, explicit datetime, or N most recent files |

### System
| Feature | Details |
|---|---|
| `/status` | Node roles (Primary/Standby), real-time task health, RSS memory usage, feed sync state |
| High availability | Leader election via Upstash lease; automatic Primary/Standby failover with no HTTP tunnel required |
| Watchdog | Dual-endpoint session probe (`api.weather.gov` + IEM); operator alerts to dev channel at 2/3 failures and on session reset |

## Prerequisites

* Python 3.12+ (matches the Dockerfile; 3.10/3.11 may still work but CI runs on 3.12)
* A Discord bot token and application ([Discord Developer Portal](https://discord.com/developers/applications))
* Channel IDs and Guild ID for where the bot should post
* An [Upstash Redis](https://upstash.com/) instance *(optional, free tier is sufficient)* — only required for Primary/Standby failover. The bot runs fine on a single node without it.

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

### ⚡ Automatic Install (systemd-based linux)

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

## High Availability (optional)

The bot supports an active/standby failover pair using Upstash Redis as a shared lease store. This section is only relevant if you want to run two nodes — a single install needs none of this.

### How it works

One node holds the Upstash lease and runs as **Primary** (posts to Discord, polls all feeds). The other runs as **Standby** (holds no lease, all cogs idle). If the Primary crashes or goes offline, the Standby detects the expired lease on its next heartbeat and promotes itself automatically — no manual intervention, no HTTP tunnel between nodes.

### Failover setup

1. **Create an Upstash Redis database** (free tier is enough) and note the REST URL and token.
2. **Set the following on both nodes** in `.env`:
   ```env
   UPSTASH_REDIS_REST_URL=https://your-upstash-url.upstash.io
   UPSTASH_REDIS_REST_TOKEN=your-upstash-token
   FAILOVER_TOKEN=some-long-random-shared-secret
   ADMIN_USER_ID=your_discord_user_id
   ```
3. **Set the initial role** on each node:
   ```env
   IS_PRIMARY=true   # on the Primary
   IS_PRIMARY=false  # on the Standby
   ```
   The bot uses leader election on every heartbeat regardless, so `IS_PRIMARY` only controls which cogs load at startup — it doesn't hard-lock a node to a role.
4. Start both nodes. Use `/failover` in Discord (restricted to `ADMIN_USER_ID`) to trigger a manual role swap at any time.

### Syncthing — events archive sync (optional)

The significant-weather events archive (`cache/events.db`) is a standalone SQLite file that never syncs to Upstash. To replicate it across nodes so the Standby has a current copy if it promotes:

1. **Install [Syncthing](https://syncthing.net/)** on both nodes and pair them.
2. **Create a shared folder** pointing to the `cache/events_sync/` directory on each node. Note the folder ID Syncthing assigns.
3. **Add to `.env`** on both nodes:
   ```env
   SYNCTHING_API_KEY=your_local_syncthing_api_key
   SYNCTHING_FOLDER_ID=your-folder-id
   ```
4. The bot manages folder mode automatically — it sets the folder to **send-only** on the Primary and **receive-only** on the Standby, and flips the mode on promotion/demotion. No manual Syncthing configuration beyond pairing is required.

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
│   ├── events_db.py         # Standalone SQLite archive for significant events (tornadoes, hail, wind);
│   │                        # separate from bot_state.db, never synced to Upstash
│   ├── spc_urls.py          # SPC outlook URL resolution
│   ├── spc_outlook.py       # SPC Day 1 categorical polygon (MDT/HIGH) with geodesic buffer
│   ├── backoff.py           # Exponential backoff tracker for task loops
│   └── db.py                # Async SQLite backend used internally by state_store as the durable mirror; also home of http_validators
├── cogs/
│   ├── outlooks.py          # SPC Day 1-3 and Day 4-8 auto-posting
│   ├── mesoscale.py         # SPC MD monitoring with watch probability detection and IEM fallbacks
│   ├── iembot.py            # IEM iembot feed poller with persistent text-product caching
│   ├── watches.py           # SPC watch monitoring via NWS API (stores affected_zones)
│   ├── warnings.py          # NWS VTEC warning monitoring (SVR, TOR, FFW) with map mapping
│   ├── reports.py           # LSR and PNS monitoring; triggers Autoplot 253 tornado track posts
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
└── tests/                   # pytest suite (346 tests, see CONTRIBUTING.md)
    ├── conftest.py          # Fixtures: fake_bot (real BotState), isolated_db, global patches
    ├── test_fixtures.py     # Fixture invariants
    ├── test_utils.py        # Utility and sounding parsing
    ├── test_watches.py      # Watch VTEC parsing
    ├── test_warnings.py     # Warning VTEC and LAT...LON polygon parsing
    ├── test_surveys.py      # PNS date extraction and Autoplot 253 polling
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
    ├── test_iem_races.py    # IEM/SPC race logic and watch-triggered soundings
    ├── test_spc_outlook.py  # Day 1 categorical polygon parsing + geodesic buffer
    ├── test_iembot.py       # IEMBotCog seqnum persistence, feed filtering, dispatch paths
    └── test_mesoscale.py    # MesoscaleCog MD cancellation, lag protection, year wraparound
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
