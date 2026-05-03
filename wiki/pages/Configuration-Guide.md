# Configuration Guide ⚙️

SPCBot is configured via a `.env` file in the project root. Below is a comprehensive list of all supported environment variables.

## 🔑 Required

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Your Discord bot token. |
| `GUILD_ID` | The ID of the Discord server where the bot operates. |
| `LOG_CHANNEL_ID` | Channel ID for system alerts and errors. |

## 📡 Alerting & Data Sources

| Variable | Description | Default |
|---|---|---|
| `NWWS_USER` | NWWS-OI XMPP username. | (empty) |
| `NWWS_PASSWORD` | NWWS-OI XMPP password. | (empty) |
| `NWWS_SERVER` | NWWS-OI XMPP server address. | `nwws-oi.weather.gov` |
| `WARNINGS_CHANNEL_ID` | Channel for TOR/SVR/FFW alerts. | (Required) |
| `OUTLOOKS_CHANNEL_ID` | Channel for SPC outlook posts. | (Required) |

## 🔄 High Availability (Failover)

| Variable | Description | Default |
|---|---|---|
| `IS_PRIMARY` | Set initial role (`true`/`false`). | `true` |
| `UPSTASH_REDIS_REST_URL` | Your Upstash Redis REST URL. | (empty) |
| `UPSTASH_REDIS_REST_TOKEN` | Your Upstash Redis REST token. | (empty) |
| `FAILOVER_TOKEN` | Shared secret for `/failover` command auth. | (Required for HA) |
| `ADMIN_USER_ID` | Your Discord User ID (for owner-only commands). | (Required) |

## 💾 Persistence & Sync

| Variable | Description | Default |
|---|---|---|
| `CACHE_DIR` | Path to store DBs and image caches. | `cache/` |
| `EVENTS_DB_PATH` | Path to the historical events archive. | `cache/events.db` |
| `SYNCTHING_API_KEY` | Local Syncthing API key for `events.db` sync. | (empty) |
| `SYNCTHING_FOLDER_ID` | Syncthing folder ID for `events.db`. | `spcbot-events` |

## 🧪 Science & Thresholds

| Variable | Description | Default |
|---|---|---|
| `SOUNDING_TRIGGER_RADIUS` | Radius (km) to find stations near watches. | `150` |
| `MAX_RADAR_FILES` | Max files allowed per `/download` request. | `50` |
| `AGGRESSIVE_SPC_POLL` | Use faster polling on High Risk days. | `true` |

## 🖥️ System

| Variable | Description | Default |
|---|---|---|
| `LOG_LEVEL` | Logging verbosity (`DEBUG`, `INFO`, `ERROR`). | `INFO` |
| `PYTHONUNBUFFERED` | Ensures logs stream immediately in Docker. | `1` |
