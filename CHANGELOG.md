# Changelog

All notable changes to this project will be documented in this file. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
version numbers follow [SemVer](https://semver.org/).

## [Unreleased]

## [5.1.1] — 2026-04-22

### Changed
- `aiohttp.TCPConnector` now sets `ttl_dns_cache=300` and
  `keepalive_timeout=75` so repeated NWS/SPC fetches reuse DNS + TCP.
- All HTTP retry sleeps use full-jitter backoff to avoid lockstep
  retries when parallel fetches all hit 429/503 at once.
- `http_get_bytes` is now a thin wrapper over `http_get_bytes_conditional`
  (gained `extra_headers` kwarg). Removes a duplicated retry loop.
- `download_single_image` uses conditional GET, so auto-posts benefit
  from 304s — not just the partial-update pass.
- `_validators_cache` is LRU-bounded (2048 entries) to prevent unbounded
  growth as dated URLs rotate.
- `check_partial_updates_parallel` returns outcomes from `gather`
  instead of mutating counters via `nonlocal`.
- `should_use_cache_for_manual` runs its stat loop in an executor.
- `cogs.watches` compiles VTEC / href / tornado-watch regex at module
  level; `fetch_active_watches_nws` uses conditional GET with a
  module-level last-parsed cache so 304 short-circuits re-parsing.

### Removed
- Dead code in `utils.change_detection`: `head_changed`,
  `clear_head_cache_for_url`, `_head_cache` (zero callers).

## [5.1.0] — 2026-04-21

### Added
- Identifying `User-Agent` on all outbound HTTP so NWS/SPC won't throttle
  the bot as an unknown client. UA is derived from `config.__version__`.
- `http_validators` SQLite table and `get_validators` / `set_validators` /
  `get_all_validators` helpers. Conditional-GET ETag / Last-Modified pairs
  now survive restart, so the first poll after boot no longer redownloads
  every URL.
- DB write-failure counter (`utils.db.get_write_failure_count`). Five
  consecutive failed writes escalate from warning to error so a persistent
  outage (full disk, schema drift) is visible.

### Changed
- `utils.state_store.get_hash` accepts an optional `cache_type`. When
  provided, the Upstash lookup hits a single HGET instead of racing both
  the `auto` and `manual` indexes — halves command cost on that path.
- Watchdog session probe now targets `api.weather.gov` (HEAD) instead of
  `google.com`. Reflects whether the bot's actual upstream is reachable.
- `cogs.watches._execute_watches` fetches per-watch details in parallel;
  `fetch_watch_details` fetches the SPC main page and prob page in
  parallel; the HTML-fallback classifier also runs in parallel.
- Duplicated watch-embed construction collapsed into a single
  `_build_watch_embed` / `_watch_files` helper (paginator, auto-post,
  iembot fast-path, and upgrade-edit all share the same code).
- Image cache writes (`download_single_image`, `save_downloaded_images`)
  go through `run_in_executor` so burst saves don't stall the event loop.
- `http_get_bytes` surfaces the terminal 429/503 status to callers
  instead of flattening to `(None, None)`, and no longer sleeps after
  the final retry attempt.

### Fixed
- Cache-path extension is now whitelisted and the URL query / fragment
  is stripped before `os.path.splitext`, so a URL like
  `x.gif?param=..%2F..` can no longer shape the cached filename.
- `utils.state_store._upstash_cmd` rejects `None` arguments instead of
  silently shipping the literal `"None"` on the wire.
- `http_head_ok` no longer falls back to a full GET on HEAD failure — a
  liveness probe that downloads the body defeats the purpose.
- `should_use_cache_for_manual` collapses its `exists()` + `getmtime()`
  pair into a single `os.stat`.

### Removed
- Unused `_connecting` flag in `utils.db`.
