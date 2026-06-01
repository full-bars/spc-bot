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
- **Startup/Periodic Cache Pruning:** `utils.cache_utils` removes root cache files older than 7 days after startup and every 24 hours.
- **Primary Maintenance Loop:** `cogs/maintenance.py` removes root cache image/temp/hash files older than 48 hours.
- **Forensics Management:** Automatically prunes orphaned VAD recording mission directories and enforces a **1GB budget** for archived GIFs, deleting oldest files first.
- **Events Retention:** Enforces a **365-day rolling retention** for the tornado-only `events.db` archive and backfills missing DAT GUIDs.
- **Radar Cleanup:** The radar downloader removes temporary files from `radar_data/` after 24 hours.

## ☁️ Off-Server Backups (rclone)

To prevent local disk pressure from high-resolution VAD evolution GIFs, the bot supports automated off-server backups via **rclone**.

### Setup
1. **Install rclone**: The bot expects `rclone` to be available in the system PATH.
2. **Configure Remote**: Run `rclone config` to set up your target provider (e.g., Google Drive, AWS S3, B2).
3. **Set environment variables**:
   - `RCLONE_REMOTE`: The name of the remote you configured (e.g., `gdrive`).
   - `RCLONE_DEST_DIR`: The target directory on that remote.

Finalized GIFs are uploaded asynchronously after each recording mission. Local files are still managed by the 1GB budget cleanup task, but the remote copy provides a permanent meteorological archive.

## 🔄 Operations

### Failover Manual Swap
If you need to perform maintenance on the Primary node, use `/failover`.
- The command opens an interactive selector listing active nodes from the local Redis node registry.
- Choosing a node stores a manual primary override for that hostname.
- Clearing the override returns the pair to automatic lease-based failover.

### Update Pipeline
For users running via `deploy.sh`, the `spcupdate` alias:
1. Performs a `git pull`.
2. Checks for new dependencies (`pip install -r requirements.txt`).
3. Restarts the systemd service.

## 🚨 Incident Runbooks

### NWWS-OI Disconnected
Symptoms: `/status` shows NWWS disconnected or stale ping/throughput.

1. Confirm the bot is still logged in and marked `PRIMARY`.
2. Check `/logs` for XMPP authentication, roster, or reconnect errors.
3. Confirm `NWWS_USER`, `NWWS_PASSWORD`, and `NWWS_SERVER` are set correctly.
4. Keep IEMBot/API polling online as fallback; do not restart both HA nodes at once during active weather.

### Redis Unavailable
Symptoms: failover lease warnings or standby uncertainty in `/logs`.

1. Check `/status` on both nodes and identify which node is posting.
2. Verify `ELECTION_REDIS_URL` points to a reachable Redis instance.
3. Confirm Redis is running: `systemctl status redis` (or `redis-cli ping`).
4. SQLite continues local durability while Redis is down — the node will not crash or stop posting.
5. Avoid manual role swaps unless the current primary is clearly down.

### Split-Brain Suspicion
Symptoms: both nodes appear to post or both report `PRIMARY`.

1. Use `/status` on both nodes and compare hostname/IP/role.
2. Stop or demote the less healthy node first.
3. Confirm only one node loads posting cogs and runs auto-post loops.
4. Check recent MD/watch/warning posts for duplicates before re-enabling the second node.

### Discord Command Sync Failures
Symptoms: slash commands are missing, stale, or return interaction errors.

1. Confirm the primary node completed startup and command sync.
2. Check `/logs` for Discord HTTP errors or permission failures.
3. Confirm the bot is invited with `applications.commands` scope.
4. Restart only the primary if command sync failed during startup.

### Sounding Queue Saturation
Symptom: users see `Plot Queued (Position X)...` instead of an immediate sounding image.

The sounding renderer uses a dedicated `Heavy Sounding` worker pool (4 concurrent processes) separate from the `Fast Hodo` radar pool. When all workers are busy, new requests are queued and users receive a position message. The queue auto-clears as workers finish — no operator action is needed. A 60-second render timeout prevents deadlock; requests that exceed the timeout are discarded and the user sees an error. If queuing is persistent, the server may be under unusually high sounding load.

### Cache Disk Pressure
Symptoms: large `cache/`, slow image/radar commands, or filesystem warnings.

1. Check `cache/`, `cache/event_archive/`, and radar download output size.
2. Confirm the maintenance loop is running in `/taskmgr`.
3. Manually archive important GIFs before deleting local forensic files.
4. Validate rclone backups before lowering local retention.

### SQLite Corruption
Symptoms: integrity check failures or startup messages about recreating `bot_state.db`.

1. Preserve the `.corrupted` database file for inspection.
2. Confirm the replacement DB starts cleanly and that Redis replication is still in sync on standby.
3. For HA setups, verify `events.db` replication before promoting a standby.
4. If duplicate posts appear, reconcile posted MD/watch/warning state before resuming automation.

## 🧭 Cache & Backup Guide

| Path | Purpose | Backup Priority | Safe to Delete? |
|---|---|---|---|
| `cache/bot_state.db` | Operational dedupe and bot state mirror. | Medium; Redis replication keeps standby in sync. | No, unless intentionally resetting state. |
| `cache/events.db` | Historical confirmed tornado and forensics archive. | High. | No. |
| `cache/event_archive/` | VAD evolution GIF archive. | High if not backed up remotely. | Only after backup/retention decision. |
| `cache/vad_recordings/` | Temporary active recording missions. | Low after mission finalization. | Old orphaned dirs are pruned automatically. |
| Cached images/radar downloads | Re-downloadable product/cache artifacts. | Low. | Usually yes when the bot is stopped. |
