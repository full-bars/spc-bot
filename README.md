# WxAlert / SPCBot

A Discord bot for severe weather enthusiasts. Auto-posts SPC convective outlooks, mesoscale discussions, and tornado/severe thunderstorm watches in real time. Includes a NEXRAD Level 2 radar downloader pulling from the NOAA AWS S3 archive with flexible time range selection.

## Features

* SPC convective outlooks (Day 1, 2, 3, Day 4-8) with dynamic URL resolution
* SPC mesoscale discussions with cancellation tracking
* Tornado and severe thunderstorm watch alerts via NWS API
* NIU/Gensini CFSv2/GEFS supercell composite parameter (SCP) graphics, twice daily
* NEXRAD Level 2 radar downloader from NOAA AWS S3
  * Single or multi-site downloads with per-site ZIP packaging
  * Z-to-Z range, start+duration, explicit datetime, or N most recent files

## Setup

1. Clone the repository:
   ```
   git clone https://github.com/full-bars/spc-bot.git
   cd spc-bot
   ```

2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env` and fill in your values:
   ```
   cp .env.example .env
   ```

4. Run the bot:
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
├── config.py                # Configuration from environment variables
├── requirements.txt         # Python dependencies
├── .env.example             # Template for required environment variables
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
│   ├── scp.py               # NIU/Gensini SCP graphics posting
│   ├── status.py            # Bot status and manual slash commands
│   └── radar/
│       ├── __init__.py      # Radar cog registration
│       ├── s3.py            # S3 client, file listing, time parsing
│       ├── downloads.py     # Download orchestration, zipping, progress
│       └── views.py         # Discord UI views and modals
└── tests/
    ├── conftest.py          # Test environment setup
    └── test_utils.py        # Unit tests for utilities
```

## Status

Work in progress. Actively developed in my free time, expect some bugs.

## Built With

* [discord.py](https://github.com/Rapptz/discord.py)
* [aiohttp](https://github.com/aio-libs/aiohttp)
* [boto3](https://github.com/boto/boto3)
