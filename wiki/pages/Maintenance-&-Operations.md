# Maintenance & Operations 🛠️

SPCBot is built for long-term, stable operation. It includes several built-in tools for monitoring health and performing maintenance.

## 📊 Monitoring Tools

### `/status` (The Dashboard)
The primary observability tool for all users (or restricted to staff).
- **Auto-Refresh:** Updates every 5 seconds with live data.
- **Network Latency:** Tracks `NWWS-OI`, `IEMBot`, and `HTTP` latency in real time.
- **System Info:** Shows node role (Primary/Standby), uptime, and RSS memory usage.

### `/taskmgr` (Owner-Only)
An "htop-style" manager for background loops.
- **Health Checks:** Shows the status (🟢/🔴) of every background task (e.g., `auto_post_spc`, `sync_loop`).
- **Iteration Tracking:** Displays the time until the next scheduled run for every loop.

### `/logs` (Owner-Only)
A virtual terminal console inside Discord.
- **Live Stream:** Streams the last 20-30 lines of the bot's console log with 5-second auto-refresh.
- **Formatting:** Uses Discord's `ansi` code blocks to preserve log highlighting.

## 🧹 Automated Maintenance

The bot performs several background cleanup tasks every 24 hours:
- **Cache Pruning:** Deletes old SPC/WPC images and temporary radar downloads.
- **DB Retention:** Enforces a **365-day rolling retention** for the `events.db` archive and prunes ephemeral state from `bot_state.db`.
- **Photo Cleanup:** Deletes cached DAT damage photos older than 30 days.

## 🔄 Operations

### Failover Manual Swap
If you need to perform maintenance on the Primary node, use `/failover`.
- The bot will gracefully demote the current node and allow the Standby to take over within 10–20 seconds.
- **Force Hostname:** You can optionally specify a target hostname to ensure the correct node promotes.

### Update Pipeline
For users running via `deploy.sh`, the `spcupdate` alias:
1. Performs a `git pull`.
2. Checks for new dependencies (`pip install -r requirements.txt`).
3. Restarts the systemd service.
