# Getting Started 🛠️

SPCBot is designed for flexibility, supporting both containerized and native Linux deployments.

## 📋 Prerequisites

- **Python 3.12+** (matches production stack).
- **Discord Bot Token**: Create one at the [Discord Developer Portal](https://discord.com/developers/applications).
- **Upstash Redis** (Optional): Required only for High Availability (Failover).
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

## ⚡ Native Linux (systemd)

Use the portable deploy script for an interactive setup on Ubuntu/Debian.

```bash
git clone https://github.com/full-bars/spc-bot.git
cd spc-bot
sudo ./deploy.sh
```

The script creates a virtual environment, configures your service, and installs aliases (`spcon`, `spcoff`, `spclog`) into your `.bashrc`.

## ⚙️ Core Configuration (`.env`)

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Your bot token from Discord. |
| `GUILD_ID` | The ID of your primary server. |
| `LOG_CHANNEL_ID` | Channel for system alerts and errors. |
| `NWWS_USER` | NWWS-OI XMPP username. |
| `NWWS_PASSWORD` | NWWS-OI XMPP password. |
| `IS_PRIMARY` | Set `true` for your main node. |

For a full list of configuration options, see the [Configuration Guide](Configuration-Guide).
