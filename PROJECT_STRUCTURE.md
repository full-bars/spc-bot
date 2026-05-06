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
├── scripts/
│   └── migrate_sqlite_to_upstash.py  # One-shot migration of local SQLite into Upstash
├── utils/
│   ├── http.py              # Async HTTP session management (centralized pooling, retry, conditional GET)
│   ├── change_detection.py  # Content hashing and placeholder-image detection
│   ├── cache.py             # Download orchestration; conditional-GET poll path (validators persist across restarts)
│   ├── cache_utils.py       # TTL-based cache eviction with scheduled cleanup tasks (7-day default)
│   ├── state.py             # BotState — HashStore + PostingLog + TimingTracker sub-stores
│   ├── state_store.py       # Upstash Redis facade: read-through cache → Upstash → SQLite fallback;
│   │                        # double-writes both backends, retries failed Upstash writes via a reconciler
│   ├── events_db.py         # Standalone SQLite archive for significant events (tornadoes, hail, wind);
│   │                        # separate from bot_state.db, never synced to Upstash
│   ├── spc_urls.py          # SPC outlook URL resolution
│   ├── spc_outlook.py       # SPC Day 1 categorical polygon (MDT/HIGH) with geodesic buffer
├── backoff.py           # Exponential backoff tracker for task loops
├── worker_pool.py       # Shared ProcessPoolExecutor for background rendering
└── db.py                # Async SQLite backend used internally by state_store as the durable mirror; also home of http_validators

├── cogs/
│   ├── outlooks.py          # SPC Day 1-3 and Day 4-8 auto-posting
│   ├── mesoscale.py         # SPC MD monitoring with watch probability detection and IEM fallbacks
│   ├── iembot.py            # IEM iembot feed poller with persistent text-product caching
│   ├── watches.py           # SPC watch monitoring via NWS API (stores affected_zones)
│   ├── warnings.py          # NWS VTEC warning monitoring (SVR, TOR, FFW) — polling & deduplication logic
│   ├── warning_format.py    # Warning styling, narrative extraction, URL generation (decoupled from warnings.py)
│   ├── warning_ui.py        # Discord UI views for tornado data: EnvironmentalView, TornadoPhotoView, TornadoDashboardView
│   ├── reports.py           # LSR and PNS monitoring; triggers Autoplot 253 tornado track posts
│   ├── scp.py               # NIU/Gensini SCP graphics, twice daily
│   ├── csu_mlp.py           # CSU-MLP consolidated /csu command with Choice dropdown
│   ├── ncar.py              # NCAR WxNext2 AI severe weather forecast
│   ├── sounding.py          # RAOB+ACARS sounding plots; auto-posts near active watches
│   ├── sounding_utils.py    # Location resolution, IEM fetch (all hours), ACARS fetch, plot generation
│   ├── sounding_views.py    # Discord UI: CombinedSoundingView, IEMTimeSelectionView, ACARS views
│   ├── hodograph.py         # VWP hodograph generation via /hodograph
│   ├── failover.py          # Leader election via an Upstash lease (no HTTP tunnel — v5+)
│   ├── status.py            # Bot status and manual slash commands
│   └── radar/
│       ├── __init__.py      # Radar cog: /download with quick-start site+time+count params
│       ├── s3.py            # S3 client, file listing, time parsing
│       ├── downloads.py     # Download orchestration, zipping, progress
│       └── views.py         # Discord UI views and modals
├── config/
│   └── logrotate.conf       # Log rotation config: size-based (50 MB), 12-file retention, gzip -9 compression
├── src_rust/
│   ├── lib.rs               # PyO3 Rust extension: VAD calculations, VTEC parsing, haversine distance, batch operations (803 LOC)
│   └── Cargo.toml           # Rust dependencies (pyo3, xxhash_rust, regex, rstar, geo)
├── lib/
│   ├── vtec_parser.py       # VTEC/polygon parsing (reusable, zero Discord dependencies); Rust bridge with Python fallback
│   ├── geo.py               # Geospatial utilities; haversine distance wrapper with Rust bridge
│   └── vad_plotter/         # Hodograph library (vad-plotter by Tim Supinie)
│       ├── vad.py           # Main entry point, called as subprocess
│       ├── vad_reader.py    # NEXRAD VWP binary parser
│       ├── plot.py          # Hodograph plotting with matplotlib
│       ├── params.py        # Storm parameter computations
│       ├── wsr88d.py        # Radar site info and filename utilities
│       ├── asos.py          # ASOS surface wind fetching
│       └── utils.py         # Shared exception types
└── tests/                   # pytest suite (380 tests, see CONTRIBUTING.md)
    ├── conftest.py          # Fixtures: fake_bot (real BotState), isolated_db, global patches
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

All Rust functions maintain pure-Python fallback implementations. FFI detection at runtime gracefully downgrades to Python if Rust extension is unavailable.

### Modular Refactoring (v5.16.0+)
Large feature modules are split into focused, reusable components:
- **lib/vtec_parser.py**: VTEC/polygon parsing with zero Discord dependencies—usable in scripts and utilities
- **cogs/warning_format.py**: All styling, narrative extraction, and URL generation
- **cogs/warning_ui.py**: Discord-specific UI views (buttons, carousels, dashboards)
- **cogs/warnings.py**: Polling and deduplication logic only

### State Management
- **Upstash Redis**: Operational source of truth; all posted IDs, hashes, state
- **SQLite (bot_state.db)**: Durable mirror; survives Upstash outages
- **Events DB (events.db)**: Standalone archive of significant tornadoes; synced cross-node via Syncthing (optional)

### High Availability
See [High Availability & Failover](CONTRIBUTING.md#failover) in CONTRIBUTING.md for detailed setup. In brief:
- Primary node holds Upstash lease, runs all loops
- Standby heartbeats and promotes if lease expires
- No HTTP tunnel required; all state in Upstash + SQLite

### Scheduled Tasks
- **auto_poll_spc** (30s): SPC outlook polling
- **auto_post_md** (60s): Mesoscale discussion monitoring
- **auto_post_watches** (120s): SPC watch polling
- **auto_poll_warnings** (30s): NWS warning API polling
- **auto_post_soundings** (00z/12z): Synoptic sounding cycles
- **periodic_cache_cleanup** (03:00 UTC daily): TTL-based eviction (7-day default)
- **logrotate** (size-based): 50 MB per file, 12-file retention, gzip -9

## Testing

The test suite has **380 tests** covering:
- Unit tests for parsers (VTEC, polygons, narratives)
- Integration tests for bot state and cog lifecycle
- Failover scenarios and race conditions
- HTTP caching and retry logic
- Forecasting model parsing
- **Rust FFI tests** (Phases 1–6): SRH/Bunkers, VTEC parser, image cache validator, product ID normalizer, haversine distance, with Python fallback verification

Run tests with:
```bash
pip install -r requirements-dev.txt
VIRTUAL_ENV=venv maturin develop --release  # Build Rust extension
python -m pytest tests/ -v
```

See [CONTRIBUTING.md](CONTRIBUTING.md#running-tests) for coverage reports and lint checks.
