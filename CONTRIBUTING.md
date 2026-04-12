# Contributing to WxAlert / SPCBot

This document covers the bot's internal architecture, slash command reference,
channel configuration, and operational behavior for contributors and operators.

---

## Channel Configuration

Two channel IDs are required in `.env`:

| Variable | Purpose |
|---|---|
| `SPC_CHANNEL_ID` | Receives all severe weather alerts — SPC outlooks (Days 1–3), Day 4–8 outlooks, mesoscale discussions, watch alerts and cancellations, and bot health alerts from the watchdog |
| `MODELS_CHANNEL_ID` | Receives model/forecast graphics — SCP twice-daily posts, CSU-MLP daily forecasts, and NCAR WxNext2 daily forecasts |

Slash commands can be used from any channel — they always respond ephemerally
or inline where invoked, not into the configured channels.

---

## Slash Command Reference

### SPC Outlooks
| Command | Description |
|---|---|
| `/spc1` | Fetch and display the latest SPC Day 1 outlook graphics. Optional `fresh:True` bypasses cache. |
| `/spc2` | Fetch and display the latest SPC Day 2 outlook graphics. Optional `fresh:True` bypasses cache. |
| `/spc3` | Fetch and display the latest SPC Day 3 outlook graphics. Optional `fresh:True` bypasses cache. |
| `/spc48` | Fetch and display the latest SPC Day 4–8 outlook graphics |

### Watches & Mesoscale Discussions
| Command | Description |
|---|---|
| `/watches` | Show all currently active SPC watches with details and probabilities |
| `/ww` | Alias for `/watches` |
| `/md` | Show the latest active SPC mesoscale discussion |

### Model Forecasts
| Command | Description |
|---|---|
| `/scp` | Show the latest NIU/Gensini SCP forecast graphics. Optional `fresh:True` bypasses cache. |
| `/csu` | Show CSU-MLP ML severe weather forecast — choose from Days 1–8, 6-Panel Days 1-2, or 6-Panel Days 3-8 via dropdown |
| `/wxnext` | Show the latest NCAR WxNext2 Mean AI convective hazard forecast |
| `/wpc` | Show the latest WPC Day 1–3 rainfall outlooks |

### Soundings
| Command | Description |
|---|---|
| `/sounding` | Plot an observed sounding — accepts city names, radar site codes (e.g. `KTLX`), or RAOB station IDs. Optional `time` (MM-DD-YYYY HHz, any hour supported via IEM) and `dark` (saves preference) parameters. Shows nearest RAOB stations with available times discovered via IEM, plus nearby ACARS aircraft profiles. |

### Radar
| Command | Description |
|---|---|
| `/download` | Open the NEXRAD Level 2 radar downloader UI. Optional `sites` (space or comma separated codes e.g. `KICT KUEX`), `time` (Last 1h/2h/3h/4h), and `count` (number of most recent files) for quick-start without interactive flow. |
| `/downloaderstatus` | Check AWS downloader and S3 latency |

### Status
| Command | Description |
|---|---|
| `/status` | Show bot health: task states, last auto-post times, partial update state, tracked MD/watch counts. Ephemeral. |

---

## How Auto-Posting Works

### SPC Outlooks (Days 1–3): Normal and Aggressive Check Mode

The outlook cog runs two loops concurrently:

**`auto_post_spc` (every 30 seconds)** — the normal loop. For each day (1, 2, 3)
it scrapes the SPC HTML page to resolve the current issuance-time PNG URLs, then
checks if the URLs have changed since the last post. If they have, it downloads
the images and posts them to `SPC_CHANNEL_ID`.

**Partial update detection** — sometimes the SPC page updates its tab URLs before
all images are actually available (returning placeholder content). When this
happens, the cog enters *partial update state* for that day: it records the new
URLs and the time it first saw them, but does not post yet.

**`aggressive_check_spc` (every 20 seconds)** — only runs when `partial_update_state`
is non-empty. It re-checks the affected days more frequently, attempting to
download the images until they are all non-placeholder. Once all images are
confirmed real, it posts and clears partial update state. If partial update state
persists beyond a timeout, the cog posts whatever it has and resets.

You can see which days are in partial update state via `/status`.

### SPC Day 4–8 Outlooks

Posted once daily when the SPC updates the Day 4–8 graphic. Uses HEAD-based
change detection on the static URL rather than HTML scraping.

### Mesoscale Discussions

The MD cog polls the SPC mesoscale discussion index every 60 seconds. It tracks
posted MD numbers in a persistent set and posts new ones as they appear. When an
MD is no longer listed on the index, it posts a cancellation embed.

### Watches

The watch cog runs every 2 minutes. It calls the NWS Alerts API as the primary
source. The return value has three distinct states:

- `None` — API call failed (HTTP error or bad JSON). The cycle is skipped
  entirely. `active_watches` is not modified, preventing false cancellations
  during a transient outage.
- `{}` — API succeeded and returned zero active watches. Normal processing
  continues — any watches in `active_watches` that are missing or expired will
  have cancellation embeds posted.
- `{...}` — one or more active watches. New ones are posted; expired or missing
  ones get cancellation embeds.

If the NWS API returns `None`, the `/watches` slash command falls back to
scraping the SPC watch index HTML directly.

### SCP Graphics

Posted at 6am and 6pm Pacific daily, but only if the images have actually
changed (hash-based detection). Uses `MODELS_CHANNEL_ID`.

### Sounding Plots

The `/sounding` command geocodes the location, finds nearby RAOB stations that have verified data in the Wyoming archive, and presents an interactive station and time picker. Plots are generated headlessly via SounderPy and posted to the channel where the command was used. Per-user dark mode preference is persisted to the local SQLite database. Auto-posting of soundings is active — when a new 00z or 12z sounding cycle becomes available, the bot checks for active SPC watches and posts soundings for up to 3 nearby RAOB stations per watch to `SPC_CHANNEL_ID`.

### CSU-MLP and NCAR WxNext2

Both poll once daily around model update time. State is persisted to the SQLite database so restarts don't cause duplicate posts.

---

## Task Backoff

The `TaskBackoff` class in `utils/backoff.py` provides per-task exponential backoff for the high-frequency polling loops (`auto_post_spc`, `auto_post_md`, `auto_post_watches`). When a loop cycle fails, subsequent cycles are skipped with increasing delays (0s, 0s, 30s, 60s, 120s, 300s). After 5 consecutive failures a non-critical health alert is posted. On success the counter resets.

---

## Watchdog

A `watchdog_task` loop runs every 2 minutes. It:

1. Probes the HTTP session with a lightweight request and recreates it if dead.
2. Checks every registered task. If a task has stopped, it restarts it.
3. After a threshold number of failures, posts a health alert embed to
   `SPC_CHANNEL_ID`. Watch and MD task failures are flagged as critical.

Tasks are registered with the watchdog in `main.py` after cogs load.

---

## Persistence

All persistent state lives in a single SQLite database at `CACHE_DIR/bot_state.db`, managed by `utils/db.py` using `aiosqlite`. The database uses WAL mode and a 5-second busy timeout for safe concurrent access.

| Table | Contents |
|---|---|
| `image_hashes` | URL → hash mapping for change detection (replaces JSON hash caches) |
| `posted_mds` | Set of posted MD numbers (pruned to last 200) |
| `posted_watches` | Set of posted watch numbers (pruned to last 200) |
| `bot_state` | Key/value store for CSU-MLP, NCAR, and sounding preferences |

On first startup, existing JSON files are automatically migrated into the database. The in-memory dicts (`auto_cache`, `manual_cache`, `posted_mds`, `posted_watches`) are kept as a fast lookup layer — the database is the persistence layer only.

If the database fails an integrity check on startup, it is renamed to `bot_state.db.corrupted` and recreated from scratch.

---

## Running Tests

```bash
pip install pytest pytest-asyncio
python -m pytest tests/ -v

---

## Failover Configuration

WxAlert supports a primary/standby failover architecture using Cloudflare tunnels and Upstash Redis for coordination.

### How it works
- **Primary** runs an aiohttp HTTP server on `STATE_PORT` (default 8765), starts a cloudflared tunnel, and writes the tunnel URL to Upstash Redis with a 7-minute TTL every 30 seconds.
- **Standby** polls Upstash every 30 seconds. If the primary URL is missing or unreachable for 3 consecutive checks (~90 seconds), it promotes itself, loads all cogs, starts its own tunnel, and writes its URL to Upstash.
- When the primary comes back online, it reads the standby's URL from Upstash, hydrates its state from the standby via `GET /state`, then writes its own URL. The standby detects the URL change and demotes itself, pushing accumulated state back to the primary via `POST /sync`.

### Required `.env` variables

| Variable | Primary | Standby |
|---|---|---|
| `IS_PRIMARY` | `true` | `false` |
| `FAILOVER_TOKEN` | shared secret | same shared secret |
| `STATE_PORT` | `8765` (default) | `8765` (default) |
| `UPSTASH_REDIS_REST_URL` | your Upstash URL | same |
| `UPSTASH_REDIS_REST_TOKEN` | your Upstash token | same |

### Dependencies
- `cloudflared` must be installed on both servers (handled by `deploy.sh`)
- A free Upstash Redis instance is sufficient (well within free tier limits)
