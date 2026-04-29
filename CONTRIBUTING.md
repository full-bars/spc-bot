# Contributing to WxAlert / SPCBot

This document covers the bot's internal architecture, slash command reference,
channel configuration, and operational behavior for contributors and operators.

---

## Channel Configuration

The following variables are required or optional in `.env`:

| Variable | Purpose |
|---|---|
| `GUILD_ID` | The Discord Server (Guild) ID where the bot should register its commands. |
| `SPC_CHANNEL_ID` | Receives all severe weather alerts — SPC outlooks (Days 1–3), Day 4–8 outlooks, mesoscale discussions, watch alerts and cancellations, and bot health alerts from the watchdog |
| `MODELS_CHANNEL_ID` | Receives model/forecast graphics — SCP twice-daily posts, CSU-MLP daily forecasts, and NCAR WxNext2 daily forecasts |
| `WARNINGS_CHANNEL_ID` | (Optional) Receives real-time NWS warning embeds (TOR, SVR, FFW, SPS) and damage survey posts. Defaults to `SPC_CHANNEL_ID` if not set. |
| `SOUNDING_CHANNEL_ID` | (Optional) Receives auto-posted sounding plots near active watches. Defaults to `SPC_CHANNEL_ID` if not set. |
| `HEALTH_CHANNEL_ID` | (Optional) Receives bot health alerts (watchdog degraded, task failures). Defaults to `SPC_CHANNEL_ID` if not set. |

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

### Watches, Warnings & Mesoscale Discussions
| Command | Description |
|---|---|
| `/watches` | Show all currently active SPC watches with details and probabilities |
| `/ww` | Alias for `/watches` |
| `/md` | Show the latest active SPC mesoscale discussion |
| `/significantwx` | View recent significant weather events (EF1+ tornadoes, significant hail/wind) from today's warnings and storm reports |
| `/recenttornadoes` | List confirmed tornadoes from recent warnings and reports |

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

### Radar & Hodograph
| Command | Description |
|---|---|
| `/download` | Open the NEXRAD Level 2 radar downloader UI. Optional `sites` (space or comma separated codes e.g. `KICT KUEX`), `time` (Last 1h/2h/3h/4h), and `count` (number of most recent files) for quick-start without interactive flow. |
| `/downloaderstatus` | Check AWS downloader and S3 latency |
| `/hodograph` | Generate a VWP hodograph for any NEXRAD or TDWR site. Accepts a 4-letter site ID (e.g. `KTLX`). Includes auto ASOS surface wind and storm parameter table. |

### Status & Admin
| Command | Description |
|---|---|
| `/status` | Show bot health: node role (Primary/Standby), task states, last auto-post times, partial update state, tracked MD/watch counts. Ephemeral. |
| `/help` | Show all available weather and bot commands. |
| `/failover` | (Admin only) Manually designate the Primary node. Requires `ADMIN_USER_ID` to be set in `.env`. |

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

### NWS Warnings (TOR / SVR / FFW / SPS)

`WarningsCog` runs two parallel paths:

**iembot fast-trigger** — the `IEMBotCog` WebSocket feed fires within seconds of issuance. A new `WEA` (warning) product in the feed causes an immediate embed post to `WARNINGS_CHANNEL_ID` before the NWS API has the product indexed.

**`auto_poll_warnings` (every 2 minutes)** — calls the NWS Alerts API (`/alerts/active?event=...`) for Tornado Warnings, Severe Thunderstorm Warnings, Flash Flood Warnings, and Special Weather Statements. Each new VTEC ID is posted as a rich embed containing:
- Nearest-NEXRAD radar GIF (IEM Autoplot)
- IEM Autoplot polygon map (208 for TOR/SVR/FFW, 217 for SPS)
- Affected counties / zones
- Wind, hail, and tornado tags where applicable
- PDS and Tornado Emergency labels when present

**Lifecycle tracking** — when a warning expires, is cancelled, or receives a statement of no activity, the cog edits the original Discord embed in place with a timestamp and updated status. VTEC context (including the Autoplot image URL) is cached at post time so the edit can attach the correct graphic.

**Damage surveys** — `ReportsCog` polls for PNS products flagged as `DAMAGE SURVEY`. Once the NWS survey is finalized, it fetches and posts an IEM Autoplot 253 tornado track map for that event.

### IEMBot Real-Time Feed

`IEMBotCog` polls `weather.im/iembot-json/room/spcchat` every 15 seconds. When a new SEL (watch) or SWOMCD (MD) product appears, the full text is fetched from `mesonet.agron.iastate.edu/api/1/nwstext/{product_id}` and cached via `state_store.set_product_cache` with a 10-minute TTL (written to both Upstash and SQLite). `fetch_watch_details` and `fetch_md_details` check the cache first, so embeds are populated within seconds of issuance — and the Upstash copy means a fresh primary after a failover already has the text. The last-seen seqnum is persisted via `state_store.set_state("iembot_last_seqnum", …)` through the same double-write path.

### SCP Graphics

Posted at 6am and 6pm Pacific daily, but only if the images have actually
changed (hash-based detection). Uses `MODELS_CHANNEL_ID`.

### Sounding Plots

The `/sounding` command geocodes the location, finds nearby RAOB stations that have verified data in the Wyoming archive, and presents an interactive station and time picker. Plots are generated headlessly via SounderPy and posted to the channel where the command was used. Per-user dark mode preference is persisted to the local SQLite database. Auto-posting of soundings is active in three modes: (1) **proactive pre-warming** when the MD cog detects a mesoscale discussion with ≥80% watch issuance probability; (2) **immediately on watch issuance**, using the most recent IEM-available sounding time (any hour); (3) **at 00z/12z** for all active watches. Up to 3 RAOB stations and 2 ACARS profiles per watch. At 00z/12z, Wyoming and IEM are raced simultaneously — whichever returns data first wins.

### CSU-MLP and NCAR WxNext2

Both poll once daily around model update time. State is persisted via `state_store` (Upstash + SQLite) so restarts and failovers don't cause duplicate posts.

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

## Persistence (v5+)

Bot state lives in **Upstash Redis** as the source of truth, with a local SQLite database as a durable mirror for outage survival. Everything in the codebase goes through `utils/state_store.py`, which is a drop-in replacement for the historical `utils/db.py` interface.

### Data flow

```
    cogs → state_store → in-process cache (60 s TTL)
                          │
                          ├─→ Upstash Redis (authoritative)
                          └─→ SQLite mirror (utils/db.py)
```

- **Read**: cache hit → return. Miss → Upstash → populate cache. Upstash unavailable → fall back to SQLite.
- **Write**: update cache immediately, then double-write to SQLite (durability guarantee) and Upstash (best-effort). An Upstash failure enqueues the write on a dirty list; a background reconciler retries every 30 s until it lands.
- **Startup resync**: on promotion (and optionally on boot) the node pushes anything SQLite has that Upstash is missing. Handles the "Upstash was down when we wrote, then we restarted" edge case.

### Upstash key schema

All keys are prefixed with `spcbot:` and are centralized in `utils/state_store.py` `_k_*` helpers.

| Key | Type | Contents |
|---|---|---|
| `spcbot:hashes_index:auto` | HASH | URL → image hash (auto-posted graphics) |
| `spcbot:hashes_index:manual` | HASH | URL → image hash (manual slash-command results) |
| `spcbot:posted_mds` | SET | Posted MD numbers |
| `spcbot:posted_watches` | SET | Posted watch numbers |
| `spcbot:state:<key>` | STRING | KV store (e.g. `iembot_last_seqnum`, `csu_mlp_posted`, sounding prefs) |
| `spcbot:posted_urls:<day>` | STRING | JSON-encoded list of posted URLs for that outlook day |
| `spcbot:product_cache:<id>` | STRING (EX) | Watch/MD text bodies with TTL |
| `spcbot:primary_url` | STRING (EX) | Leader-election lease (see Failover) |

### SQLite mirror

Same tables as historical `utils/db.py`: `image_hashes`, `posted_mds`, `posted_watches`, `bot_state`, `posted_urls`, `product_text_cache`. WAL mode, 5-second busy timeout. If the database fails an integrity check on startup it is renamed to `bot_state.db.corrupted` and recreated.

### Free-tier Upstash budget

Projected ~8.2 k commands/day across both nodes (primary heartbeat writes, standby heartbeat reads, periodic bulk refreshes, state mutations) against the 10 k/day free-tier ceiling. Hot reads are served from the in-process cache, not billed.

---

## Running Tests

Install the dev dependency set (runtime + pytest/pytest-asyncio/pytest-cov/ruff):

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

With coverage:

```bash
python -m pytest tests/ \
    --cov=cogs --cov=utils --cov=config --cov=main \
    --cov-report=term-missing
```

The suite currently collects **255 tests**.

Lint (same selection CI uses):

```bash
ruff check --select=E9,F63,F7,F82,F401 --exclude=venv,lib,cache .
```

---

## Migrating from an older bot_state.db (pre-v5)

If you have an existing SQLite-only install, one-shot migrate it into Upstash before booting the v5 code:

```bash
# First, a dry-run to print counts without touching Upstash:
python -m scripts.migrate_sqlite_to_upstash --dry-run

# Then the real run:
python -m scripts.migrate_sqlite_to_upstash

# Use --force to DEL existing Upstash keys before re-seeding (e.g. after a
# schema change):
python -m scripts.migrate_sqlite_to_upstash --force
```

The script is idempotent (Redis `SADD`/`HSET` won't duplicate on re-run). It requires `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` in `.env`.

---

## Failover (v5+)

Two-node primary/standby architecture using Upstash Redis for **both** leader election *and* shared state. As of v5 there is no HTTP state-sync between nodes — they both read/write the same Upstash keys directly.

### How it works

- **Primary** holds an Upstash lease at `spcbot:primary_url` with `EX HEARTBEAT_TTL` (420 s) and refreshes it every `SYNC_INTERVAL` (30 s). The lease value is a per-process identity (`<hostname>:<random>`) so a node can recognize whether the lease is still its own.
- **Standby** reads the lease every sync interval. If the key is **present**, the primary is alive — the standby does nothing. If the key is **missing** for `MAX_FAILURES` consecutive cycles (derived from the TTL — currently 7 failures ≈ 210 s, half the TTL), the standby promotes:
  1. Invalidates its in-process cache so the first read on every key goes to Upstash.
  2. Writes its own lease value.
  3. Rehydrates `bot.state` mirrors from Upstash.
  4. Calls `state_store.resync_to_upstash()` to push any SQLite-only writes that may have happened during an Upstash outage.
  5. Loads every cog and syncs the slash-command tree.
- **Startup grace**: for the first 120 s after cog load the standby's failure counter does not advance — covers the common case of deploying the standby before the primary has finished its own restart.
- **Self-demotion**: if the current holder sees a *different* node's identity in the lease, it demotes and unloads its cogs rather than fighting.

### What this replaces (historical)

Older versions (≤ v4) shipped state between the two nodes via an HTTP endpoint exposed through a Cloudflare tunnel (`cloudflared`) and used `/state` and `/sync` handlers plus hydration logic. All of that is gone in v5 — state is in Upstash; nodes talk to the shared store directly, not to each other.

### Required `.env` variables

| Variable | Primary | Standby |
|---|---|---|
| `IS_PRIMARY` | `true` | `false` |
| `FAILOVER_TOKEN` | shared secret | same shared secret |
| `UPSTASH_REDIS_REST_URL` | your Upstash URL | same |
| `UPSTASH_REDIS_REST_TOKEN` | your Upstash token | same |

`FAILOVER_TOKEN` is validated at cog load; the cog refuses to start if it's empty or the literal `"changeme"`. This was added after a production incident where the default value meant anyone who discovered the (now-removed) tunnel URL could read full bot state.

### No `cloudflared` dependency

`deploy.sh` and the Dockerfile used to install cloudflared for the tunnel. Neither does as of v5 — if you're upgrading from an older install, the binary can be removed (`sudo rm /usr/local/bin/cloudflared`) but leaving it installed is harmless.

---

## Events Archive (v5.3.2+)

Significant weather events (confirmed tornadoes, hail ≥ 3 in, wind ≥ 80 mph) are written to a dedicated **`cache/events.db`** SQLite file that is entirely separate from `bot_state.db` and never touches Upstash Redis. This keeps the free-tier budget free for operational state (hashes, watches, MDs) while the event archive grows unboundedly.

### Path configuration

| Variable | Default | Purpose |
|---|---|---|
| `EVENTS_DB_PATH` | `cache/events.db` | Path to the events archive database |
| `EVENTS_SYNC_DIR` | `cache/events_sync` | Directory watched by Syncthing for cross-node replication |

### Syncthing cross-node replication (optional)

The Primary snapshots `events.db` into `EVENTS_SYNC_DIR/events.db` every 5 minutes. Install Syncthing on both nodes, create a shared folder with the folder ID below, and the bot handles the rest.

| Variable | Purpose |
|---|---|
| `SYNCTHING_API_KEY` | Local node's Syncthing REST API key (`/home/user/.local/state/syncthing/config.xml`) |
| `SYNCTHING_FOLDER_ID` | Shared folder ID — must match on both nodes (default: `spcbot-events`) |

On promotion the bot restores from the sync snapshot and flips the folder to `sendonly`. On demotion it flips back to `receiveonly`. Set `SYNCTHING_FOLDER_ID=spcbot-events` on both nodes when creating the shared folder.
