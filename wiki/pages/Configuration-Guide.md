# Configuration Guide ⚙️

SPCBot is configured via a `.env` file in the project root. Below is a comprehensive list of all supported environment variables.

## 🔑 Required

| Variable | Description | Default |
|---|---|---|
| `DISCORD_TOKEN` | Your Discord bot token. | Required |
| `GUILD_ID` | The ID of the Discord server where the bot operates. | Required |
| `SPC_CHANNEL_ID` | Default channel for SPC outlooks, watches, MDs, reports, and health fallback posts. | Required |
| `MODELS_CHANNEL_ID` | Channel for model/science products such as CSU-MLP, WxNext2, and SCP. | Required |
| `FAILOVER_TOKEN` | Non-default shared secret used by failover/admin controls. | Required |

## 📡 Alerting & Data Sources

| Variable | Description | Default |
|---|---|---|
| `NWWS_USER` | NWWS-OI XMPP username. | (empty) |
| `NWWS_PASSWORD` | NWWS-OI XMPP password. | (empty) |
| `NWWS_SERVER` | NWWS-OI XMPP server address. | `nwws-oi.weather.gov` |
| `WARNINGS_CHANNEL_ID` | Channel for TOR/SVR/FFW alerts. | `SPC_CHANNEL_ID` |
| `TOR_CHANNEL_ID` | Dedicated channel for Tornado Warning (TOR) posts. | `WARNINGS_CHANNEL_ID` |
| `SVR_CHANNEL_ID` | Dedicated channel for Severe Thunderstorm Warning (SVR) posts. | `WARNINGS_CHANNEL_ID` |
| `FFW_CHANNEL_ID` | Dedicated channel for Flash Flood Warning (FFW) posts. | `WARNINGS_CHANNEL_ID` |
| `SPS_CHANNEL_ID` | Dedicated channel for Special Weather Statement (SPS) posts. | `WARNINGS_CHANNEL_ID` |
| `HEALTH_CHANNEL_ID` | Channel for health alerts and watchdog notifications. | `SPC_CHANNEL_ID` |
| `SOUNDING_CHANNEL_ID` | Channel for automated sounding posts. | `SPC_CHANNEL_ID` |
| `DEV_CHANNEL_ID` | Channel for developer/admin operational alerts. | `HEALTH_CHANNEL_ID`, then `SPC_CHANNEL_ID` |

**Warning-type channel fallback chain:** For each warning type the bot resolves the destination as: type-specific ID (`TOR_CHANNEL_ID`, `SVR_CHANNEL_ID`, `FFW_CHANNEL_ID`, `SPS_CHANNEL_ID`) → `WARNINGS_CHANNEL_ID` → `SPC_CHANNEL_ID`. These IDs can also be changed at runtime without a restart via `/enablewarnings`, `/disablewarnings`, and `/displaysetup`.

## 🔄 High Availability (Failover)

| Variable | Description | Default |
|---|---|---|
| `IS_PRIMARY` | Set initial role (`true`/`false`). | `true` |
| `REDIS_URL` | Redis connection URL. Preferred over individual host/port vars. | `redis://localhost:6379/0` |
| `REDIS_HOST` | Redis host (alternative to `REDIS_URL`). | `localhost` |
| `REDIS_PORT` | Redis port (alternative to `REDIS_URL`). | `6379` |
| `REDIS_DB` | Redis database number (alternative to `REDIS_URL`). | `0` |
| `ELECTION_REDIS_URL` | **Standby nodes only.** Points the leader-election client at the primary node's Redis (e.g. via Tailscale). Connection failures here trigger promotion. Leave unset on the primary. | (empty — falls back to `REDIS_URL`) |
| `ADMIN_USER_ID` | Discord user ID authorized to use `/failover`. | `0` |

## 💾 Persistence & Sync

| Variable | Description | Default |
|---|---|---|
| `CACHE_DIR` | Path to store DBs and image caches. | `cache/` |
| `EVENTS_DB_PATH` | Path to the confirmed tornado and forensics archive. | `cache/events.db` |
| `EVENTS_SYNC_DIR` | Directory used for Syncthing `events.db` exchange. | `cache/events_sync` |
| `LOG_FILE` | Main bot log path. | `spc_bot.log` |
| `NWWS_FIREHOSE_LOG` | Rotating raw NWWS firehose log path inside `CACHE_DIR`. | `nwws_firehose.log` |
| `MANUAL_CACHE_FILE` | Legacy manual hash cache filename. | `posted_records.json` |
| `AUTO_CACHE_FILE` | Legacy automatic hash cache filename. | `auto_posted_records.json` |
| `SYNCTHING_API_KEY` | Local Syncthing API key for `events.db` sync. | (empty) |
| `SYNCTHING_FOLDER_ID` | Syncthing folder ID for `events.db`. | `spcbot-events` |
| `RCLONE_REMOTE` | rclone remote name for off-server backups. | `gdrive` |
| `RCLONE_DEST_DIR` | Destination directory on the rclone remote. | `spc-bot-forensics` |

## 🖥️ System

| Variable | Description | Default |
|---|---|---|
| `PYTHONUNBUFFERED` | Ensures logs stream immediately in Docker. | `1` |

## Deployment Profiles

| Profile | Required | Optional |
|---|---|---|
| Single node | `DISCORD_TOKEN`, `GUILD_ID`, `SPC_CHANNEL_ID`, `MODELS_CHANNEL_ID`, `FAILOVER_TOKEN` | NWWS credentials for lower-latency text products |
| Single node with split channels | Single-node variables plus any channel override IDs | `HEALTH_CHANNEL_ID`, `WARNINGS_CHANNEL_ID`, `SOUNDING_CHANNEL_ID`, `DEV_CHANNEL_ID` |
| High availability | Single-node variables plus `REDIS_URL`, `FAILOVER_TOKEN`, `ADMIN_USER_ID`, and opposite `IS_PRIMARY` values on each node; standby also needs `ELECTION_REDIS_URL` pointing at the primary's Redis | Syncthing for `events.db` replication |
| Tornado forensics archive | Single-node variables plus `RCLONE_REMOTE` and `RCLONE_DEST_DIR` | Syncthing if also running HA |
