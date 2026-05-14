# WxAlert / SPCBot

A high-performance severe weather monitoring platform with near-zero latency alerts, real-time warning lifecycle tracking, and automated scientific analysis. Uses **NWWS-OI (XMPP)** as the fastest warning path, with IEM/NWS API fallbacks, high-availability failover, and interactive Discord dashboards.

## Features

**SPC Products:** Day 1–8 convective outlooks, mesoscale discussions with watch probability detection, SPC watches, WPC rainfall outlooks, SCP, CSU-MLP, and NCAR WxNext2 forecasts.

**Real-Time Alerts:**
> [!IMPORTANT]  
> **Warning features are Beta.** While designed for ultra-low latency, this bot is for situational awareness only. Redundancy saves lives. Avoid a single point of failure by always maintaining multiple independent methods for receiving life-safety alerts. Recommended sources include: NOAA Radio (w/ battery backup), WEA mobile alerts, TV/Radio Broadcast. 

NWWS-OI fast path, IEMBot fallback, NWS API polling, SPC watch alerts with deduplication, NWS warnings (TOR/SVR/FFW/SPS) routable to dedicated per-type channels with runtime configuration via `/enablewarnings`, lifecycle tracking (CON/EXT/EXA), damage survey detection with DAT integration and photo carousels, automated 2.5h environmental evolution GIFs for observed tornadoes.

**Scientific Tools:** Interactive RAOB/ACARS sounding plots (auto-posted near active watches and MDT/HIGH risk areas), VWP hodographs, searchable tornado forensics archive, NEXRAD Level 2 downloader, and IEM-based tornado analytics.

**System:** Real-time `/status` dashboard with state monitoring, owner-only `/taskmgr` and `/logs` monitoring, dual-endpoint watchdog, leader-election failover with self-hosted or cloud Redis (Upstash), SQLite durability, optional Syncthing event archive replication, and a Rust hybrid core (Phases 1–8) that accelerates VAD hodograph math, VTEC parsing, XXH3 image hashing, NWWS product ID normalization, haversine distance queries, VAD storm-mode calculations (`shear_mag`, `sr_flow`, `clip_profile`), and batch spatial joins (`find_nearest_stations_batch`, `points_in_polygon_counts`). All Rust paths fall back to pure Python if the extension is unavailable.

## Quick Start

### Prerequisites
- Python 3.13+ or Docker
- Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- Discord channel IDs for SPC/model posts
- A non-default `FAILOVER_TOKEN` (required at startup)
- (Optional) Redis for high-availability failover: either self-hosted local Redis or [Upstash Cloud](https://upstash.com/)

### Docker (Recommended)
```bash
mkdir spc-bot && cd spc-bot
curl -O https://raw.githubusercontent.com/full-bars/spc-bot/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/full-bars/spc-bot/main/.env.example
cp .env.example .env
# Edit .env with your Discord token and channel IDs
docker compose up -d
```

Minimum required `.env` for a single-node install:
```env
DISCORD_TOKEN=your_bot_token_here
SPC_CHANNEL_ID=your_spc_channel_id
MODELS_CHANNEL_ID=your_models_channel_id
GUILD_ID=your_guild_id
FAILOVER_TOKEN=your_non_default_shared_secret
```

`ADMIN_USER_ID` is only required if you want to use the manual `/failover` control.

### Systemd (Linux)
```bash
git clone https://github.com/full-bars/spc-bot.git
cd spc-bot
sudo ./deploy.sh
```
Creates systemd service and bash aliases: `spcon`, `spcoff`, `spcstatus`, `spclog`, `spcupdate`.

## Optional Features

### High Availability (Primary/Standby Failover)
Run two nodes with automatic failover via Redis. Choose one of two deployment models:

**Option 1: Self-Hosted Local Redis (Recommended for Production)**
- Install Redis 7.0+ on primary server
- Configure replica with `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` in `.env`
- Redis replication handles all data sync automatically
- No external dependencies or API quotas
- Optimal performance and full control

**Option 2: Upstash Cloud Redis (Legacy/Cloud-First)**
- Managed service with no Redis infrastructure to manage
- Set `REDIS_UPSTASH_URL` and `REDIS_UPSTASH_TOKEN` in `.env`
- Works across cloud providers without VPN or Tailscale
- Subject to API quota limits (10k commands/day free tier)

Both require:
- Shared `FAILOVER_TOKEN` on both nodes
- `IS_PRIMARY=true` on primary, `IS_PRIMARY=false` on standby
- `ADMIN_USER_ID` on both nodes if you want manual `/failover` control

**See [High Availability & Failover](https://github.com/full-bars/spc-bot/wiki/High-Availability-&-Failover) in the wiki for complete setup and failover procedures.**

### Events Archive Sync (Syncthing)
Replicate the tornado events database (`cache/events.db`) across nodes for seamless standby promotion:
- Install [Syncthing](https://syncthing.net/) on both nodes
- Create a shared folder for `cache/events_sync/`
- Add `SYNCTHING_API_KEY` and `SYNCTHING_FOLDER_ID` to `.env`

The bot automatically manages folder modes (send-only on Primary, receive-only on Standby).

## Documentation

- **[PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md)** — Full file tree, module descriptions, architecture overview, testing guide
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Development setup, slash commands, auto-posting mechanics, persistence model, failover details, running tests
- **[docs/testing.md](docs/testing.md)** — Focused test-running guide for local development and CI troubleshooting
- **[GitHub Wiki](https://github.com/full-bars/spc-bot/wiki)** — Feature guides (Alerting Authority, Warning Lifecycle, Soundings, Maintenance)
- **[CHANGELOG.md](CHANGELOG.md)** — Release history and version-by-version improvements

## Status

Work in progress. Actively developed in free time; expect behavior to evolve between releases.

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
