# WxAlert / SPCBot

A high-performance severe weather monitoring platform with near-zero latency alerts, real-time warning lifecycle tracking, and automated scientific analysis. Uses **NWWS-OI (XMPP)** for <1s delivery of NWS products, with built-in high-availability failover and interactive Discord dashboards.

## Features

**SPC Products:** Day 1–8 convective outlooks, mesoscale discussions with watch probability detection, SCP/CSU-MLP/NCAR AI forecasts.

**Real-Time Alerts:**
> [!IMPORTANT]  
> **Warning features are Beta.** Designed for ultra low latency but should be considered experimental. Always rely on official NWS sources for life-safety decisions.

NWWS-OI (sub-1s latency), SPC watch alerts with deduplication, NWS warnings (TOR/SVR/FFW) with lifecycle tracking (CON/EXT/EXA), damage survey detection with DAT integration and photo carousels, automated 2.5h environmental evolution GIFs for observed tornadoes.

**Scientific Tools:** Interactive RAOB/ACARS sounding plots (auto-posted near active watches), VWP hodographs, searchable environmental archive, NEXRAD Level 2 downloader.

**System:** Real-time `/status` dashboard, automated `/taskmgr` and `/logs` monitoring, dual-endpoint watchdog, leader-election failover with Upstash Redis.

## Quick Start

### Prerequisites
- Python 3.12+ or Docker
- Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- Channel IDs for your Discord server
- (Optional) [Upstash Redis](https://upstash.com/) for high-availability failover

### Docker (Recommended)
```bash
mkdir spc-bot && cd spc-bot
curl -O https://raw.githubusercontent.com/full-bars/spc-bot/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/full-bars/spc-bot/main/.env.example
cp .env.example .env
# Edit .env with your Discord token and channel IDs
docker compose up -d
```

### Systemd (Linux)
```bash
git clone https://github.com/full-bars/spc-bot.git
cd spc-bot
sudo ./deploy.sh
```
Creates systemd service and bash aliases: `spcon`, `spcoff`, `spcstatus`, `spclog`, `spcupdate`.

## Optional Features

### High Availability (Primary/Standby Failover)
Run two nodes with automatic failover via Upstash Redis. No HTTP tunnel required. Requires:
- Upstash Redis instance
- `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`, `FAILOVER_TOKEN` in `.env` on both nodes
- `IS_PRIMARY=true` on primary, `IS_PRIMARY=false` on standby

**See [High Availability & Failover](https://github.com/full-bars/spc-bot/wiki/High-Availability-&-Failover) in the wiki for complete setup.**

### Events Archive Sync (Syncthing)
Replicate the tornado events database (`cache/events.db`) across nodes for seamless standby promotion:
- Install [Syncthing](https://syncthing.net/) on both nodes
- Create a shared folder for `cache/events_sync/`
- Add `SYNCTHING_API_KEY` and `SYNCTHING_FOLDER_ID` to `.env`

The bot automatically manages folder modes (send-only on Primary, receive-only on Standby).

## Documentation

- **[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)** — Full file tree, module descriptions, architecture overview, testing guide
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Development setup, slash commands, auto-posting mechanics, persistence model, failover details, running tests
- **[GitHub Wiki](https://github.com/full-bars/spc-bot/wiki)** — Feature guides (Alerting Authority, Warning Lifecycle, Soundings, Maintenance)
- **[CHANGELOG.md](CHANGELOG.md)** — Release history and version-by-version improvements

## Status

Work in progress. Actively developed in free time. All 328 tests passing with zero spurious warnings.

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
