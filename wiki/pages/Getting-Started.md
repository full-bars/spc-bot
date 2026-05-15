# Getting Started 🛠️

SPCBot is designed for flexibility, supporting both containerized and native Linux deployments.

## 📋 Prerequisites

- **Python 3.13+** (matches current production stack).
- **Discord Bot Token**: Create one at the [Discord Developer Portal](https://discord.com/developers/applications).
- **Discord Channel IDs**: At minimum, one SPC channel and one model channel.
- **Redis** (Optional): Required only for High Availability (Failover). A local Redis instance on each node is recommended; no external service is needed.
- **Syncthing** (Optional): Required for cross-node `events.db` replication.

## 🐳 Docker Deployment (Recommended)

The easiest way to run SPCBot with all scientific dependencies (MetPy, Cartopy) pre-installed.

1. **Setup Directory:**
   ```bash
   mkdir spc-bot && cd spc-bot
   curl -O https://raw.githubusercontent.com/full-bars/spc-bot/main/docker-compose.yml
   curl -O https://raw.githubusercontent.com/full-bars/spc-bot/main/.env.example
   cp .env.example .env
   ```
2. **Configure:** Edit `.env` with your token and channel IDs.
3. **Launch:** `docker compose up -d`

Check startup logs with:

```bash
docker compose logs -f bot
```

## ⚡ Native Linux (systemd)

Use the portable deploy script for an interactive setup on Ubuntu/Debian.

```bash
git clone https://github.com/full-bars/spc-bot.git
cd spc-bot
sudo ./deploy.sh
```

The script creates a virtual environment, configures your service, and installs aliases (`spcon`, `spcoff`, `spcstatus`, `spclog`, `spcupdate`) into your `.bashrc`.

## ⚙️ Core Configuration (`.env`)

Minimum required variables for a single-node install:

| Variable | Description | Required |
|---|---|---|
| `DISCORD_TOKEN` | Your bot token from Discord. | Yes |
| `GUILD_ID` | The ID of your primary server. | Yes |
| `SPC_CHANNEL_ID` | Default channel for SPC outlooks, watches, MDs, and fallback health posts. | Yes |
| `MODELS_CHANNEL_ID` | Channel for model/science products such as CSU-MLP, WxNext2, and SCP. | Yes |
| `FAILOVER_TOKEN` | Non-default shared secret used by failover/admin controls. | Yes |

Optional production features:

| Feature | Variables |
|---|---|
| Warning channel split | `WARNINGS_CHANNEL_ID`, `HEALTH_CHANNEL_ID`, `SOUNDING_CHANNEL_ID`, `DEV_CHANNEL_ID` |
| NWWS-OI fast path | `NWWS_USER`, `NWWS_PASSWORD`, `NWWS_SERVER` |
| High availability | `IS_PRIMARY`, `ELECTION_REDIS_URL`, `ADMIN_USER_ID` |
| Events DB sync | `EVENTS_DB_PATH`, `EVENTS_SYNC_DIR`, `SYNCTHING_API_KEY`, `SYNCTHING_FOLDER_ID` |
| Off-server GIF backups | `RCLONE_REMOTE`, `RCLONE_DEST_DIR` |

For a full list of configuration options, see the [Configuration Guide](Configuration-Guide).

## ✅ Startup Verification

After launch, confirm:

1. Logs show the bot logged in and slash commands synced.
2. `/status` shows the expected node role, uptime, Discord gateway, and data-source latency fields.
3. `/spc1` or `/wxnext` can post in the expected channel.
4. If using HA, only one node shows `PRIMARY`; the standby should not run posting loops.
