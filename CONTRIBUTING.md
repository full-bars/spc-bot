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
| `WARNINGS_CHANNEL_ID` | (Optional) Receives real-time NWS warning embeds (TOR, SVR, FFW, SPS) and damage survey posts. Defaults to `SPC_CHANNEL_ID` if not set. Overridden by per-type channels if set. |
| `TOR_CHANNEL_ID` | (Optional) Tornado warning posts. Defaults to `WARNINGS_CHANNEL_ID` if not set. Configurable at runtime via `/enablewarnings`. |
| `SVR_CHANNEL_ID` | (Optional) Severe thunderstorm warning posts. Defaults to `WARNINGS_CHANNEL_ID` if not set. Configurable at runtime via `/enablewarnings`. |
| `FFW_CHANNEL_ID` | (Optional) Flash flood warning posts. Defaults to `WARNINGS_CHANNEL_ID` if not set. Configurable at runtime via `/enablewarnings`. |
| `SPS_CHANNEL_ID` | (Optional) Special weather statement posts. Defaults to `WARNINGS_CHANNEL_ID` if not set. Configurable at runtime via `/enablewarnings`. |
| `SOUNDING_CHANNEL_ID` | (Optional) Receives auto-posted sounding plots near active watches. Defaults to `SPC_CHANNEL_ID` if not set. |
| `HEALTH_CHANNEL_ID` | (Optional) Receives bot health alerts (watchdog degraded, task failures). Defaults to `SPC_CHANNEL_ID` if not set. |
| `DEV_CHANNEL_ID` | (Optional) Receives watchdog probe-degradation alerts (2/3 warning and session-reset confirmation). Defaults to `HEALTH_CHANNEL_ID` if not set. |

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
| `/md` | Show a paginated view of all active SPC mesoscale discussions |
| `/recenttornadoes` | List confirmed tornadoes via an interactive, chronological calendar-style dashboard |
| `/sigtor` | List significant (EF2+) tornadoes via the interactive dashboard |
| `/archive` | Search the tornado environmental forensics archive by radar and/or date |
| `/enablewarnings` | Configure per-type warning channels (TOR, SVR, FFW, SPS) with a multi-select interface |
| `/displaysetup` | Show the current warning channel routing configuration |
| `/disablewarnings` | Disable all warning routing overrides and revert to static `.env` config |

### Model Forecasts
| Command | Description |
|---|---|
| `/scp` | Show the latest NIU/Gensini SCP forecast graphics. Optional `fresh:True` bypasses cache. |
| `/csu` | Show CSU-MLP ML severe weather forecast — choose from Days 1–8, 6-Panel Days 1-2, or 6-Panel Days 3-8 via dropdown |
| `/wxnext` | Show the latest NCAR WxNext2 Mean AI convective hazard forecast |
| `/wpc` | Show the latest WPC Day 1–3 rainfall outlooks |
| `/fronts` | Show the latest WPC surface fronts analysis. Auto-discovers the most recent 3-hourly analysis cycle by `Last-Modified` and also auto-posts to Weather Chat every 15 minutes on change (SHA-256 dedup). |

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

### Analytics
These commands expose IEM Autoplot or IEM Cow data. They are useful for operations and experimentation, but some are still being hardened for production workflows.

| Command | Description |
|---|---|
| `/topstats` | Tornado warning or report leaderboards by state or WFO (`by`, `year`, `source`) |
| `/dayssince` | IEM map showing days since the last tornado warning (`wfo` and `state` are accepted but currently not applied to the generated URL) |
| `/dailyrecap` | Daily tornado warning polygon recap for a date, defaulting to yesterday |
| `/tornadoheatmap` | Tornado report density map for a recent lookback window |
| `/riskmap` | Historical SPC Day 1 categorical risk-frequency map |
| `/verify` | Storm-based warning verification metrics from IEM Cow |

### Status & Admin
| Command | Description |
|---|---|
| `/status` | Real-time health dashboard: node role, network latency (ms), alert delay, and task states. Features 5-second auto-refresh. |
| `/taskmgr` | (Owner-only) htop-style background loop monitor showing health and iteration timers. |
| `/logs` | (Owner-only) Virtual terminal console viewer with ANSI support and auto-refresh. |
| `/help` | Show all available weather and bot commands. |
| `/failover` | (`ADMIN_USER_ID` only) Open an interactive failover manager to designate a primary node or clear a manual override. |

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

The MD cog polls the SPC mesoscale discussion index every 30 seconds. It tracks
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

`WarningsCog` runs three parallel paths:

**NWWS-OI (XMPP) Trigger** — the highest-authority path. Raw text products are pushed directly from the NWS satellite feed. This path provides near-zero latency, often beating the NWS API and IEM by 10–60 seconds.

**iembot fast-trigger** — secondary path via IEM JSON/botstalk feeds polled every 15 seconds.

**`auto_poll_warnings` (every 30 seconds)** — tertiary polling path using the NWS Alerts API.

**Narrative Extraction** — the bot extracts the substantive "At..." narrative or impact statement from the raw text. For **Special Weather Statements (SPS)**, a refined fallback ensures impact text is captured even without standard bullet points. High-signal weather keywords (e.g., `TORNADO`, `HAIL`, `WIND`) are automatically bolded for clarity.

**Lifecycle tracking** — when a warning expires, is cancelled, or receives a statement of no activity, the cog edits the original Discord embed in place. Mid-warning updates (`CON`, `EXT`, `EXA`) and statements (`SVS`, `FFS`) are posted as fresh, concise embeds with automated county-level diffing ('cancels X, continues Y').

**Damage surveys** — `ReportsCog` polls for PNS products flagged as `DAMAGE SURVEY`. Once the NWS survey is finalized, it fetches and posts an IEM Autoplot 253 tornado track map. The bot automatically attempts to link these tracks to historical events in the database using the NWS Damage Assessment Toolkit (DAT) API, including interactive photo carousels from the field.

### IEMBot Real-Time Feed

`IEMBotCog` polls `weather.im/iembot-json/room/spcchat` every 15 seconds. When a new SEL (watch) or SWOMCD (MD) product appears, the full text is fetched from `mesonet.agron.iastate.edu/api/1/nwstext/{product_id}` and cached via `state_store.set_product_cache` with a 10-minute TTL (written to both Redis and SQLite). `fetch_watch_details` and `fetch_md_details` check the cache first, so embeds are populated within seconds of issuance — and the Redis copy means a fresh primary after a failover already has the text. The last-seen seqnum is persisted via `state_store.set_state("iembot_last_seqnum", …)` through the same double-write path.

### NWWS-OI (XMPP) Authority

`NWWSCog` maintains a persistent XMPP connection to `nwws-oi.weather.gov`. This is the bot's highest-authority data source, delivering raw NWS text products via push notification. 

**Application for Access:**
To use NWWS-OI, you must apply for a user ID and password. The system is intended for "government, emergency management, and weather-sensitive organizations."
- **Website:** [NOAA Weather Wire Service (NWWS)](https://www.weather.gov/nwws/#NWWS-OI)
- **Email:** Contact `nws.nwws.ops@noaa.gov` to request credentials for the Open Interface (XMPP).

**Authority Sequence:**
The bot uses a multi-layered approach to ensure reliability:
1. **NWWS-OI (Primary):** Immediate push; triggers `post_now` logic in alert cogs.
2. **IEMBot (Secondary):** 15s poll of IEM's botstalk/spcchat feeds; used if NWWS is disconnected or misses a product.
3. **NWS API (Tertiary):** warning polling every 30s, watch polling every 2m; provides final polygon/area truth and acts as the ultimate safety net.

### SCP Graphics

Posted at 6am and 6pm Pacific daily, but only if the images have actually
changed (hash-based detection). Uses `MODELS_CHANNEL_ID`.

### VAD Forensics (Evolution GIFs)

`RecorderCog` manages automated 2.5-hour "recording missions" triggered by `OBSERVED` tornado warnings.
1. **Lookback**: Fetches last 60m of VAD scans from S3 upon trigger.
2. **Follow-up**: Polls for new scans for the next 90m.
3. **Rendering**: Uses the shared worker pool to generate animated GIFs.
4. **Archival**: Peak 0-1km SRH is calculated and saved to `events.db` along with the GIF path.
5. **Resumption**: Mission state is persisted to Redis, allowing the bot to survive restarts or failovers during a recording window.

### Cache Management

The bot implements two cleanup paths. `utils/cache_utils.py` runs once after startup and then every 24 hours, removing root cache files older than 7 days. `cogs/maintenance.py` runs every 24 hours on the primary node and removes root image/temp/hash files older than 48 hours, prunes orphaned VAD recording mission directories older than 24 hours, enforces a 1 GB local GIF archive budget, prunes tornado events older than 365 days, and backfills missing DAT GUIDs.

**Log Rotation** has two layers. At runtime, `main.py` uses Python's `RotatingFileHandler` at 5 MB with 3 backups for `LOG_FILE`, and `cogs/nwws.py` writes the raw NWWS firehose log at 10 MB with 1 backup. `config/logrotate.conf` is an optional systemd-host policy with 50 MB files, 12-file retention, and gzip -9 compression.

### Sounding Plots

The `/sounding` command geocodes the location, finds nearby RAOB stations that have verified data in the Wyoming archive, and presents an interactive station and time picker. Plots are generated headlessly via SounderPy and posted to the channel where the command was used. Per-user dark mode preference is persisted to the local SQLite database. Auto-posting of soundings is active in three modes: (1) **proactive pre-warming** when the MD cog detects a mesoscale discussion with ≥80% watch issuance probability; (2) **immediately on watch issuance**, using the most recent IEM-available sounding time (any hour); (3) **at 00z/12z** for all active watches. Up to 3 RAOB stations and 2 ACARS profiles per watch. At 00z/12z, Wyoming and IEM are raced simultaneously — whichever returns data first wins.

**Disk caching**: generated sounding plots are cached to disk using a hash-based key so identical requests skip re-rendering. **Queue management**: concurrent plot requests are governed by an `asyncio.Semaphore`; users see a "Plot Queued (Position X)..." message while waiting so the interaction does not time out.

**Worker pool split**: sounding generation runs in a dedicated "Heavy Sounding" `ProcessPoolExecutor` (isolated from the "Fast Hodo" pool used for hodograph/VAD rendering) to prevent long SounderPy renders from blocking time-sensitive hodo requests.

### CSU-MLP and NCAR WxNext2

Both poll once daily around model update time. State is persisted via `state_store` (Redis + SQLite) so restarts and failovers don't cause duplicate posts.

---

## Task Backoff

The `TaskBackoff` class in `utils/backoff.py` provides per-task exponential backoff for the high-frequency polling loops (`auto_post_spc`, `auto_post_md`, `auto_post_watches`). When a loop cycle fails, subsequent cycles are skipped with increasing delays (0s, 0s, 30s, 60s, 120s, 300s). After 5 consecutive failures a non-critical health alert is posted. On success the counter resets.

---

## Watchdog

A `watchdog_task` loop runs every 2 minutes. It:

1. Probes the HTTP session using two independent endpoints (`api.weather.gov` and `mesonet.agron.iastate.edu`). A failure only counts when **both** are unreachable — single-endpoint NWS outages are absorbed without action. At 2/3 consecutive dual-failures an orange warning embed is posted to `DEV_CHANNEL_ID`. At 3/3 the session is torn down and recreated, and a red confirmation embed is posted.
2. Checks every registered task. If a task has stopped, it restarts it.
3. After a threshold number of failures, posts a health alert embed to
   `SPC_CHANNEL_ID`. Watch and MD task failures are flagged as critical.

Tasks are registered with the watchdog in `main.py` after cogs load.

---

## Persistence (v5+)

Bot state goes through `utils/state_store.py`, which keeps a short-lived in-process cache, writes first to local SQLite for durability, and uses Redis as the shared operational store for HA deployments. Redis failures queue dirty writes for later reconciliation.

### Data flow

```
    cogs → state_store → in-process cache (60 s TTL)
                          │
                          ├─→ SQLite mirror (durable local write)
                          └─→ Redis (shared HA state, best effort)
```

- **Read**: cache hit → return. Miss → Redis → populate cache. Redis unavailable → fall back to SQLite.
- **Write**: update cache immediately, write SQLite for local durability, then write Redis best-effort. A Redis failure enqueues the write on a dirty list; a background reconciler retries every 30 s until it lands.
- **Startup resync**: on promotion (and optionally on boot) the node pushes anything SQLite has that Redis is missing. Handles the "Redis was down when we wrote, then we restarted" edge case.

### Redis key schema

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

### Redis connection

Configure via `REDIS_URL` (e.g. `redis://localhost:6379/0`) or the individual `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` env vars. A single long-lived async client is shared across all state operations. On standby nodes, `ELECTION_REDIS_URL` points exclusively at the primary's Redis for leader-election traffic so that connection failures to the primary are the failover trigger — not a stale replica TTL.

### Redis command volume

Projected ~8 k commands/day across both nodes (primary heartbeat writes, standby heartbeat reads, periodic bulk refreshes, state mutations). Hot reads are served from the in-process cache and never hit Redis.

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

The suite currently collects **382 tests**.

Lint (same selection CI uses):

```bash
ruff check --select=E9,F63,F7,F82,F401 --exclude=venv,lib,cache .
```

### CI pipeline

The GitHub Actions workflow runs three jobs on every push and pull request:

| Job | What it checks |
|---|---|
| **pytest** | Full test suite via `pytest -n auto` (pytest-xdist parallel execution) with coverage |
| **mypy** | Static type check on `utils/` (`mypy utils/`) |
| **rust** | `cargo fmt --check` and `cargo clippy -- -D warnings` on `src_rust/` |

All three jobs must pass before a PR can be merged.

---

## Pre-commit Hooks

The repo ships a `.pre-commit-config.yaml` that enforces the same checks run in
CI (trailing whitespace, ruff lint + format, mypy on `utils/`, and Rust
`cargo fmt`/`cargo clippy`).

Install the hooks once after cloning:

```bash
pip install pre-commit
pre-commit install
```

Run all hooks against the full tree at any time:

```bash
pre-commit run --all-files
```

The Rust hooks (`cargo-fmt` and `cargo-clippy`) require `rustfmt` and `clippy`
to be present. Install them via rustup if needed:

```bash
rustup component add rustfmt clippy
```

---

## Roadmap: Performance & Scalability (v5.13+)

- **Sounding Plot Worker Pool Expansion**: Targeting the primary bottleneck in sounding generation.
- **Hybrid Core (Rust Integration)**:
    - **PyO3 Extensions**: Move CPU-bound image hashing (change detection) to Rust.
    - **Binary Parsing**: Implement a high-speed Rust `nom` parser for NEXRAD/VWP products.
    - **Sidecar Service**: Offload heavy I/O (zipping/downloads) to a compiled Rust binary.
- **Database Connection Pooling**: Improving SQLite/Redis throughput.

---

## Migrating from an older bot_state.db (pre-v5)

If you have an existing SQLite-only install, one-shot migrate it into Redis before booting the v5 code:

```bash
# First, a dry-run to print counts without writing to Redis:
python -m scripts.migrate_sqlite_to_redis --dry-run

# Then the real run:
python -m scripts.migrate_sqlite_to_redis

# Use --force to DEL existing Redis keys before re-seeding (e.g. after a
# schema change):
python -m scripts.migrate_sqlite_to_redis --force
```

The script is idempotent (Redis `SADD`/`HSET` won't duplicate on re-run). It requires `REDIS_URL` (or `REDIS_HOST`/`REDIS_PORT`) in `.env`.

---

## Failover (v5+)

Two-node primary/standby architecture using a **self-hosted Redis 7+** instance on the primary node for leader election and shared state. Nodes communicate over **Tailscale** (or any private network with Redis port reachability). There is no HTTP state-sync between nodes.

### How it works

- **Primary** holds a Redis lease at `spcbot:primary_url` with `EX HEARTBEAT_TTL` (420 s) and refreshes it every `SYNC_INTERVAL` (30 s) via a Lua conditional `SET` that only writes if the caller still holds the key (prevents split-brain reclaim). The lease value is a per-process identity (`<role>:<hostname>:<uuid>`) so a node can recognize whether the lease is still its own.
- **Standby** points `ELECTION_REDIS_URL` at the primary's Redis (via Tailscale). It reads the lease every sync interval. If the primary's Redis is **unreachable** or the lease is **missing** for `MAX_FAILURES` consecutive cycles (currently 7 ≈ 210 s), the standby promotes:
  1. Updates its identity from `S:` to `P:` prefix and purges its old entry from the nodes hash.
  2. Issues `REPLICAOF NO ONE` to its local Redis replica via a dedicated local client, detaching it so writes succeed.
  3. Invalidates its in-process cache so the first read on every key goes to Redis.
  4. Writes its own lease value.
  5. Rehydrates `bot.state` mirrors from Redis.
  6. Calls `state_store.resync_to_redis()` to push any SQLite-only writes queued during the outage.
  7. Loads every cog and syncs the slash-command tree.
- **Startup grace**: for the first 120 s after cog load the standby's failure counter does not advance — covers the common case of deploying the standby before the primary has finished its own restart.
- **Self-demotion**: if the current holder sees a *different* node's identity in the lease, it demotes and unloads its cogs rather than fighting.
- **Manual override**: `/failover` lists active nodes from `spcbot:nodes` and writes `spcbot:manual_primary` when an authorized operator designates a host. Clearing the override returns the pair to automatic election.
- **Failover indicator**: `/status` displays an orange **PRIMARY ⚠️ FAILOVER** badge when a standby-configured node is acting as primary, so operators can immediately identify an unplanned promotion.

### Measured failover timing

- **Primary crash → standby fully live**: ~210 s (7 × 30 s heartbeat cycles after clean lease release)
- **Restore primary**: ~30 s (stop standby → standby releases lease → restart primary → claims lease → restart standby → boots as standby)

### Required `.env` variables

| Variable | Primary | Standby |
|---|---|---|
| `IS_PRIMARY` | `true` | `false` |
| `FAILOVER_TOKEN` | shared secret | same shared secret |
| `REDIS_URL` | `redis://localhost:6379/0` | `redis://localhost:6379/0` |
| `ELECTION_REDIS_URL` | *(not set — defaults to `REDIS_URL`)* | `redis://<primary-tailscale-ip>:6379/0` |
| `ADMIN_USER_ID` | Discord user allowed to use `/failover` | same |

`FAILOVER_TOKEN` is validated at cog load; the cog refuses to start if it's empty or the literal `"changeme"`.

### What this replaces (historical)

- **v4 and earlier**: state shipped via HTTP through a Cloudflare tunnel (`cloudflared`). Gone in v5.
- **v5.0–v5.25**: used Upstash Redis REST API. Replaced in v5.26 with self-hosted Redis to eliminate external API quota constraints and reduce latency.

If you're upgrading from an Upstash install, remove `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` from your `.env` and set `REDIS_URL` / `ELECTION_REDIS_URL` as above.

---

## Events Archive (v5.3.2+)

Significant weather events (confirmed tornadoes ONLY) are written to a dedicated **`cache/events.db`** SQLite file that is entirely separate from `bot_state.db` and never touches Upstash Redis. Hail and wind events are explicitly excluded from this archive to maintain focus on the tornado record. This keeps the free-tier budget free for operational state (hashes, watches, MDs) while the event archive follows the 365-day retention policy enforced by `cogs/maintenance.py`.

### Path configuration

| Variable | Default | Purpose |
|---|---|---|
| `EVENTS_DB_PATH` | `cache/events.db` | Path to the confirmed tornado and forensics archive database |
| `EVENTS_SYNC_DIR` | `cache/events_sync` | Directory watched by Syncthing for cross-node replication |

### Syncthing cross-node replication (optional)

The Primary snapshots `events.db` into `EVENTS_SYNC_DIR/events.db` every 5 minutes. Install Syncthing on both nodes, create a shared folder with the folder ID below, and the bot handles the rest.

| Variable | Purpose |
|---|---|
| `SYNCTHING_API_KEY` | Local node's Syncthing REST API key (`/home/user/.local/state/syncthing/config.xml`) |
| `SYNCTHING_FOLDER_ID` | Shared folder ID — must match on both nodes (default: `spcbot-events`) |

On promotion the bot restores from the sync snapshot and flips the folder to `sendonly`. On demotion it flips back to `receiveonly`. Set `SYNCTHING_FOLDER_ID=spcbot-events` on both nodes when creating the shared folder.
