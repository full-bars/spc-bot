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
- **Forensics Management:** Automatically prunes orphaned VAD recording mission directories and enforces a **1GB budget** for archived GIFs, deleting oldest files first.
- **DB Retention:** Enforces a **365-day rolling retention** for the `events.db` archive and prunes ephemeral state from `bot_state.db`.
- **Photo Cleanup:** Deletes cached DAT damage photos older than 30 days.

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
- The bot will gracefully demote the current node and allow the Standby to take over within 10–20 seconds.
- **Force Hostname:** You can optionally specify a target hostname to ensure the correct node promotes.

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

### Upstash Unavailable
Symptoms: failover lease warnings, dirty-write reconciliation logs, or standby uncertainty.

1. Check `/status` on both nodes and identify which node is posting.
2. Verify `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`.
3. Let SQLite mirror continue local durability; dirty writes are replayed when Upstash returns.
4. Avoid manual role swaps until the lease store recovers unless the current primary is clearly down.

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

### Cache Disk Pressure
Symptoms: large `cache/`, slow image/radar commands, or filesystem warnings.

1. Check `cache/`, `cache/event_archive/`, and radar download output size.
2. Confirm the maintenance loop is running in `/taskmgr`.
3. Manually archive important GIFs before deleting local forensic files.
4. Validate rclone backups before lowering local retention.

### SQLite Corruption
Symptoms: integrity check failures or startup messages about recreating `bot_state.db`.

1. Preserve the `.corrupted` database file for inspection.
2. Confirm the replacement DB starts and rehydrates from Upstash where available.
3. For HA setups, verify `events.db` replication before promoting a standby.
4. If duplicate posts appear, reconcile posted MD/watch/warning state before resuming automation.

## 🧭 Cache & Backup Guide

| Path | Purpose | Backup Priority | Safe to Delete? |
|---|---|---|---|
| `cache/bot_state.db` | Operational dedupe and bot state mirror. | Medium; Upstash can rehydrate some state. | No, unless intentionally resetting state. |
| `cache/events.db` | Historical significant events archive. | High. | No. |
| `cache/event_archive/` | VAD evolution GIF archive. | High if not backed up remotely. | Only after backup/retention decision. |
| `cache/vad_recordings/` | Temporary active recording missions. | Low after mission finalization. | Old orphaned dirs are pruned automatically. |
| Cached images/radar downloads | Re-downloadable product/cache artifacts. | Low. | Usually yes when the bot is stopped. |
