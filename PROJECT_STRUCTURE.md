# Project Structure

## Directory Layout

```
spc-bot/
├── main.py                  # Bot entrypoint, watchdog, and signal handling
├── deploy.sh                # Portable one-command deployment script
├── Dockerfile               # Debian-based scientific stack image
├── docker-compose.yml       # Docker orchestration
├── install-hooks.sh         # Installs pre-push git hooks (syntax + test checks)
├── config.py                # Configuration and centralized URL constants
├── requirements.txt         # Runtime dependencies (what the bot needs to run)
├── requirements-dev.txt     # Runtime + pytest/pytest-asyncio/pytest-cov/ruff for development & CI
├── .env.example             # Template for required environment variables
├── CREDITS.md               # Third-party attributions
├── PRIVACY_POLICY.md        # Data collection and usage policies
├── TERMS_OF_SERVICE.md      # Usage terms and situational awareness disclaimer
├── scripts/
│   ├── backfill_dat.py               # DAT enrichment utility for tornado events
│   ├── benchmark_hashing.py          # Python/Rust image hashing benchmark
│   ├── migrate_sqlite_to_redis.py    # One-shot migration of local SQLite into Redis
│   ├── nwws_monitor.py               # Standalone NWWS-OI connection monitor
│   └── precache_all_photos.py        # DAT photo cache utility
├── utils/
│   ├── http.py              # Async HTTP session management (centralized pooling, retry, conditional GET)
│   ├── change_detection.py  # Content hashing and placeholder-image detection
│   ├── cache.py             # Download orchestration; conditional-GET poll path (validators persist across restarts)
│   ├── cache_utils.py       # TTL-based cache eviction with scheduled cleanup tasks (7-day default)
│   ├── state.py             # BotState — HashStore + PostingLog + TimingTracker sub-stores
│   ├── state_store.py       # Redis facade: read-through cache → Redis → SQLite fallback;
│   │                        # double-writes both backends, retries failed Redis writes via a reconciler
│   ├── events_db.py         # Standalone SQLite archive for confirmed tornadoes and tornado forensics;
│   │                        # separate from bot_state.db, never synced to Redis
│   ├── spc_urls.py          # SPC outlook URL resolution
│   ├── spc_outlook.py       # SPC Day 1 categorical polygon (MDT/HIGH) with geodesic buffer
│   ├── ai.py                # Gemini API integration for AI summaries (outlook, MD, sounding analysis)
│   ├── compare_utils.py     # Outlook version comparison and historical archive utilities
│   ├── dat_api.py           # Direct Access Tornado (DAT) API integration for survey links and photo galleries
│   ├── discord_gateway.py   # Discord WebSocket gateway heartbeat monitoring
│   ├── geo.py               # Rust-accelerated geospatial queries: haversine distance, R*-tree nearest stations, polygon queries
│   ├── map_utils.py         # Map rendering and geographic visualization utilities
├── backoff.py               # Exponential backoff tracker for task loops
├── worker_pool.py           # Two separate ProcessPoolExecutors: "Fast Hodo" (hodograph/VAD rendering) and "Heavy Sounding" (SounderPy plot generation)
└── db.py                    # Async SQLite backend used internally by state_store as the durable mirror; also home of http_validators

├── cogs/
│   ├── nwws.py              # NWWS-OI XMPP firehose; routes immediate warning/watch/MD triggers
│   ├── analytics.py         # IEM analytics slash commands (/topstats, /verify, /riskmap, etc.)
│   ├── recorder.py          # VAD forensics recorder and /archive search
│   ├── outlooks.py          # SPC Day 1-3 and Day 4-8 auto-posting with AI summary autoposting
│   ├── mesoscale.py         # SPC MD monitoring with watch probability detection, IEM fallbacks, AI summary autoposting
│   ├── iembot.py            # IEM iembot feed poller with persistent text-product caching
│   ├── watches.py           # SPC watch monitoring via NWS API (stores affected_zones)
│   ├── watch_fetch.py       # Watch data fetching and zone parsing
│   ├── watch_format.py      # Watch formatting utilities
│   ├── warnings.py          # NWS VTEC warning monitoring (SVR, TOR, FFW) — polling & deduplication logic
│   ├── warning_channels.py  # Slash commands for per-type warning channel routing (/enablewarnings, /displaysetup, /disablewarnings)
│   ├── warning_format.py    # Warning styling, narrative extraction, URL generation (decoupled from warnings.py)
│   ├── warning_ui.py        # Discord UI views for tornado data: EnvironmentalView, TornadoPhotoView, TornadoDashboardView
│   ├── reports.py           # LSR and PNS monitoring; logs tornado events and DAT survey links
│   ├── scp.py               # NIU/Gensini SCP graphics, twice daily
│   ├── csu_mlp.py           # CSU-MLP consolidated /csu command with Choice dropdown
│   ├── ncar.py              # NCAR WxNext2 AI severe weather forecast
│   ├── sounding.py          # RAOB+ACARS sounding plots; auto-posts near active watches with AI summary autoposting; asyncio.Semaphore queue with position feedback
│   ├── sounding_utils.py    # Location resolution, IEM fetch (all hours), ACARS fetch, plot generation; disk-caches plots with hash-based dedup
│   ├── sounding_views.py    # Discord UI: CombinedSoundingView, IEMTimeSelectionView, ACARS views
│   ├── hodograph.py         # VWP hodograph generation via /hodograph
│   ├── failover.py          # Leader election via a self-hosted Redis lease (no HTTP tunnel — v5+)
│   ├── wxsummary.py         # /wxsummary: Project WxEye live weather briefing embed
│   ├── status.py            # Bot status and manual slash commands
│   ├── ai_summaries.py      # AI-powered summaries for outlooks, MDs, soundings; context-aware synthesis with Gemini
│   ├── historical.py        # Historical outlook archive and /compare tool
│   ├── fronts.py            # WPC surface fronts monitoring and posting
│   ├── maintenance.py       # Administrative utilities and maintenance commands
│   └── subscriptions.py     # User subscription management and preferences
├── config/
│   └── logrotate.conf       # Optional external logrotate config: 50 MB files, 12-file retention, gzip -9 compression
├── src_rust/
│   ├── lib.rs               # PyO3 Rust extension: VAD calculations, VTEC parsing, haversine distance, batch operations (803 LOC)
│   └── Cargo.toml           # Rust dependencies (pyo3, xxhash_rust, regex, rstar, geo)
├── lib/
│   ├── vtec_parser.py       # VTEC/polygon parsing (reusable, zero Discord dependencies); Rust bridge with Python fallback
│   └── vad_plotter/         # Hodograph library (vad-plotter by Tim Supinie)
│       ├── vad.py           # Main entry point, called as subprocess
│       ├── vad_reader.py    # NEXRAD VWP binary parser
│       ├── plot.py          # Hodograph plotting with matplotlib
│       ├── params.py        # Storm parameter computations
│       ├── wsr88d.py        # Radar site info and filename utilities
│       ├── asos.py          # ASOS surface wind fetching
│       └── utils.py         # Shared exception types
└── tests/                   # pytest suite (382 tests, see CONTRIBUTING.md)
    ├── conftest.py          # Fixtures: fake_bot (real BotState), isolated_db, global patches
    ├── test_analytics.py    # IEM analytics command URL/API behavior
    ├── test_fixtures.py     # Fixture invariants
    ├── test_utils.py        # Utility and sounding parsing
    ├── test_watches.py      # Watch VTEC parsing
    ├── test_warnings.py     # Warning VTEC and LAT...LON polygon parsing
    ├── test_surveys.py      # PNS date extraction and Autoplot 253 polling
    ├── test_integration.py  # BotState, cog instantiation, function signatures
    ├── test_state_split.py  # HashStore / PostingLog / TimingTracker delegation
    ├── test_state_store.py  # Upstash-backed state store (cache, reconciler, SQLite fallback)
    ├── test_db.py           # SQLite backend roundtrips
    ├── test_http.py         # HTTP retry + conditional GET
    ├── test_cache_conditional.py  # Partial-update poll with ETag/If-Modified-Since
    ├── test_backoff.py      # TaskBackoff delay and alert logic
    ├── test_main_lifecycle.py  # Shutdown guard, watchdog restart, startup smoke
    ├── test_failover_coverage.py  # Lease election, promotion, demotion
    ├── test_hodograph.py    # Hodograph cog
    ├── test_radar_cleanup.py  # Radar download temp cleanup
    ├── test_recorder_finalize.py  # VAD recorder mission finalization
    ├── test_iem_races.py    # IEM/SPC race logic and watch-triggered soundings
    ├── test_spc_outlook.py  # Day 1 categorical polygon parsing + geodesic buffer
    ├── test_iembot.py       # IEMBotCog seqnum persistence, feed filtering, dispatch paths
    ├── test_mesoscale.py    # MesoscaleCog MD cancellation, lag protection, year wraparound
    ├── test_rust_params.py  # Rust SRH/Bunkers calculator and Python fallback
    ├── test_rust_vtec.py    # Rust VTEC string parser and Python fallback
    ├── test_rust_cache.py   # Rust image cache batch validator and Python fallback
    ├── test_rust_nwws.py    # Rust product_id normalizer and Python fallback
    └── test_rust_geo.py     # Rust haversine distance calculator and Python fallback
```

## Architecture Overview

### Core Polling System
The bot operates a multi-source authority hierarchy for weather data:
- **NWWS-OI (Primary)**: Direct push via XMPP for <1s latency
- **IEMBot (Secondary)**: Polled every 15s as fallback
- **NWS API (Tertiary)**: Polled every 30-60s for final truth and outage resilience

### Rust Integration (v5.20.0+)
High-performance Rust core via PyO3 with Python fallback for all operations:
- **VAD Hodograph Calculations** (Phase 1): vec2comp, comp2vec, compute_bunkers, compute_srh, compute_critical_angle
- **VTEC String Parser** (Phase 2): Regex-based action/office/phenom/sig/ETN extraction
- **Image Cache Batch Validator** (Phase 3): XXH3 hashing and placeholder detection (<2048 bytes threshold)
- **NWWS Product ID Normalizer** (Phase 4): ISO8601 timestamp conversion and product ID deduplication
- **Haversine Distance Calculator** (Phase 5): Geospatial queries for station lookups
- **Comprehensive Unit Tests** (Phase 6): 30+ tests covering all Rust internals
- **VAD Storm-Mode Math** (Phase 7): `compute_shear_mag`, `compute_sr_flow`, `clip_profile` from `lib/vad_plotter/params.py` now Rust-backed
- **Batch Spatial Joins** (Phase 8): `find_nearest_stations_batch`, `points_in_polygon_counts`, `points_in_polygon_lookup` in `utils/geo`; `find_nearest_stations` uses Rust fast-path via rstar R*-tree

All Rust functions maintain pure-Python fallback implementations. FFI detection at runtime gracefully downgrades to Python if Rust extension is unavailable.

### AI Summary Autoposting (v5.37.6+)
Context-aware AI-powered summaries for weather products, powered by Google Gemini:
- **Outlook Summaries** (cogs/ai_summaries.py → ensure_outlook_summary): Regional analysis by geographic area, paginated across Previous/Next buttons when multiple risk zones detected
- **Mesoscale Discussion Summaries**: Concise plain-English summaries of MD threats, timing, and limiting factors
- **Sounding Summaries**: Enhanced environmental analysis synthesizing local thermodynamic/kinematic data with cross-referenced SPC products (Day 1 outlook + active MDs + nearby watches)
- **Auto-Posting**: Non-blocking background tasks (asyncio.create_task) post summaries as Discord follow-ups immediately after the main product post
- **Error Resilience**: Full try-catch blocks with warning logs ensure API failures (token limits, network errors, missing text) don't break product posts
- **Caching**: 3-day Redis TTL per summary; re-use cached analysis if available before API call

### Modular Refactoring (v5.16.0+)
Large feature modules are split into focused, reusable components:
- **lib/vtec_parser.py**: VTEC/polygon parsing with zero Discord dependencies—usable in scripts and utilities
- **cogs/warning_format.py**: All styling, narrative extraction, and URL generation
- **cogs/warning_ui.py**: Discord-specific UI views (buttons, carousels, dashboards)
- **cogs/warnings.py**: Polling and deduplication logic only

### State Management
- **In-process cache**: Short TTL read-through cache for hot operational state
- **Redis (self-hosted)**: Shared operational state and leader-election lease for HA deployments
- **SQLite (bot_state.db)**: Durable local mirror; writes land here before best-effort Redis replication
- **Events DB (events.db)**: Standalone confirmed tornado archive and forensics record; synced cross-node via Syncthing (optional)

### High Availability
See [High Availability & Failover](CONTRIBUTING.md#failover) in CONTRIBUTING.md for detailed setup. In brief:
- Primary node holds Redis lease, runs all loops
- Standby points `ELECTION_REDIS_URL` at primary's Redis; promotes after 7 consecutive missed heartbeats (~210 s)
- No HTTP tunnel required; all state in Redis + SQLite

### Scheduled Tasks
- **auto_post_spc** (30s): SPC outlook polling
- **auto_post_md** (30s): Mesoscale discussion monitoring
- **auto_post_watches** (120s): SPC watch polling
- **auto_poll_warnings** (30s): NWS warning API polling
- **monitor_special_soundings** (15m): Special RAOB releases near active watches
- **monitor_high_risk_soundings** (15m): RAOB/ACARS sweep inside MDT/HIGH Day 1 risk polygons
- **auto_sounding_watches** (30m): 00z/12z sounding cycles near active watches
- **cleanup_cache_loop** (24h): Root cache file pruning, orphan VAD mission cleanup, 1 GB GIF archive budget, 365-day tornado event retention
- **periodic_cache_cleanup** (24h after startup): TTL-based root cache eviction (7-day default)
- **snapshot_events_task** (5m): Primary-only snapshot of `events.db` for optional Syncthing replication
- **periodic_cleanup** (1h): Radar downloader temporary file cleanup
- **application log rotation**: Runtime `RotatingFileHandler` uses 5 MB files with 3 backups; `config/logrotate.conf` is an optional external 50 MB/12-file policy for systemd installs

## Testing

The test suite has **382 tests** covering:
- Unit tests for parsers (VTEC, polygons, narratives)
- Integration tests for bot state and cog lifecycle
- Failover scenarios and race conditions
- HTTP caching and retry logic
- Forecasting model parsing
- **Rust FFI tests** (Phases 1–8): SRH/Bunkers, VTEC parser, image cache validator, product ID normalizer, haversine distance, VAD storm-mode math, batch spatial joins — with Python fallback verification

Run tests with:
```bash
pip install -r requirements-dev.txt
VIRTUAL_ENV=venv maturin develop --release  # Build Rust extension
python -m pytest tests/ -n auto -v          # pytest-xdist parallel execution
```

CI additionally runs a **mypy gate** on `utils/` and a **Rust clippy/fmt** job on every push. See [CONTRIBUTING.md](CONTRIBUTING.md#running-tests) for coverage reports and lint checks.
