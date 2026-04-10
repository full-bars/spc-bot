# WxAlert / SPCBot

A Discord bot for severe weather enthusiasts. Auto-posts SPC convective outlooks, mesoscale discussions, and tornado/severe thunderstorm watches in real time. Includes a NEXRAD Level 2 radar downloader pulling from the NOAA AWS S3 archive and a VWP hodograph generator for any NEXRAD or TDWR site.

## Features

* SPC convective outlooks (Day 1, 2, 3, Day 4-8) with dynamic URL resolution
* SPC mesoscale discussions with cancellation tracking
* Tornado and severe thunderstorm watch alerts via NWS API
* NIU/Gensini CFSv2/GEFS supercell composite parameter (SCP) graphics, twice daily
* CSU-MLP machine learning severe weather forecasts (Days 1-8 + 6-panel summaries), auto-posted daily with `/csu1`-`/csu8`, `/csupanel12`, and `/csupanel38` slash commands
* NCAR WxNext2 Mean AI convective hazard forecast (Days 1-8), auto-posted daily with `/wxnext` slash command
* VWP hodograph generation for any NEXRAD or TDWR site (200 sites) via `/hodograph`, with auto ASOS surface wind and storm parameter table
* NEXRAD Level 2 radar downloader from NOAA AWS S3
  * Single or multi-site downloads with per-site ZIP packaging
  * Z-to-Z range, start+duration, explicit datetime, or N most recent files

## Prerequisites

* Python 3.10+
* A Discord bot token and application ([Discord Developer Portal](https://discord.com/developers/applications))
* Channel IDs for where the bot should post

## Setup

### Automatic (recommended)

A deploy script is included that creates a virtual environment, configures your `.env` interactively, creates a dedicated non-root system user, and installs a systemd service that starts automatically on boot.

```bash
git clone https://github.com/full-bars/spc-bot.git
cd spc-bot
sudo ./deploy.sh
```

The script will prompt you for your Discord bot token and channel IDs, then handle everything else.


### Manual



1. Clone the repository:
   ```
   git clone https://github.com/full-bars/spc-bot.git
   cd spc-bot
   ```

2. Create and activate a virtual environment:
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
├── config.py                # Configuration from environment variables
├── requirements.txt         # Python dependencies
├── .env.example             # Template for required environment variables
├── CREDITS.md               # Third-party attributions
├── utils/
│   ├── http.py              # Async HTTP session management
│   ├── persistence.py       # Atomic JSON load/save helpers
│   ├── change_detection.py  # HEAD-based change detection, hashing
│   ├── cache.py             # In-memory state, download orchestration
│   └── spc_urls.py          # SPC outlook URL resolution
├── cogs/
│   ├── outlooks.py          # SPC Day 1-3 and Day 4-8 auto-posting
│   ├── mesoscale.py         # SPC Mesoscale Discussion monitoring
│   ├── watches.py           # SPC Watch monitoring via NWS API
│   ├── scp.py               # NIU/Gensini SCP graphics, twice daily
│   ├── csu_mlp.py           # CSU-MLP ML severe weather forecasts, Days 1-8 and 6-panels
│   ├── ncar.py              # NCAR WxNext2 AI severe weather forecast
│   ├── hodograph.py         # VWP hodograph generation via /hodograph
│   ├── status.py            # Bot status and manual slash commands
│   └── radar/
│       ├── __init__.py      # Radar cog registration
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
    ├── test_utils.py        # Unit tests for utilities
    ├── test_watches.py      # Unit tests for watch VTEC parsing
    └── test_hodograph.py    # Unit tests for hodograph cog
```

## Status

Work in progress. Actively developed in my free time, expect some bugs.

## Built With

* [discord.py](https://github.com/Rapptz/discord.py)
* [aiohttp](https://github.com/aio-libs/aiohttp)
* [boto3](https://github.com/boto/boto3)
* [numpy](https://numpy.org)
* [matplotlib](https://matplotlib.org)
* [requests](https://requests.readthedocs.io)
* [vad-plotter](https://github.com/tsupinie/vad-plotter) by Tim Supinie
