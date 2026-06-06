# Changelog

All notable changes to this project will be documented in this file.

## [5.36.1] - 2026-06-05

### Fixed
- **AI Integration:** Updated the Gemini API endpoint to use `gemini-flash-latest` as `gemini-1.5-flash` was deprecated/unavailable.

## [5.36.0] - 2026-06-05

### Added
- **AI Integration:** Added custom async REST client (`utils/ai.py`) for interacting directly with the Gemini API.
- **Interactive Outlooks:** Added an "🪄 AI Analysis" button to Day 1, 2, 3, and 4-8 Convective Outlook embeds for structured AI summaries.
- **Interactive MDs:** Added a "🪄 TL;DR" button to Mesoscale Discussions for quick, on-demand AI summaries.
- **Slash Commands:** Added `/dailybriefing` command to generate an AI briefing combining the Day 1 Outlook and currently active watches.
- **Survey Routing:** Added `SURVEYS_CHANNEL_ID` configuration for dedicated routing of PNS and DAT statements.

### Performance
- **AI Caching:** Implemented in-memory caching for AI-generated summaries to minimize API calls and latency on duplicate requests.

## [5.35.0] — 2026-06-04

### Added
- **`/compare` Command.** Compare any two SPC convective outlooks (categorical or individual hazard probabilities) side-by-side or calculate differences. Supports Day 1, Day 2, Day 3, and Day 4-8 outlooks, featuring an API fallback for real-time products when no archived version exists, and support for the `'all'` product option. (#490)
- **`/historical` Command.** Retrieve historical convective outlook images (categorical and hazards) from the SPC archive (2004–present). Allows selecting specific issuance times dynamically from a dropdown menu if the requested time is unavailable, and supports the `'all'` product option to fetch all hazards in one command. (#491)
- **Rust VAD S3 Fetcher.** Added a high-performance Rust-based VAD (Velocity Azimuth Display) S3 fetcher in `spc_rust_core` using the `reqwest` library. Acts as a fast-path fallback for real-time radar data, bypassing Python's `aioboto3` overhead and reducing S3 fetch latency from ~742ms to ~260ms. (#487)
- **SURVEYS_CHANNEL_ID Configuration.** Added dedicated channel routing for Damage Survey PNS (tornado rating) posts and DAT toolkit plots via a new `SURVEYS_CHANNEL_ID` environment variable.

### Performance
- **Read connection pooling for events database.** Implemented a read-only connection pool (size 5) for `utils/events_db.py`, allowing concurrent slash command reads without blocking background writes. (#484)
- **Expanded sounding worker pool.** Increased the sounding plot worker pool size from 3 to 4 to improve throughput and reduce queue times during high-risk weather events. (#484)

### Fixed
- **NWWS Backend Conflict on Promotion.** Resolved a dual NWWS backend conflict in `FailoverCog` promotion logic by prioritizing the Rust NWWS backend and explicitly cancelling the legacy Python loop if Rust is active, preventing redundant XMPP connections. (#485)
- **VAD Reliability & Decompression.** Enhanced VAD S3 fallback reliability, added Gzip/Zlib decompression for radar products (supporting Gzip `1f8b`, default Zlib `789c`, and level-9 Zlib `78da`), resolved a double-slash bug in the TGFTP URL fallback path, and expanded the S3 search window to 3 days for radar wind profiles. (#486)
- **`/logs` Virtual Console Enhancements.** Implemented live log tailing with real-time websocket/refresh loops, a functional stop button interface, content hashing rather than line indices to accurately track updates, and immediate display of recent logs upon invocation rather than waiting for new entries. (#480, #481, #482, #483)
- **`/fronts` discovery timeout and stale Discord images.** WPC surface fronts discovery now applies a 15s per-request HEAD timeout while scanning the 3-hourly analysis cycles, and improved logging makes cycle selection observable. Embeds append a `Last-Modified`-derived cache-busting query parameter (`?t=<epoch>`) so Discord refetches the current analysis instead of serving a stale cached image. (#477)
- **`/logs` message size limit.** The owner-only live console viewer could build a message exceeding Discord's 2000-character limit, causing the send to fail and surface "⚠️ An unexpected error occurred." Output is now truncated to fit within the limit. (#478)

### Documentation
- **Backfilled slash-command documentation.** A coverage audit found commands defined in code but missing from user-facing docs. Added `/fronts` (shipped in 5.34.7) and `/wxsummary` (Project WxEye briefing) across the `/help` embed, README, CONTRIBUTING, and the GitHub Wiki. The `/help` embed also gained the previously-omitted `/archive`, `/ww`, `/downloaderstatus`, and the warning-routing commands (`/enablewarnings`, `/disablewarnings`, `/displaysetup`). (#479)

## [Unreleased]


## [5.34.7] — 2026-05-26

### Added
- **WPC Surface Fronts command & auto-poster.** New `/fronts` slash command fetches the latest surface fronts map from WPC. Background task automatically posts updates to Weather Chat (channel 1016454846326513876) every 15 minutes using SHA-256 hash-based deduplication to detect image changes. Follows the same dedup pattern as other products. (#474, #475)

## [5.34.6] — 2026-05-26

### Fixed
- **Redis dirty-write reconciler never drained at runtime.** `state_store` enqueues failed Redis writes to a durable SQLite dirty-write queue, but `resync_to_redis()` was only invoked on startup and on promotion — the documented "periodic background retry" did not exist. On a long-lived Primary, a transient Redis blip could strand writes locally until the next restart/promotion, leaving the Standby's Redis silently missing them and risking duplicate posts after a failover. The failover `sync_loop` now drains the queue every cycle while Primary (a cheap no-op when the queue is empty), and the stale module docstring was corrected. (#471)
- **`recorder_loop` not supervised by the watchdog.** `RecorderCog` was the only task-owning cog without `MANAGED_TASK_NAMES`, so a stopped VAD recorder loop would never be auto-restarted. Added it to the watchdog's managed-task registry. (#471)
- **Mesoscale Discussions double-posting.** `post_md_now` is invoked by two independent feeds — the NWWS-OI XMPP push (`cogs/nwws.py`) and the IEMBot poller (`cogs/iembot.py`) — for the same MD. Because `posted_mds` is only marked after a successful `channel.send` (so a failed send still retries), the dedup check and the mark were separated by several network `await`s; two near-simultaneous triggers both passed the check and posted twice. Began when NWWS-OI ingestion was restored (2026-05-25), reactivating the second feed. `post_md_now` now reserves the MD in an in-flight set synchronously before any `await`, so the concurrent second call bails; the reservation is released in `finally`. The same latent race in `post_watch_now` is guarded identically as a precaution (not observed for watches, which are far sparser). (#472)

## [5.34.5] — 2026-05-26

### Fixed
- **Persistent Alert Buttons.** The "View Full Text" (PNS) and "View Environmental Evolution" (Tornado Warnings) buttons now survive bot restarts. These views are now registered as persistent views on startup and retrieve state directly from the database, rather than relying on volatile in-memory storage. (#470)
- **Rust Build.** Updated `deploy.sh` to ensure the Rust extension is rebuilt during local deployments. (#469)
- **NWWS Heartbeat.** Reduced log volume by suppressing per-timeout heartbeat messages, now only warning on sustained silence. (#468)

### Dependencies
- **XMPP Pinning.** Pinned the XMPP crate stack to 4.x to resolve upstream compatibility issues in `tokio-xmpp` 5.x. (#466, #467)
- **Rust Math.** Updated `rstar` to v0.13.0. (#456)

## [5.34.4] — 2026-05-25

### Fixed
- **`/md` status command tuple unpack.** `fetch_latest_md_numbers` returns `(list, bool)` but the status cog was assigning the tuple directly, causing `len()` to always return 2. Unpacked to `md_numbers, _` and guarded against `None`. (#458)
- **Warning UI environment data fallback.** `warning_ui.py` fell through to `row["gif_path"]` when `RecorderCog` was absent or the row had no GIF path, raising `TypeError`. Added an `else` branch that sends a "no environmental data" message and returns early. (#458)
- **NWWS reconnect flag reset on clean shutdown.** `self.reconnect = True` and `self.use_ipv6 = False` ran on every exit including normal `CancelledError`; now scoped to the `except asyncio.CancelledError` branch. Also fixed `asyncio.create_task` → `self.bot.loop.create_task` in slixmpp sync callback. (#458)
- **PIL frame handle leak in GIF recorder.** `Image.open()` frames were never closed after `save()`, leaking ~18 file handles per mission. Wrapped the save in a `try/finally` that calls `frame.close()` on each frame. (#458)
- **Mark-before-send race in reports and mesoscale.** `add_posted_report` (reports) and `posted_mds.add` (mesoscale) were called before `channel.send`, so a Discord failure silently lost the product — it would never be retried. Both now write dedup state only after a successful send. (#459)
- **`render_tornado_track` blocking the event loop.** The synchronous Cartopy render was called directly on the event loop in two places. Both call sites now use `await loop.run_in_executor(None, render_tornado_track, ...)`. (#460)
- **HTTP 500 not retried by tenacity.** `http_get_json` returned `None` on 500 responses instead of raising, so tenacity never triggered a retry. Added 500 to the set of status codes that raise `ClientResponseError`. (#461)
- **`get_events_db()` singleton race.** Concurrent first-callers could both open an aiosqlite connection, orphaning one. Fixed with a module-level `asyncio.Lock` and double-checked locking. (#461)
- **Warning routing slash commands open to all members.** `/enablewarnings` and `/disablewarnings` had no permission gate; any guild member could reroute or disable warning types. Both commands now require `manage_guild`. (#462)

### CI
- **Ruff format enforced in CI.** `ruff format --check` added to the lint job; format violations now fail the pipeline. Formatted all pre-existing violations across the codebase. (#463)
- **Coverage floor added.** `--cov-fail-under=20` added to pytest; builds now fail if coverage drops below 20%. (#463)

## [5.34.3] — 2026-05-21

### Fixed
- **Improved observability for silent exception handlers.** Redis connection cleanup in `FailoverCog` now logs a warning (with traceback) if `aclose()` raises instead of silently discarding the error. Sounding source-metadata extraction in `sounding_views.py` now emits a debug-level log on failure instead of swallowing it.
- **Rust NWWS-OI ingestion key mapping and delay parsing.** Fixed silent ingestion failures when `_USE_RUST_NWWS` is active. The Rust XMPP sidecar client now exposes Python-compatible key mappings (`cccc`, `text`, `delay_stamp`) in its output dictionary alongside Rust keys (`office`, `raw_text`). The Python cog uses the `delay_stamp` (parsed from `urn:xmpp:delay` stanzas in the Rust backend) to determine if a message is archived, correcting a mismatch that previously caused all incoming products to bypass delay checks or fail due to key errors.
- **Slixmpp path now correctly detects archived/backlog messages.** `NWWSClient` was not registering the `xep_0203` (Delayed Delivery) plugin, so `msg['delay']['stamp']` always returned `None` and `is_archived` was always `False`. Backlog messages replayed on reconnect flooded through as live products. Fixed by registering `xep_0203` and switching the delay check to use the `datetime` object the plugin provides.
- **Fixed double-counted `messages_received` stat in Rust NWWS sidecar.** The counter was incremented both in the connection loop (once per inbound XMPP stanza) and again in `nwws_try_recv` when Python drained the channel, reporting roughly twice the actual value. Removed the redundant increment from `nwws_try_recv`.
- **Added early-exit guard in Rust NWWS drain loop.** `_drain_rust_nwws` now skips messages with an empty `awipsid` or empty text body before calling `_process_nwws_message`, matching the guard that already exists in the slixmpp path.

## [5.34.2] — 2026-05-20

### Fixed
- **NWWS-OI stanza parser now reads structured payload attributes.** The Rust XMPP parser was whitespace-splitting the first line of the message body and treating `parts[0..3]` as office/ttaaii/awipsid. Most NWWS-OI messages have a blank or non-product body, so this produced garbled log lines like `[XMPP] Received: KNCF issued, valid` — fragments stitched together from unrelated MUC chatter. The previous fix (v5.34.1) papered over the symptom with strict WMO-header validation, which suppressed the noise but also silently dropped legitimate products. Rewrote `parse_xmpp_message` to read the `<x xmlns="nwws-oi">` payload's `cccc`/`ttaaii`/`awipsid`/`issue` attributes (the structured fields iembot actually sends) and pull product text from the payload body. Messages without an nwws-oi payload (status pings, chatter) are skipped silently. Log output now shows real product codes (`KSEW SRUS46 RRMSEW`, `PAJK SRAK57 RR3AJK`, etc.). (#446)

## [5.34.0] — 2026-05-20

### Performance
- **Rust nom-based parsers for hot NWWS path.** Added three new Rust functions to `spc_rust_core`: `parse_warning_polygon()` (full LAT...LON scan, parse, and range-clipping in one pass), `parse_md_number()` (extract Mesoscale Discussion number), and `parse_watch_number()` (extract watch number + type). Each uses nom combinators for zero-copy forward scanning and case-insensitive tag matching. Python wrappers use try-Rust-first pattern with pure-Python fallbacks for backward compatibility. Eliminates redundant regex scans in the NWWS routing hot-path and pre-positions the codebase for B3 (tokio-based XMPP client). All 479 tests passing.

### Added
- **Sounding worker pre-fork at bot startup.** `prefork_sounding_executor()` is now called during bot initialization instead of lazily on first request, eliminating cold-start latency for the first sounding render. Added `max_tasks_per_child=5` to `ProcessPoolExecutor` (Python 3.11+) to cap cumulative memory bloat from SounderPy/MetPy processes — after 5 tasks each worker is recycled.
- **Sounding queue depth monitoring.** Added `sounding_queue_depth()` helper to replace fragile CPython internals (`sem._waiters`) for compatibility with future Python versions.

### Fixed
- **Pre-push hook now works in worktree sessions.** Added venv detection with fallback to `/home/ubuntu/spc-bot/venv/bin/python` for agents running in parallel worktrees without an active venv. Updated both `.git/hooks/pre-push` and `install-hooks.sh` for consistency.

### Removed
- **Deleted broken `scripts/migrate_sqlite_to_upstash.py`.** The script called `_upstash_cmd()` which was removed in v5.26.0. The replacement `scripts/migrate_sqlite_to_redis.py` already exists and is the correct tool for the job.

## [5.33.1] — 2026-05-20

### Fixed
- **Split-brain prevention in `_do_promote`.** If two standbys raced to promote after the lease expired, both ran the full promotion sequence — set `is_primary=True` locally, invalidated caches, then both wrote the lease unconditionally (last-writer-wins). For one heartbeat window both nodes thought they owned primary and could post to Discord. Now the *first* action in `_do_promote` is an atomic `SET ... NX EX HEARTBEAT_TTL`. The NX loser aborts cleanly — no `is_primary=True`, no cache invalidation, no cog load, stays a clean standby. The redundant `_write_lease()` call further down was removed since the lease is already claimed. Manual override flows pass `force=True` through `_promote(force=True)` → `_do_promote(force=True)`, which uses an unconditional `SET` so an operator can still forcibly take the lease from a held primary. 4 new tests in `TestPromoteSplitBrainPrevention` pin the contract, including a two-cog concurrent race against a shared in-memory Redis. (#440)

### Added
- **Fault-injection coverage for `_do_promote` side-effects.** Four new tests in `TestPromoteSideEffectFaultInjection` pin the contract that failures in `mirror_to_sqlite()`, `_rehydrate_bot_state()`, and `resync_to_redis()` during promotion are non-fatal — the bot still loads all cogs, syncs the slash command tree, and ends up `is_primary=True`. The `resync_to_redis()` failure path additionally asserts a `critical=True` operator alert is dispatched (dirty writes from the standby period may have been lost). Worst-case test exercises all three failing simultaneously. Locks in current behavior so a future refactor can't accidentally make any of these failures strand a node mid-promotion. (#439)

## [5.33.0] — 2026-05-20

### Performance
- **`mirror_to_sqlite()` promotion path is no longer N+1.** On failover promotion, the standby pulls authoritative state from Redis and rewrites it to its local SQLite mirror so the new primary has a fresh source-of-truth before accepting writes. The hashes path already used `set_hashes_batch`, but the four posted_* collections (MDs, watches, reports, product_ids) were a per-row `await sqlite_backend.add_*` inside a Python `for` loop — one transaction, one fsync per row. With thousands of cached entries that's seconds of extra promotion latency at the worst possible moment. Added `add_posted_mds_batch`, `add_posted_watches_batch`, `add_posted_reports_batch`, `add_posted_product_ids_batch` in `utils/db.py` — each does a single `INSERT OR IGNORE … executemany` inside one transaction — and rewrote `mirror_to_sqlite` to use them. (#437)

### Fixed
- **Promotion rollback could deadlock on `_role_lock`.** If a cog failed to load during `_do_promote()`, the rollback path called `await self._demote()` — which tries to acquire `_role_lock`. The lock was already held by the enclosing `_promote()` and `asyncio.Lock` is not reentrant, so the task would hang indefinitely with `state.is_primary=True` and cogs unloaded, leaving the node stuck until process restart. Rollback now calls `_do_demote()` directly (the lock is already held by the caller). Added a regression test that exercises the real lock through the public `_promote()` entry point with `asyncio.wait_for(timeout=2.0)` — the previous tests only mocked `_demote` so they couldn't catch this. (#435)
- **Watchdog hydration failures were silently swallowed.** `_hydrate_state()` ran 15 cache loads under `asyncio.gather(..., return_exceptions=True)` and the post-processing only checked `isinstance(r, ...)` for the expected type. A real Redis/SQLite read failure looked identical to "table empty" — the affected cache would start empty and the bot would happily replay duplicate posts. Now logs each exception by name and emits a follow-up rollup warning naming the affected caches. (#436)
- **`CircuitBreaker` re-logged `Circuit OPEN` on every half-open trial flap.** The old code used `if failures == failure_threshold` so each half-open trial that decremented and re-incremented the counter retriggered the warning. The half-open window also had no concurrency guard — every concurrent caller would see the breaker as closed and slam the failing host together. Rewrote as an explicit three-state machine (`CLOSED → OPEN → HALF_OPEN → CLOSED/OPEN`): `Circuit OPEN` only logs on the CLOSED→OPEN edge; only one request slips through during HALF_OPEN; trial failure transitions back to OPEN with an INFO log (no duplicate WARNING). Added 5 dedicated tests pinning the state transitions. (#436)
- **`sync_loop` periodic-cleanup check could fire 0 or 2 times in a window.** `if int(time.time()) % (SYNC_INTERVAL * 5) < SYNC_INTERVAL` was sensitive to clock drift and NTP slews. Switched to the deterministic `self.sync_loop.current_loop % 5 == 0` counter. (#436)
- **`resync_to_redis()` no longer silently drops failed replays.** The startup replay loop in `utils/state_store.py` treated any non-`_RedisUnavailable` exception the same as success: it logged the traceback and queued the row for deletion. A future schema change or malformed row would silently nuke every queued dirty write on the next startup with no operator visibility. Added a `retry_count` column to `dirty_writes` and a `dirty_writes_dead` quarantine table; failures now bump `retry_count`, and after `_MAX_REPLAY_RETRIES` (5) the row is moved to the dead-letter table instead of being deleted. `bump_dirty_retry`, `quarantine_dirty_writes`, and `get_quarantined_writes` added to `utils/db.py`; the resync result dict now also reports `retried` and `quarantined` counts. (#437)

### Changed
- **Reduced state_store log volume during Redis outages.** Each of the 16 `add_*` / `set_*` paths previously logged at WARNING when Redis failed and the write was queued for the dirty-write reconciler. During a 60-second blip with severe weather firing, this could flood `spc_bot.log` with dozens of duplicate warnings. Per-write logs are now DEBUG; a single rate-limited summary WARNING fires at most once every 30 seconds (`[STATE] Redis reconciler: N write(s) queued in the last 30s`). The durability guarantee (SQLite mirror + dirty queue) is unchanged. (#436)
- **Small cleanups across `utils/` and `cogs/failover.py`.** `utils/state_store.py:_redis_cmd()` no longer stringifies its arguments — redis-py handles ints/floats/bytes natively, so the coercion was lossy if non-text payloads were ever passed through. `utils/state.py:BoundedFIFOKeys.append()` replaces the eviction `while` loop with an `if` (only one entry can be added per call, so only one eviction is ever needed). `utils/state.py:BotState.to_dict()` now uses a walrus binding on `.get("expires")` so mypy can narrow the Optional in the watch-serialization branch. `cogs/failover.py:_PROCESS_UUID` is now a per-instance `self._process_uuid` reassigned in `FailoverCog.__init__`, and `_node_identity` became a method — so `importlib.reload(cogs.failover)` during tests yields a fresh identity instead of reusing a stale module-level constant. `.gitignore` now excludes `bot_state.db` so a transient 0-byte file at the repo root can't sneak into CI state. (#438)

## [5.32.2] — 2026-05-20

### Fixed
- **`/download` time-range selection message not deleted after upload.** When `/download` was invoked with site codes but no time preset (e.g. `/download sites:KDTX KCLE KBUF`), the followup embed showing `TimeRangeView` was sent without `wait=True`, discarding the returned message object. It was never appended to `messages_to_delete`, so it persisted in the channel even after all uploads completed successfully. (#433)
- **Redis replication not configured on standby nodes by `deploy.sh`.** `deploy.sh` wrote `IS_PRIMARY=false` and `ELECTION_REDIS_URL` to `.env` but never touched Redis itself — replication had to be configured manually after every fresh provision, and runtime drift (e.g. an accidental `REPLICAOF NO ONE` during failover testing) went undetected on re-runs. Added `_configure_redis_replication()` which parses `ELECTION_REDIS_URL`, runs `redis-cli REPLICAOF` for immediate effect, persists the `replicaof` line to `redis.conf` so it survives Redis restarts, and verifies the link came up. Called on both new standby setup and the already-configured re-run path so `spcupdate` self-heals replication drift. (#432)

## [5.32.1] — 2026-05-19

### Fixed
- **VTEC regex now requires `/O.` operational-class prefix instead of pinning to `KWNS`.** The previous fix (#427) pinned the watch VTEC regex to `KWNS` (SPC's WFO identifier) to block test-watch ETN collisions, but the NWS Alerts API returns Watch County Notifications issued by local WFOs — none of which carry `KWNS`. This caused the API to return zero watches, falling through to the HTML scraper fallback. (#428)
- **HTML fallback tornado classifier matched site nav on every SPC page.** `_TORNADO_WATCH_RE = re.compile(r"Tornado Watch")` matched the navigation menu link present on every SPC page, classifying all active Severe Thunderstorm Watches as Tornado Watches when the HTML fallback was active. Changed to `"Tornado Watch Number"` to match only the actual product title. (#428)
- **Stale WFO WCN with ETN `0001` posted as active watch.** KILN (NWS Cincinnati) had an un-cancelled Watch County Notification continuation for SPC watch #0001 from January 2026 in the NWS Alerts API. The `/O.` filter let it through; the bot treated it as a currently-active watch and fetched `ww0001.html`. After parsing the NWS API, the bot now cross-validates ETNs against the SPC watch index page — only watches listed there are kept. If the SPC page is unreachable, all NWS API results are accepted (fail-open). (#429)
- **`/failover` SELECT callback could hit 40060 "Interaction already acknowledged".** The callback did Redis work before calling `interaction.response.edit_message()`. Component interactions have a 3-second acknowledgment window; a slow Redis call (up to 5s socket timeout) would cause the `edit_message()` to fire after Discord auto-expired the token. Fixed by deferring immediately at callback entry and using `edit_original_response()`. (#430)
- **Malformed heartbeat timestamps crashed `/failover` after `defer()`.** The `int(ts_str)` list comprehension in `failover_slash` raised `ValueError` on any non-integer entry in the nodes hash. The exception propagated after the interaction was already deferred, so discord.py's default error handler tried `response.send_message()` on a deferred interaction — surfacing as another 40060. Each entry is now parsed individually with try/except. (#430)
- **No error handler for deferred `/failover` interactions.** Without `cog_app_command_error`, unhandled exceptions after `defer()` produced confusing 40060 log noise with no user-facing feedback. Added handler that uses `followup.send()` when the interaction is already done. (#430)

## [5.32.0] — 2026-05-19

### Performance
- **O(1) `posted_product_ids` membership lookup.** `posted_product_ids` was a `deque(maxlen=1000)`, so the `in` check on the hot dedup path (NWWS + IEMBot triggers + NWS poll all hit it per product) was O(N) — measurable during severe weather outbreaks with thousands of products/sec. Replaced with `BoundedFIFOKeys`, a dict-backed FIFO (`dict` preserves insertion order in CPython 3.7+) that gives O(1) `in` / `append` / `remove` while keeping the same drop-in interface (`.append()`, `.remove()` with `ValueError`, `.extend()`, `in`, `list()`, `len()`). No call sites changed. (#423)
- **Bumped connection pool limits.** `aiohttp.TCPConnector` `limit` 20→100 and `limit_per_host` 10→25 in `utils/http.py`; SQLite read pool `_READ_POOL_SIZE` 3→10 in `utils/db.py`. The old caps throttled the bot during outbreaks when radar-frame bursts, concurrent slash commands, and warning-image downloads stacked up against the connector before reaching the server. 25 per host is well below what NWS API / IEM Autoplot tolerate. (#424)

### Fixed
- **`_periodic_cache_cleanup` task leaked on shutdown.** `on_ready` spawned it via bare `asyncio.create_task(...)` without saving the handle. `_shutdown()` never knew about it, so on a clean stop the task kept running until the event loop was torn down — systemd saw a stale process and waited out the kill timeout. Saved the handle as a module global `_cache_cleanup_task`; `_shutdown()` now cancels it alongside `watchdog_task`, `periodic_sync`, and `snapshot_events_task`. (#425)

### Changed
- **Watchdog probes now run on STANDBY too.** The probe block previously bailed out entirely on standby, meaning the replica's aiohttp session, TCP keepalives, and DNS cache went cold; the first request after a failover-promotion would have to redo all that work just when latency mattered most. Probes run on both roles; Discord-channel alerts (`session reset`, `probe degraded`) and the session-teardown action stay gated to Primary so the two nodes don't duplicate noise, and managed-task supervision still short-circuits on standby (those cogs aren't loaded there). (#425)

## [5.31.0] — 2026-05-19

### Added
- **Per-endpoint HTTP latency tracking.** `bot.state.http_latency` was a single rolling average across every HTTP call, which made "the bot feels slow" reports indistinguishable between NWS API delays, IEM Autoplot delays, and Discord delays. `utils/http.py` now extracts the hostname from each request URL and passes it through the latency callback; `BotState.update_http_latency()` keeps a per-host rolling window (`deque(maxlen=100)`). `BotState.http_latency_percentiles(host)` returns nearest-rank P50/P95 in seconds. `/status` surfaces the top 5 hosts by sample count under a new "🌐 HTTP Endpoints" field. The callback is back-compatible with the legacy `cb(latency)` signature via a `TypeError` fallback. (#420)

### Fixed
- **`active_warnings` / `active_watches` could strand expired entries forever.** The normal cleanup path is the NWS-API expiry detection in `_handle_disappeared_warnings`, but a single missed poll (NWS API blip, network glitch) left entries lingering with no recovery. Added `BotState.sweep_active(grace_minutes=60)` which evicts entries whose VTEC `end` timestamp (warnings) or `expires` datetime (watches) is past `now - grace`. Null VTEC times (`000000T0000Z`), missing fields, and unparseable timestamps are kept — only positive evidence of expiry triggers eviction. The hourly `prune_posted_warnings_loop` task now invokes the sweep alongside `posted_warnings` pruning. (#421)

### Tests
- **Failover fault-injection coverage for `_do_promote` cog-load rollback.** v5.29.0 (#412) added per-cog transactional rollback inside `_do_promote` — if `bot.load_extension(name)` raises on any cog in `ALL_EXTENSIONS`, the previously-loaded cogs are unloaded in reverse order and the bot demotes back to standby. That code path had no test coverage; a regression would only surface during a real failover-with-broken-cog combination at 3am. Added 4 fault-injection tests in `tests/test_failover_coverage.py::TestPromoteFaultInjection` covering: partial-load reverse-order rollback, rollback that continues past an `unload_extension` failure, full-success path (tree.sync called, no demote), and first-cog-fails (no unloads but still demotes). (#419)

## [5.30.0] — 2026-05-19

### Fixed
- **`posted_warnings` in-memory dict was unbounded.** v5.29.0 plumbed `prune_posted_warnings` to wipe stale rows from Redis and SQLite, but nothing called it at runtime and the in-memory dict on `BotState` had no cap analog to `posted_product_ids`' `deque(maxlen=1000)`. During multi-day outbreaks the dict grew without bound. Added `BotState.prune_posted_warnings()` which prunes all three layers and preserves unconfirmed placeholders, plus an hourly `prune_posted_warnings_loop` task in `WarningsCog` (cap 500, primary-only). (#416)

### Refactored
- **`posted_warnings` mutations now go through `BotState.claim_posted_warning` context manager.** Resolves the v5.28.0 Opus-review "three sources of truth" issue. `cogs/warnings.py` had eight call sites that mutated `bot.state.posted_warnings` and `posted_product_ids` directly — placeholder writes, `del`/`pop` rollbacks, and `posted_product_ids.remove()` — none of which touched SQLite or Redis, causing silent drift between the three storage layers. Same class of bug that caused the sounding dedup race in #390. Added `PostedWarningClaim` async context manager that writes a placeholder on enter, exposes `confirm()`/`abort()`, and auto-rolls back the in-memory placeholder on any exit path (early `continue`, leaked exception, missed `confirm`). Added `BotState.remove_posted_warning()` and `BotState.remove_posted_product_id()` that delete across memory + SQLite + Redis (with `_replay` queueing for offline Redis), plus matching `utils/db.py` helpers. Both NWWS-trigger and NWS-poll paths in `cogs/warnings.py` now use the context manager; no direct `posted_warnings[...]` writes remain in cogs. (#417)

## [5.29.0] — 2026-05-19

### Added
- **Failover & Sync Observability.** Added counters for failover transitions, lease renewals, and sync failures to `BotState`. These are now displayed in the `/status` command under the Cluster Status section to provide better visibility into the health of the high-availability mechanism.
- **`posted_product_ids` Persistence.** The cross-feed deduplication state (`posted_product_ids`) is now persisted to Redis and SQLite. This ensures that failovers or restarts no longer reset the dedup history, preventing duplicate posts from concurrent NWWS and IEM feeds during transitions.

### Fixed
- **Unbounded Redis State Growth.** Fixed several state pruning functions (`prune_posted_warnings`, `prune_posted_mds`, etc.) to explicitly remove stale entries from Redis. Previously, these only cleaned up the local SQLite mirror, leading to indefinite memory and storage growth in the shared Redis instance.
- **Failover Promotion Race & Partial Load.** Implemented transactional promotion in `FailoverCog`. If any extension fails to load during promotion to Primary, the bot now performs a full rollback (unloading successfully loaded cogs) and demotes itself back to Standby. This prevents the bot from entering a "partial Primary" state where only some alerting feeds are active.
- **Concurrent Mutability in `to_dict()`.** Wrapped dictionary and set iterations in `BotState.to_dict()` with `.copy()` and `list()` to prevent `RuntimeError: dictionary changed size during iteration` when the state is serialized for metrics or failover sync while background tasks are updating it.
- **`prune_posted_mds` Random Eviction.** Fixed a bug where MDs were being randomly evicted from Redis instead of the oldest ones. Eviction now uses the authoritative ordered list from SQLite.

### Refactored
- **State Encapsulation.** Refactored `BotState` from a passive data structure into an active state manager. Moved dual-write logic (memory + persistence) into `async` methods in `BotState` (e.g., `add_posted_warning()`). This ensures consistency and simplifies cog logic by removing the burden of manual persistence calls.

## [5.28.2] — 2026-05-19

### Changed
- **Upgraded redis client from 5.x to 7.x.** Asyncio support is now built-in; dropped the `[asyncio]` extra which was removed in redis 7.x. (#414)
- **Bumped mypy to >=1.19.1.** Stricter type checking; CI scope (utils/ only) passes clean. (#414)
- **Bumped fakeredis to >=2.35.1.** Test-only dependency, no runtime impact. (#414)
- **Bumped ruff to >=0.15.13.** Patch bump, no linting changes. (#414)

### Fixed
- **Dependabot repeatedly reopening broken artifact action bumps.** `actions/upload-artifact` and `actions/download-artifact` were previously bumped to v7/v8 (PR #335) and had to be reverted twice (#396, #397) due to breakage in the Docker multi-platform digest workflow. Added ignore rules to `dependabot.yml` to suppress future PRs for these until the root cause is diagnosed. (#413)

## [5.28.1] — 2026-05-19

### Fixed
- **`add_significant_event` received a tuple instead of `event_id` string.** Two call sites passed the raw `find_matching_tornado` return value (an `(event_id, vtec_id)` tuple) directly as `event_id`, causing SQLite to fail with `type 'tuple' is not supported` on every matched tornado warning and LSR. Significant events were still posted to Discord; only database logging was broken. (#410)
- **`posted_warnings` not snapshotted in `to_dict`.** State serialization omitted `posted_warnings`, so any warnings posted since the last Redis sync were lost across restarts or failover. (#400)
- **Dead `upstash` alias removed from state store.** Leftover `upstash` import alias was referenced nowhere and caused a lint warning. (#400)
- **`_promote`/`_demote` not serialized under a lock.** Concurrent promotion and demotion calls could race on shared state. Both methods now acquire the failover lock before executing. (#398)
- **Unhandled `SystemExit` on cog unload failure.** A failing cog unload previously swallowed the exit, leaving the process in a degraded state. Now calls `os._exit(1)` to force a clean restart. (#398)
- **RwLock `.unwrap()` panics in Rust extension.** Several `RwLock` acquire sites used `.unwrap()`, which could panic on a poisoned lock and kill the process. Replaced with proper `PyErr` conversion so Python sees a recoverable exception. (#399)
- **`upload-artifact` version mismatch in `tests.yml`.** CI used `upload-artifact@v4` in `tests.yml` while `docker-publish.yml` had been aligned to `@v8`; aligned to `@v4` across both files. (#397)

## [5.28.0] — 2026-05-18

### Fixed
- **Sounding dedup race in `post_soundings_for_watch`.** The inner `_check_avail` coroutine checked a pkey against `_posted_watch_soundings` but returned it unclaimed; keys were claimed after the gather completed. A concurrent `monitor_special_soundings` or `monitor_high_risk_soundings` task could claim the same pkey during the gather's yield window, resulting in duplicate sounding posts on high-risk days. Fixed by claiming the in-memory set synchronously inside `_check_avail` before returning. (#390)
- **Incomplete warning rollback on Discord send failure.** On `channel.send()` raising `Forbidden` or `HTTPException`, `active_warnings[vtec_id]` was not rolled back alongside `posted_product_ids` and `posted_warnings`. The stale entry triggered a spurious expiry notification on the next 30 s poll cycle. (#389)
- **`posted_product_ids` wiped entire dedup history at 1000 entries.** A blunt `.clear()` at 1000 entries discarded all dedup history, causing previously-posted warnings to be reposted during large multi-day outbreaks. Replaced the plain `set` with `deque(maxlen=1000)` for automatic FIFO eviction. (#389)
- **Startup hydration crash on corrupted seqnum.** Bare `int()` casts on DB-sourced `iembot_last_seqnum` and `iembot_botstalk_last_seqnum` would raise `ValueError` on any malformed row, aborting the entire startup hydration sequence before the bot connected to Discord. Wrapped in `try/except ValueError` with fallback to 0. (#391)
- **Watchdog module-level sets grew unbounded across cog reloads.** `_task_alerted`, `_task_seen_running`, and `_task_fail_counts` accumulated stale task names and were never pruned, causing missed alerts after cog name changes. Now pruned to the current live task set each watchdog tick. (#391)
- **Failover promotion silent on `resync_to_redis()` failure.** If dirty-write resync failed during `_promote()`, the promoted primary silently ran with incomplete dedup state. Now fires a critical bot alert so the operator can monitor for duplicate posts. (#392)
- **Corrupt VTEC entries silently dropped.** `get_all_posted_warnings()` discarded JSON-decode failures with no log output. Now logs at WARNING so Redis memory errors are visible. (#393)
- **Resync loop abort gave no count of pending writes.** On `_RedisUnavailable` during `resync_to_redis()`, the loop broke without logging how many writes remained unsynced. Now logs the remaining count before breaking. (#393)
- **DB migration swallowed all exceptions.** `ALTER TABLE ... ADD COLUMN` caught every exception including real failures. Now only silences "column already exists"; unexpected errors are logged at ERROR. (#393)

### Fixed (Day 1 Outlook)
- **Auto-refresh showed stale SPC Day 1 risk after new issuance.** The auto-refresh loop called `build_embeds()` which read `max_risk` from the module cache without triggering a fetch, leaving the displayed risk stale until something else refreshed the polygon. Added `get_high_risk_polygon()` call inside `build_embeds()` to match the `/status` slash command path. (#388)

### Changed
- **CI artifact version alignment.** `upload-artifact` and `download-artifact` in `docker-publish.yml` aligned to `@v8`. (#394)

### Refactored
- Removed `strict=True` from `zip()` in `cache.py` — lists are guaranteed same-length by construction. (#394)
- Capped `_RETRY_CACHE` in `http.py` at 16 entries with LRU eviction. (#394)

## [5.27.0] — 2026-05-14

### Fixed
- **Standby promotion never triggered on primary crash.** After the Upstash → self-hosted Redis migration, the standby pointed its election client at its own local Redis replica. When the primary crashed, the replica kept stale lease data alive until the 420 s TTL expired instead of surfacing a `ConnectionError` — so the failure counter never ticked and promotion was blocked indefinitely. Fix: add `ELECTION_REDIS_URL` env var; standby nodes point this at the primary's Tailscale Redis address so connection failures are the immediate promotion trigger.
- **`REPLICAOF NO ONE` silently failed on promotion.** `_promote()` issued the command via the election client, which points to the (now-dead) primary's Redis. Fix: use a dedicated `_build_local_redis()` client (always localhost) for this command so the standby's local replica detaches cleanly and write commands succeed immediately.
- **Node identity never updated after promote/demote.** `self._identity` was set once at init with an `S:` prefix and never changed, so the nodes hash always advertised `STANDBY` for the promoted node regardless of actual role. Fix: update `self._identity` in `_promote()` and `_demote()` so the next heartbeat `HSET` reflects the correct role.
- **Duplicate node entries after restarts.** Each process restart generated a new UUID, leaving the previous identity key in the nodes hash for up to 90 s. Fix: `_cleanup_own_stale_entries()` purges all prior same-hostname entries on `cog_load` and immediately after `_promote()` updates the identity.
- **`/status` didn't distinguish failover-promoted nodes.** A promoted standby was styled identically to a normal primary. Fix: `/status` now shows an orange **PRIMARY ⚠️ FAILOVER** embed and the cluster section shows `🟠 PRIMARY ⚠️ FAILOVER` when `IS_PRIMARY=false` but the node is acting as primary.

### Added
- **`ELECTION_REDIS_URL` env var.** Standby nodes set this to the primary's Redis URL via Tailscale (e.g. `redis://<tailscale-ip>:6379/0`). The failover cog uses this URL exclusively for lease and election traffic; `REDIS_URL` (localhost) continues to serve application state reads from the local replica. Leave unset on the primary (defaults to `REDIS_URL`).

### Documentation
- Updated `README.md`, `CONTRIBUTING.md`, `PROJECT_STRUCTURE.md`, `.env.example`, and `deploy.sh` to reflect the self-hosted Redis architecture: removed all Upstash references, documented `ELECTION_REDIS_URL`, added `ELECTION_REDIS_URL` prompt to the standby setup flow in `deploy.sh`.
- Updated all four affected wiki pages (High Availability & Failover, Configuration Guide, State Persistence Model, Home) to describe the Redis + Tailscale setup with the correct environment variable tables and measured failover timing.
- Renamed `scripts/migrate_sqlite_to_upstash.py` → `scripts/migrate_sqlite_to_redis.py` and rewrote it against the current `state_store` API; the old script called the removed `_upstash_cmd` function and would have crashed on use.

## [5.26.1] — 2026-05-14

### Fixed
- **Redis backend rewrite (v2).** The initial v5.26.0 migration had critical bugs identified in code review and was reverted immediately after tagging; this release ships the corrected implementation. Fixes: socket timeouts applied to `ConnectionPool` (C1); exception classifier now catches `redis.exceptions.ConnectionError`, `TimeoutError`, `BusyLoadingError`, and `OSError` instead of bare built-ins (C2); `mirror_to_sqlite` paginates `SCAN` until cursor returns 0 (C3); `_release_lease` uses an atomic Lua check-and-delete to eliminate TOCTOU races (C5); `_renew_lease` uses a Lua conditional `SET` that only writes if the caller still holds the key (C6); a single long-lived `Redis` client replaces per-command instantiation (C7); `HGETALL` with `decode_responses=True` returns a `dict` and `failover_slash` now iterates `.items()` correctly (M3); all internal identifiers renamed from `_upstash_*` / `_Upstash*` to `_redis_*` / `_Redis*` (MI).
- **ReadOnlyError on Redis replica.** Standby nodes running against a Redis replica now catch `redis.exceptions.ReadOnlyError` in `_redis_cmd` and return `None` instead of propagating the exception. Log level demoted to `DEBUG` to eliminate per-heartbeat spam during normal standby operation.
- **Replica promotion on failover.** When `_promote()` is called, the cog now issues `REPLICAOF NO ONE` to detach the local Redis from its primary before taking the leader lease, so write commands succeed immediately after promotion without manual operator intervention.
- **CI gate ordering.** `docker-build` job now `needs: [test, mypy, rust]` — previously it could run while type checks were still failing. Fixed an unused `patch` import in `test_failover_coverage` and corrected `redis`/`redis.asyncio` mypy import aliases that were producing false-positive errors.

## [5.26.0] — 2026-05-14

### Added
- **Local Redis backend.** Replaced the Upstash REST API with a self-hosted Redis 7+ backend for all shared state and leader-election operations. Auto-detects backend via `REDIS_URL` / `REDIS_HOST` env vars; falls back to Upstash if `REDIS_UPSTASH_URL` is set. Eliminates external API quota consumption and reduces state-operation latency. (#372)
- **Upstash quota stats in `/status`.** The Environment field now shows daily Upstash command usage and remaining quota when running in Upstash-backed mode. (#371)

### CI
- **Rust toolchain in test job.** Added `dtolnay/rust-toolchain@stable` and `Swatinem/rust-cache@v2` to the `test` job so Rust extensions are built and exercised during CI runs, not just in the standalone `rust` job.
- **CI/CD pipeline improvements.** Parallel test execution with `pytest-xdist -n auto`; Docker layer cache reordering so `apt-get install` is not invalidated by code-only changes; `Dockerfile.ci` merged `apt-get` + `rustup` into a single `RUN` to eliminate cached apt layers.

## [5.25.2] — 2026-05-13

### Added
- **`/wxsummary` command.** Fetches the live weather briefing from Project WxEye (updates every 20 min) and renders a Discord embed with briefing text, SPC Day 1 risk, WAI, top state, field activity (chasers/live), and tags — color-coded by tone (active/elevated/light/calm).

### Fixed
- **Hyphenated EF ratings in PNS parser.** `_PNS_RATING_RE` and the inline `re.findall` in `ReportsCog` now match both `EF2` and `EF-2` style tornado ratings from NWS post-event narratives. Some NWS offices emit the hyphenated form; the old regex silently dropped those ratings, causing incorrect max-EF detection.

## [5.25.1] — 2026-05-13

### Added
- **Warning type breakdown on `/status`.** The Active Warnings line in the Environment field now shows per-phenomenon counts when warnings are active (e.g. `🌪️ TOR \`2\` | ⛈️ SVR \`5\` | 🌊 FFW \`1\``). Falls back to a plain count when no warnings are active.

### Fixed
- **`followup.send` missing `wait=True`.** Six `interaction.followup.send(...)` calls in `status.py`, `watches.py`, and `radar/downloads.py` were assigning the result to `view.message` without `wait=True`. Discord returns `None` in that case, so view auto-update and edit paths (live-refreshing `/status`, `/taskmgr`, `/logs`, watch paginator, download progress) were silently receiving `None` and could not edit themselves.
- **`post_sounding` phantom kwarg.** A `followup=True` keyword argument was passed to `post_sounding` in `sounding.py`; the parameter does not exist in the function signature.
- **`_iem_to_clean_data` return type.** Annotated `-> dict` but returned `None` on empty profiles; corrected to `-> dict | None`.
- **`_read_lease_holder` return type.** Cast the untyped `_upstash` result to `str` before returning to satisfy the `str | None` annotation in `failover.py`.
- **`warnings.py` channel return type.** Cast `get_channel()` results to `discord.abc.Messageable` to match the method's declared return type.

### Performance
- **Ruff hygiene sweep.** 371 auto-fixed violations across 39 files: trailing/blank-line whitespace, unsorted import blocks, redundant `str.split`, and unused imports.

## [5.25.0] — 2026-05-13

### Performance
- **Tenacity Retry Decorator Hoisted.** `http_get_bytes_conditional` and `http_get_json` previously reconstructed a `tenacity.retry(...)` decorator object on every invocation, paying the full strategy-compilation cost per HTTP call. Decorators are now built once at module load and cached by attempt-count in `_RETRY_CACHE`; the common cases (1–4 attempts) are pre-populated at import time. Non-default counts are lazily memoized on first use.
- **Vectorized Station Coordinate Transform.** Replaced two `df.apply(lambda row: ...)` calls in `_fetch_stations` with column-wise `np.where` expressions. The old approach iterated row-by-row in Python (~900 iterations per startup fetch); the new approach evaluates the hemisphere-sign conversion in a single vectorized pass over the entire column.
- **Strip-Once Station String Columns.** `ICAO`, `NAME`, and `LOC` columns in the RAOB station list are now `.str.strip()`-ed once at load time (inside `_fetch_stations`) instead of on every call to `find_nearest_stations` and `resolve_location`. Removes up to 4 redundant `.strip()` operations per station lookup.
### Fixed
- **Mesoscale MD Index State Race.** Added a module-level `asyncio.Lock` (`_md_index_lock`) around the full body of `fetch_latest_md_numbers`. Concurrent callers from the `auto_post_md` background loop and the `/md` slash command handler could interleave the lazy hydration of `_md_index_head` / `_md_index_unreachable` with subsequent mutations, causing duplicate state-store reads or torn writes.
- **Circuit Breaker Log Noise.** Demoted the per-request "Circuit open for {host}, failing fast" log from WARNING to DEBUG. The breaker fast-failing is the desired behavior — the actual state transition is still logged at WARNING (`reached N failures. Circuit OPEN`) and INFO (`recovered. Closing circuit`). A single upstream outage was producing 20× amplification (one trip warning + tens of fast-fail warnings until recovery).
- **LRU Hydration Order.** `hydrate_validators_from_store` now inserts entries via `_validators_set` instead of `OrderedDict.update`. The bulk `.update` bypassed move-to-end and the max-size eviction path, so a large persisted store could silently exceed `_VALIDATORS_CACHE_MAX` and leave the LRU order undefined for items loaded at startup.
- **Executor Drain on Shutdown.** `shutdown_executors` now passes `wait=True, cancel_futures=True` to both `ProcessPoolExecutor.shutdown` calls. The previous `wait=False` caused in-flight hodo/sounding renders to be abandoned mid-write on SIGTERM, risking partial image files and silent result loss.

### Rust
- **Phase 7 — VAD Plotter Math.** Ported `compute_shear_mag`, `compute_sr_flow`, and `clip_profile` from `lib/vad_plotter/params.py` to Rust. Includes a vectorized `linspace` helper and a `_interp_linear` implementation that matches NumPy's `left=nan, right=nan` boundary behavior. Verified at `1e-9` tolerance against the Python implementations using the `test.vwp` fixture.
- **Phase 8 — Batch Spatial Joins.** Added `find_nearest_stations_batch`, `points_in_polygon_counts`, and `points_in_polygon_lookup` to the Rust core (`utils/geo`). Refactored `find_nearest_stations` in `cogs/sounding_utils.py` to use the Rust `find_nearest_indices` fast-path, replacing a row-wise Pandas `nsmallest`. Parity and integration tests in `tests/test_rust_phase8.py`.

### Fixed
- **International Sounding Availability.** `filter_stations_with_data` now falls back to a direct Wyoming/GSL archive probe when IEM returns empty results for non-CONUS stations (e.g. SBSM/Santa Maria). Previously these stations incorrectly showed "no recent data" despite full profiles being available in the Wyoming archive. Rolled into a unified `get_available_sounding_times` helper in the follow-up fix so the Wyoming probe applies consistently across all availability checks.
- **Sounding Theme Toggle.** Label changed to "Switch to Dark/Light Mode" for clarity, emojis standardized, and button row indexing fixed to prevent UI collisions with other sounding controls.
- **Upstash Daily Quota Guard.** Added a local daily command counter in `utils/db.py` and a soft-quota guard in `_upstash_cmd`. Logs a `WARNING` when usage reaches 8,000 (80% of the 10,000-command free tier) and raises `_UpstashUnavailable` to fall back to SQLite at the hard limit. The warning is emitted once per process per day to avoid log spam.
- **NWWS Ping Loop Leak.** `NWWSClient.disconnect` now explicitly cancels and clears `_ping_task`, preventing the keep-alive loop from outliving the XMPP session across reconnects.

### CI
- **Parallel test execution.** Added `pytest-xdist` to `requirements-dev.txt` and enabled `-n auto` in the CI test step, distributing tests across all available CPU cores. `pytest.ini` addopts unchanged so local runs stay serial.
- **Docker layer cache efficiency.** Reordered the runtime `Dockerfile` so the `apt-get install` layer appears before `COPY --from=builder /wheels`; code-only changes no longer invalidate the OS package layer.
- **Dockerfile.ci layer reduction.** Merged the `apt-get` install and `rustup` bootstrap into one `RUN` instruction with combined cleanup, ensuring the apt cache is never persisted in the image.

## [5.24.0] — 2026-05-11

### Fixed
- **IEM Hour Filter.** Removed a tautological `if (now - timedelta(hours=h)) < now` guard in `get_available_sounding_times_iem` that silently excluded the current UTC hour from all IEM availability checks.
- **Station Cache Race.** Added a module-level `asyncio.Lock` with double-checked locking to `get_raob_stations` so concurrent callers at startup no longer each issue a redundant RAOB CSV fetch.
- **Availability Cache Eviction.** `_AVAILABILITY_CACHE` now evicts expired entries when the cache exceeds 2,000 entries, preventing unbounded memory growth over long uptimes.
- **Dead `n_times` Parameter.** Removed the unused `n_times` parameter from `filter_stations_with_data`; the IEM fast path returned before it was ever read, and all call sites already omitted it.

### Performance
- **Haversine Batch Vectorisation.** `find_nearest_stations` now calls `haversine_batch` (Rust fast-path) instead of a row-wise `df.apply` across ~900 RAOB stations. Eliminates the unnecessary DataFrame copy and reduces per-call overhead from ~900 Python haversine calls to a single Rust batch call.
- **Pre-compiled Geographic Regex.** `_GEOGRAPHIC_GARBAGE` (133 keywords) is now compiled once into a single `re.Pattern` (`_GEOGRAPHIC_GARBAGE_RE`) at module load instead of recompiling up to 133 patterns per county string. `_STATE_REGEX` is likewise pre-compiled. Combined, this drops per-county regex overhead from 133 fresh `re.compile` calls to 1 `.search()` call.
- **Shared aiohttp Session.** Routed `utils/discord_gateway.py` (gateway URL lookup) onto the shared `ensure_session()` client, eliminating a new TLS handshake per call.
- **Shared aiohttp Session.** Routed `utils/discord_gateway.py` (IP geolocation via ipinfo.io) onto the shared session.
- **Shared aiohttp Session.** Routed `cogs/status.py` (public IP lookup via ipinfo.io) onto the shared session; was previously ignoring the `utils.http` import it already had.
- **Shared aiohttp Session + Circuit Breaker.** Routed `cogs/sounding_utils.py::geocode_city` (Nominatim) onto the shared session and added circuit-breaker protection that was previously bypassed entirely.
- **Shared aiohttp Session.** Routed `utils/events_db.py::set_syncthing_folder_mode` (Syncthing REST API) onto the shared session; preserves the per-request `X-API-Key` and `Content-Type` headers.

### Tooling
- **mypy gate.** Added a `mypy` CI job (lenient: `--ignore-missing-imports --follow-imports=silent`) scoped to `utils/` where types are cleanest. Scope expands incrementally as annotations land in other modules.
- **Pre-commit hooks.** Added `.pre-commit-config.yaml` with `pre-commit-hooks` (trailing whitespace, EOF fixer, YAML/TOML check), `ruff` lint + format, `mypy` on `utils/`, and local hooks for `cargo fmt --check` and `cargo clippy -- -D warnings`.
- **Rust clippy/fmt in CI.** Added a `rust` CI job using `dtolnay/rust-toolchain@stable` (with `rustfmt` and `clippy` components) and `Swatinem/rust-cache`. Runs `cargo fmt --check` and `cargo clippy --all-targets --all-features -- -D warnings`. `cargo test` is deferred until the crate is restructured to gate `pyo3/extension-module` behind an optional feature (current setup leaves test binaries unable to link against libpython).

## [5.23.0] — 2026-05-11

### Added
- **Per-Type Warning Channels.** Route TOR/SVR/FFW/SPS warnings to dedicated channels configured via `TOR_CHANNEL_ID`, `SVR_CHANNEL_ID`, `FFW_CHANNEL_ID`, `SPS_CHANNEL_ID` env vars. Runtime overrides via `/enablewarnings`, `/displaysetup`, and `/disablewarnings` slash commands persist to state_store for seamless cross-node config.
- **Sounding Image Disk Caching.** Sounding plot images are now cached to disk with hash-based deduplication. Enables faster re-posts for repeated location/time combinations by skipping expensive render cycles.

### Fixed
- **Sounding Queue Deadlock Protection.** Added 60-second timeout wrapper around rendering tasks. Prevents a hung sounding plot from blocking all subsequent requests via permanent semaphore starvation.
- **Worker Pool Hardening.** Increased sounding workers from 1 to 2 even on 2-core systems, preventing total pool starvation when a single plot is sluggish. Improved semaphore acquisition logging and fixed ephemeral status message cleanup.
- **Sounding Interaction Token Expiry.** Protected Discord message edits with try/except to prevent silent crashes when interaction tokens expire during long-running plots.
- **Warning VTEC Phenomenon Handling.** Fixed Severe Weather Statements (SWS) routing to correctly use VTEC phenomenon (SV) for channel resolution instead of event type label.

## [5.22.0] — 2026-05-10

### Added
- **Worker Pool Separation.** Split the shared process pool into dedicated "Fast Hodo" and "Heavy Sounding" pools. This ensures that rapid-fire tornado hodograph updates are never blocked by heavyweight sounding generation.
- **Sounding Queue Management.** Introduced an `asyncio.Semaphore` and a queuing system for sounding plots. Users now receive real-time feedback (*"⌛ Plot Queued (Position X)..."*) when the pool is saturated.
- **Database Connection Pooling.** Implemented a read/write split and connection pooling for the local SQLite state database. Uses a dedicated write connection and a pool of 3 read-only connections to prevent read stalls during heavy write volume.

## [5.21.4] — 2026-05-10

### Fixed
- **RAOB Station Elevation Crash.** Fixed a regression in `cogs/sounding_utils.py` where missing station elevation data (`NaN`) caused the bot to crash during integer casting. Now uses `pd.notna()` for safe handling, ensuring compatibility with NumPy 2.x and Python 3.13.

## [5.21.3] — 2026-05-10

### Fixed
- **Top Stats Default & Graphic.** Corrected the IEM Autoplot 109 URL parameters to ensure `/topstats` correctly defaults to Tornado Warnings by state. Previous shorthand parameters caused a fallback to Severe Thunderstorm counts.
- **Top Stats Phenomenon Option.** Added a new `phenomenon` parameter to the `/topstats` command, allowing users to explicitly toggle between Tornado and Severe Thunderstorm statistics for both Warnings (VTEC) and Reports (LSR).

## [5.21.1] — 2026-05-09

### Fixed
- **Analytics Command Encoding.** Fixed `400 Bad Request` errors in `/tornadoheatmap` and `/topstats` by properly encoding URL parameters (especially spaces in dates) using `urllib.parse.quote`.
- **VADFile Mapping Implementation.** Implemented the `Mapping` protocol for the `VADFile` class in `lib/vad_plotter/vad_reader.py`. This resolves a `'VADFile' object is not a mapping` error when offloading hodograph plotting to the background process pool.

### Added
- **VAD Operational Logging.** Added detailed logging in `lib/vad_plotter/vad.py` for VAD valid time, lowest data altitude, and computed Bunkers storm motion to assist in real-time verification and alignment debugging.

## [5.21.0] — 2026-05-09

### Added
- **Rust DTM & Critical Angle.** Ported `compute_dtm` (Deviant Tornado Motion) and `compute_crit_angl` (Rasmussen 2003 critical angle) to Rust with Python fallback, matching the reference implementations exactly. Called 60–200×/hour per sounding render cycle.
- **Persistent Watch Centroid Cache.** Watch area centroids (up to 10 NWS zone geometry HTTP calls per watch) are now persisted to SQLite with a 12-hour TTL. Survives restarts during active weather, eliminating up to 80 redundant upstream requests per restart with 8 active watches.
- **DB Write Health in /status.** `/status` now shows a `DB Write Failures` field when consecutive SQLite write failures exceed zero, surfacing full-disk or WAL corruption that was previously invisible.

### Fixed
- **Async Event Loop Blocking.** Replaced synchronous `open()`+`read()` calls in the watch upgrade polling loop with `asyncio.to_thread`, preventing the Discord event loop from stalling during image placeholder checks.
- **Cache Scan Blocking.** Moved `os.scandir()` in `cache_utils.py` off the event loop via `asyncio.to_thread`, eliminating 50–500ms stalls during hourly cache cleanup.
- **Rust Fallback Visibility.** Silent `except Exception: pass` in `compute_srh` and `compute_bunkers` now logs at DEBUG level before falling back to Python, making Rust/Python integration regressions visible in logs.
- **HTTP Status Type.** `http_get_bytes_conditional` now returns `0` (not `None`) on network errors, enabling safe `status == 200` comparisons at all call sites. Return type tightened from `Optional[int]` to `int`.
- **Env Var Validation.** Optional channel ID env vars now use `_optional_int()` which validates the value if set and names the variable in the error message, rather than raising an unattributed `ValueError`.

### Performance
- **Numpy Array Conversion.** Pre-convert wind/altitude arrays once in `compute_parameters()` instead of repeating the `isinstance/tolist` check inside each Rust wrapper call (5 redundant copies eliminated per sounding render).

### Refactored
- **warnings.py `_tick()` Decomposed.** Extracted `_parse_alert_response()` and `_handle_disappeared_warnings()` from the 180-line tick function, making each concern independently testable.
- **watches.py Split.** Extracted fetch logic to `cogs/watch_fetch.py` and embed builders to `cogs/watch_format.py`, reducing `watches.py` from 1,060 to 588 lines.
- **Poll Interval Constants.** Named `_WATCH_FAST_POLL_INTERVAL_SEC` and `_WATCH_SLOW_POLL_INTERVAL_SEC` replace bare `asyncio.sleep()` literals in the watch upgrade loop.

### Tests
- **13 new tests.** `test_nwws_connection.py`: 7 tests covering all branches of the NWWS reconnect state machine. `test_sounding_centroid.py`: 6 tests covering the three-tier centroid lookup (memory → DB cache → NWS API).

## [5.20.1] — 2026-05-06

### Fixed
- **Tornado Warning Graphics.** Corrected IEM Autoplot 208 URL parameter names (`phenomenav`/`significancev` vs `phenomena`/`significance`), added `network:WFO::` prefix, zero-padded 4-digit ETN format, and included valid timestamp for accurate product filtering. Tornado and severe weather warnings now display correct graphics.

## [5.20.0] — 2026-05-06

### Added
- **Rust Expansion Phases 1–6.** Implemented SRH/Bunkers calculator, VTEC parser, image cache batch validator, product ID normalizer, haversine distance calculator, and comprehensive Rust unit tests (803 LOC total).
- **Dark/Light Mode Toggle.** Added UI button to sounding selection embed for theme switching while preserving station/ACARS data.

### Fixed
- **IEM Availability Check Performance.** Phase 0 optimization reduces `/sounding` "Checking Station Availability" step from 10–30s to 1–3s by using asyncio.Semaphore(5) concurrency limiting instead of unlimited concurrent requests (222 → 6 HTTP calls).
- **Tornado Graphics Year.** Added defensive year validation (±10 years from current) to prevent fetching wrong-year images for tornado warnings.
- **Tornado Warning Labels.** Fixed warning updates incorrectly showing "Severe Weather Statement" instead of "Tornado Warning" by using VTEC phenomenon/significance to override NWS event labels.

### Performance
- **VAD Hodograph Calculations.** Moved vec2comp, comp2vec, compute_bunkers, compute_srh, compute_critical_angle to Rust with Python fallback.
- **Batch Image Processing.** Consolidated per-image operations (XXH3 hashing, placeholder detection) into single Rust call.
- **Geospatial Queries.** Implemented haversine distance calculations in Rust for station lookups.

## [5.19.0] — 2026-05-06

### Added
- **Hybrid Python/Rust Core.** Introduced a native Rust extension layer using PyO3 and Maturin to offload CPU-bound bottlenecks.
- **The Spatial Engine.** Replaced brute-force distance calculations with a persistent R-Tree spatial index for radar lookups and batch point-in-polygon joins.
- **Fast VWP Parser.** Implemented a robust 82-byte stride binary parser in Rust, speeding up radar ingestion by over 10x while maintaining a safe Python fallback.
- **XXH3 Content Hashing.** Accelerated image change detection with the XXH3 algorithm, reducing event loop CPU pressure by 87% (7.9x faster).
- **Rust Visibility.** Integrated Rust into the project's CI/CD pipeline and GitHub language statistics.
- **Improved /status Visibility.** The `/status` command now displays the real-time status of the Rust Hybrid Core.

### Fixed
- **ASOS Wind Staleness.** Resolved a bug where recent surface wind observations were rejected, leading to inaccurate SRH and critical angle calculations in hodographs.
- **VTEC Year Correction.** Fixed a major bug where warnings with `000000T0000Z` sentinels defaulted to year 2000.
- **NWWS-OI Recovery.** Fixed critical slixmpp connection logic and restored the primary alerting path.
- **Outlooks Race Condition.** Resolved a `KeyError: 'day1'` in the aggressive monitoring loop.
- **CSU-MLP Timezone Awareness.** Implemented a smart fallback for manual `/csu` commands during the "UTC dead zone" roll-over.

## [5.18.1] — 2026-05-06


### Fixed
- **Hodograph KeyError.** Resolved `KeyError: 'rid'` in `lib/vad_plotter/vad_reader.py` that caused manual hodograph generation to fail when using the worker pool.
- **VAD Pickling.** Ensured `VADFile` objects are picklable by deleting the transient `BytesIO` buffer after parsing, preventing failures when passing VAD objects to the `ProcessPoolExecutor` in background tasks.
- **Forensic Recorder Regressions.** Refactored `cogs/recorder.py` to use `@staticmethod` workers for all background tasks, avoiding `PicklingError` from nested functions. Fixed a regression where the recorder attempted to call a non-existent `plot_vad` function.
- **VAD Site Labeling.** Updated `download_vad` to ensure the radar site ID is correctly propagated to the plotting engine.

## [5.18.1] — 2026-05-06

### Fixed
- **CSU-MLP Manual Fallback.** Fixed a logic bug where manual `/csu` commands would fail between 00:00 UTC and 17:00 UTC (the "UTC dead zone"). The command now intelligently falls back to the most recent available run from the previous day, while the automated polling loop remains strictly bound to the current operational day to prevent regressions.
- **Sounding Cog Logging.** Completed the removal of bracketed log prefixes (`[SOUNDING-AUTO]`, etc.) in `cogs/sounding.py`, bringing it in line with the hierarchical logging standards introduced in v5.18.0.

### Changed
- **CSU Logging.** Switched `cogs/csu_mlp.py` to the hierarchical `spc_bot.csu_mlp` logger and removed redundant string prefixes.

### Added
- **Internal Documentation.** Added detailed `NOTE` blocks in `cogs/csu_mlp.py` explaining the deliberate distinction between manual (lenient) and automated (strict) fallback behaviors to prevent future maintainer confusion.

## [5.18.0] — 2026-05-06

### Fixed
- **VTEC Year Bug.** Resolved a critical bug where warnings with the `000000T0000Z` null-start sentinel had their year incorrectly calculated as `2000`, causing the bot to fetch ancient IEM Autoplot maps and broken VTEC links. Implemented a fallback to the `end` field timestamp for year extraction in `CON/EXT` products.
- **NWWS-OI Connection Error.** Fixed a `TypeError` in the `slixmpp` connection call by removing the unsupported `address=` keyword argument.
- **Outlooks KeyError.** Fixed a race condition in the partial update logic where `partial_update_state` was accessed before initialization.
- **Sounding Cog AttributeError.** Removed legacy calls to the non-existent `_persist_posted_state()` method.
- **Recorder SRH Logic.** Resolved broken imports from a non-existent `met_engine` module and refactored SRH calculation to use the verified `lib.vad_plotter.params` module.
- **Project-Wide Linting.** Fixed multiple `E701` (multiple statements) and `E741` (ambiguous variable) issues across core and utility modules.

### Changed
- **Hierarchical Named Loggers.** Transitioned all cogs to a hierarchical logger structure (e.g., `spc_bot.warnings`, `spc_bot.recorder`) and removed redundant bracketed string prefixes (`[WARN]`, `[NWWS]`) from log messages for improved readability and filtering.
- **Startup Resilience.** Added a 5-second timeout to the `git describe` version lookup and relaxed NWWS credential requirements to allow limited bot operation without full environment setup.
- **Dependency Pining.** Added upper bounds to development requirements (`pytest`, `ruff`) to prevent future breaking changes.

### Added
- **Recorder Finalization Tests.** Implemented `tests/test_recorder_finalize.py` to provide 100% coverage for the high-I/O mission finalization path.
- **Comprehensive Type Hints.** Added type safety improvements to the `lib/vad_plotter` modules.

### Refactored
- **Main Setup Hook.** Split the 70+ line `setup_hook()` in `main.py` into focused private methods (`_hydrate_state`, `_check_failover`, `_run_startup_cleanup`) for better maintainability.

## [5.17.0] — 2026-05-05

### Added
- **Automated Google Drive Backups.** Integrated `rclone` to provide off-server backups for VAD evolution GIFs. The bot automatically uploads finalized forensics to a configured Google Drive directory, ensuring long-term data preservation without local disk pressure.
- **Configurable Backup Dest.** Added `RCLONE_REMOTE` and `RCLONE_DEST_DIR` to support various cloud providers supported by rclone.

### Fixed
- **Recorder Linting.** Resolved multiple "multiple statements on one line" linting errors in `cogs/recorder.py` to align with project standards.

## [5.16.1] — 2026-05-05

### Added
- **Unwarned Tornado Tracking.** Tornado Local Storm Reports without matching warnings now explicitly marked
  with `lead_time=-1` sentinel value in the events database. Dashboard displays "⚠️ UNWARNED" badge,
  giving operators visibility into tornadoes that occurred without a preceding warning.

### Fixed
- **Uptime Display Always Showing 'Unknown'.** Root cause was duplicate `@bot.event on_ready()` handlers
  in main.py (lines 245 and 569), causing the second handler to overwrite the first. The first handler set
  `bot_start_time` on Discord connection; the second (cache cleanup scheduling) overwrote it. Merged both
  handlers into a single `on_ready()` that performs both initialization and scheduling.
- **Version Sync Errors.** Hardcoded version in `config.py` was prone to human error (evidenced by version
  showing 5.15.3 despite being on code from later commits). Implemented dynamic versioning using
  `git describe --tags` to derive version from git history, with fallback to `VERSION` file for
  non-git deployments. Version now always reflects accurate git state.
- **Bot Start Time Reset on Reconnections.** Previously reset on every Discord `on_ready()` event
  (which fires multiple times). Now only set once at process startup; subsequent Discord reconnections
  preserve the original start time.

### Changed
- **Dynamic Version Reporting.** Bot now reports version derived from git tags at runtime instead of
  hardcoded config value. Development builds show commit count ahead of tag (e.g., `5.16.0-10-g71c8002`).

### Documentation
- README restructured for improved clarity and maintainability.
- Extracted project structure and architecture details into dedicated `PROJECT_STRUCTURE.md`.
- Updated configuration and contribution guidelines.

## [5.16.0] — 2026-05-05

### Added
- **TTL-Based Cache Eviction System.** Implemented automated cleanup of cached files
  with a 7-day default TTL. Scheduled daily task runs at 03:00 UTC to remove aged files,
  protecting disk space from unbounded growth. Configurable TTL via environment or API.

### Changed
- **Size-Based Log Rotation.** Replaced daily rotation with size-based triggers (50 MB per file)
  with 12-file retention (90+ days total history) and gzip -9 aggressive compression.
  Dramatically reduces disk footprint while maintaining operational history.
- **Modular Warning Architecture.** Refactored `cogs/warnings.py` (1,720 lines) into reusable
  components:
  - `lib/vtec_parser.py` (116 lines): VTEC and polygon parsing with zero Discord dependencies
    — enables reuse in non-Discord contexts (scripts, utilities, testing).
  - `cogs/warning_format.py` (530 lines): Styling, narrative extraction, URL generation.
  - `cogs/warning_ui.py` (560 lines): Discord UI views (EnvironmentalView, TornadoPhotoView,
    TornadoDashboardView).
  - `cogs/warnings.py` (360 lines): Polling logic and deduplication only.
  - **Benefit**: Improved testability, code reusability, separation of concerns, and easier
    collaboration on specific subdomains.

### Fixed
- **Bare Exception Handlers.** Replaced bare `except` clauses in `cogs/recorder.py` and
  `lib/vad_plotter/vad_reader.py` with specific exception types (`ValueError`, `IndexError`),
  improving exception chain preservation for debugging and following Python best practices.
- **Async Test Mock Cleanup Warnings.** Fixed 22 spurious runtime warnings from unittest.mock
  by replacing `MagicMock()` with `AsyncMock()` for async task patching in `conftest.py`
  and adding pytest filter directives in `pytest.ini`.

### Tests
- All 328 tests passing with zero spurious warnings (cleaned from 22 in previous version).

## [5.15.3] — 2026-05-04

### Changed
- **Dependency Maintenance.** Consolidated batch update of core meteorological
  and spatial libraries to their latest versions, including `metpy` (1.7.1),
  `scipy` (1.17.1), `pyproj` (3.7.2), `slixmpp` (1.15.0), and `pytz` (2026.2).
- **CI Infrastructure.** Upgraded `docker/setup-qemu-action` to v4 and updated
  `pytest-cov` to 7.1.0.

## [5.15.2] — 2026-05-04

### Fixed
- **Warning Double-Posting Race Condition.** Resolved a critical race where
  identical warning updates arriving from multiple sources (IEMBot, NWWS-OI, and
  NWS API) could bypass deduplication checks and post multiple times.
  Implemented `posted_product_ids` for cross-feed normalization and tightened
  in-flight locking to ensure strictly atomic issuance processing.

### Tests
- **Warning Deduplication Suite.** Added new regression tests in
  `tests/test_warnings.py` covering high-concurrency race conditions and
  multi-source product deduplication.

## [5.15.1] — 2026-05-04

### Changed
- **Status Display.** Show public IP instead of private LAN IP for operational clarity.

## [5.15.0] — 2026-05-04

### Added
- **Discord Gateway Geolocation.** Status dashboard now displays the geographic location and IP address of the Discord gateway the bot is connected to, enabling real-time server affinity monitoring.
- **NWWS Message Throughput Tracking.** Added real-time measurement of inbound NWWS firehose message rates (messages/second), with 5-second rolling windows and 70/30 weighted averaging for smooth updates.
- **HTTP Latency Metric.** Added HTTP request latency to the Connectivity section of `/status` for monitoring external API response times.

### Fixed
- **Archived Message Detection.** Fixed throughput tracking not populating due to incorrect XMPP delay element detection. Now properly distinguishes real-time messages from archived room history by comparing delay timestamps (>10 seconds = archived).
- **NWWS Latency Measurement.** Corrected latency calculation to measure from product issue timestamp to bot reception, not from issue to current time.

## [5.14.1] — 2026-05-03

### Added
- **Global Worker Pool.** Refactored all rendering tasks (Soundings, Hodographs, and VAD Forensics) to use a shared `ProcessPoolExecutor` in `utils/worker_pool.py`. This ensures near-zero impact on the Discord event loop during high-demand events.
- **Automated Forensics Cleanup.** Implemented automated management for VAD recording caches in `cogs/maintenance.py`, including a 1GB budget for archived GIFs to protect server storage.

### Fixed
- **Linting Compliance.** Updated `ruff.toml` to explicitly exclude `docs/` and other non-code asset directories from linting.

## [5.14.0] — 2026-05-03

### Added
- **Automated VAD Forensics Recorder.** Introduced a persistent background recording system for `OBSERVED` tornado warnings. Captures 1h lead-up + 90m evolution GIFs of the vertical wind environment automatically.
- **Environmental Database.** Expanded the significant events archive to store calculated 0-1km SRH and animated evolution links, creating a permanent meteorological record of every observed tornado.
- **AWS S3 VAD Fallback.** Implemented a high-availability secondary data source for NEXRAD Level 3 Product 48 (VWP) via the Unidata-NODD S3 bucket.
- **Interactive Forensics UI.** Added dynamic "View Environmental Evolution" buttons to tornado warnings and a searchable `/archive` command for historical environmental discovery.

### Changed
- **Stateless Polling Architecture.** Major refactor of watch, MD, and warning polling to utilize the persistent `state_store`. Bot can now resume missions and deduplicate alerts seamlessly after restarts or failover.
- **Global Radar Dataset.** Synchronized a master 208-site radar list (NEXRAD + TDWR) with static coordinates bot-wide.
- **Background Rendering Pool.** Offloaded GIF and image generation to a dedicated `ProcessPoolExecutor` to ensure near-zero impact on the primary Discord event loop.

### Fixed
- **TGFTP Reliability.** Integrated a `CircuitBreaker` that automatically fails over to S3 when NWS servers return 403s or timeouts.
- **Multi-Event Linkage.** Improved the recorder logic to group overlapping warnings by radar site, creating unified evolution timelines for outbreak events.
- **VAD Parser Robustness.** Updated the binary VWP parser to be wrapper-agnostic, handling Zlib compression and NWS-internal distribution headers found on S3.

## [5.13.1] — 2026-05-03

### Added
- **System Documentation Wiki.** Built out a comprehensive 12-page GitHub Wiki repository covering core features, system architecture (Failover/Persistence), alerting hierarchy (NWWS-OI), and configuration.

### Fixed
- **Outlook Double-Posting.** Resolved a race condition between `auto_post_spc` and `aggressive_check_spc` that could cause the same Day 3 outlook to be posted twice. Implemented a per-day "in-flight" locking mechanism.
- **Immediate Hash Synchronization.** Updated the image caching pipeline to synchronize hashes in memory immediately after a successful disk write, ensuring consistent state across concurrent monitoring loops.
- **Refined Warning Bolding.** Replaced generic bolding logic with a curated list of high-signal weather keywords (`TORNADO`, `HAIL`, `WIND`, etc.) to prevent erratic bolding of structural words like `NEAR...` or `LOCATED...`.
- **SPS Narrative Extraction.** Improved the "At..." narrative fallback for Special Weather Statements to correctly capture impact text in products lacking standard bullet points.

## [5.13.0] — 2026-05-02

### Added
- **Live Performance Monitoring.** Overhauled the `/status` command with a clean, interactive embed featuring real-time latency metrics:
  - **Network Health.** Added live Direct-to-NWS (XMPP) heartbeats and IEM poll RTT in milliseconds.
  - **Alert Delay.** Tracked "on the wire" time from NWS issuance to bot receipt (with minute-precision disclosure).
  - **Auto-Refresh.** The status dashboard now self-updates every 5 seconds for 5 minutes, eliminating the need for manual refreshes.
- **Bot Task Manager.** Introduced `/taskmgr` (Owner-only), a live-updating "htop-style" dashboard showing background loop statuses and scheduled iteration timers.
- **Virtual Terminal Logs.** Introduced `/logs` (Owner-only), a live-streaming console viewer with ANSI support and 5-second auto-refresh.
- **Startup Protection.** Implemented a "Startup Shield" for latency tracking, ignoring the first 60 seconds of uptime to prevent catch-up skew from poisoning rolling averages.

### Fixed
- **Status Command Crash.** Implemented response chunking in `/status` to prevent crashes when the report exceeds Discord's 2000-character limit.
- **NWWS Routing (CLISPS Fix).** Fixed a logic error where Climate Statements were misidentified as Special Weather Statements. Routing now uses strict `.startswith()` prefix matching.
- **Warning Deduplication.** Implemented thread-safe in-flight tracking to prevent race conditions between NWWS and IEMBot triggers.
- **Stable Product IDs.** NWWS products now use the authoritative `issue` timestamp from metadata, ensuring stable identifiers for retransmitted products.
- **Office Normalization.** Standardized office codes to 4-letter ICAOs bot-wide (e.g., `OUN` → `KOUN`) for reliable key matching.
- **IEM Map Reliability.** Upgraded IEM Autoplot retry logic with exponential backoff (8 attempts over ~60s) to better handle delayed polygon generation.
- **Duplicate Log Noise.** Resolved an issue where duplicate logging handlers were being added to the main bot logger.

## [5.12.6] — 2026-05-02

### Changed
- **Python 3.13 Runtime.** Upgraded Docker base images and CI workflows from Python 3.12 to 3.13 for performance improvements in math-heavy operations (numpy/scipy/Cartopy stack).
- **Docker Build Optimization.** Implemented BuildKit cache mounts for pip to persist compiled wheels between builds, reducing subsequent release build times by 5-10x.

### Fixed
- **Duplicate MCD Posts.** Fixed a race condition in `post_md_now()` where concurrent iembot triggers could post the same Mesoscale Discussion twice. MD is now marked as posted immediately upon entry to prevent concurrent calls from both proceeding.

## [5.12.5] — 2026-05-02

### Added
- **On-Demand Dashboard Mapping.** The `/recenttornadoes` card view now supports on-demand local map rendering. If an event has a linked DAT GUID, the bot will automatically fetch the geometry and render the high-detail OSM map directly in the interactive dashboard.

### Fixed
- **CI Build Stability.** Resolved a critical build failure in v5.12.4 by adding missing `Cartopy` and `scipy` dependencies to the core requirements.
- **Standby Task Suppression.** Fixed a bug where automated sounding tasks were incorrectly attempting to run on Standby nodes.
- **Multi-Arch Wheel Building.** Optimized the Dockerfile to ensure transitive dependencies are captured correctly during cross-architecture compilation (AMD64/ARM64).

## [5.12.4] — 2026-05-02

### Added
- **Dashboard Track Maps.** Integrated the high-detail tornado track maps into the individual event cards in the `/recenttornadoes` dashboard. Operators can now view the geographic survey path directly within the interactive card view.
- **Multi-Arch Release Assets.** Automated the generation of portable Docker tarballs for both AMD64 and ARM64 architectures. These are now available directly in the GitHub Release "Assets" section for offline or registry-free deployments.

## [5.12.3] — 2026-05-02

### Added
- **Primary Local OSM Mapping.** Promoted the high-detail local map renderer to the primary visualization for all tornado tracks. Surveys now default to professional OpenStreetMap terrain tiles with road/terrain context, bypassing IEM Autoplot latency.
- **Enhanced Mapping Visuals.** Added high-resolution (10m) US county boundaries and white-halo path outlines for maximum clarity.

### Changed
- **IEM Fallback.** Retained IEM Autoplot 253 as a secondary fallback source to ensure visual coverage even if raw DAT geometry is delayed.

## [5.12.2] — 2026-05-02

### Added
- **Tornado Dashboard UX Overhaul.** Completely redesigned the `/recenttornadoes` summary view to use a vertical list with grand totals, improving scannability on mobile and desktop.
- **Improved Damage Photo Grid.** Redesigned the photo carousel into an efficient 2x2 grid layout, displaying 4 photos per page using multiple embeds.
- **Geographic DAT Linking.** Implemented a more robust linking engine between bot events and official NOAA DAT tracks using Lat/Lon proximity (Haversine) instead of word-matching.
- **Parallel Photo Ingest.** Optimized the damage survey pipeline to use ArcGIS bulk endpoints and parallel downloads, making photo caching up to 5x faster.
- **30-Day Survey Backfill.** Added a rolling 30-day automated backfill task to `MaintenanceCog` to ensure surveys finalized weeks after a storm are automatically linked to the dashboard.

## [5.12.1] — 2026-05-02

### Added
- **Tornado Dashboard UX Overhaul.** Completely redesigned the `/recenttornadoes` summary view to use a vertical list with grand totals, improving scannability on mobile and desktop.
- **Improved Damage Photo Grid.** Redesigned the photo carousel into an efficient 2x2 grid layout, displaying 4 photos per page using multiple embeds.
- **Geographic DAT Linking.** Implemented a more robust linking engine between bot events and official NOAA DAT tracks using Lat/Lon proximity (Haversine) instead of word-matching.
- **Parallel Photo Ingest.** Optimized the damage survey pipeline to use ArcGIS bulk endpoints and parallel downloads, making photo caching up to 5x faster.
- **30-Day Survey Backfill.** Added a rolling 30-day automated backfill task to `MaintenanceCog` to ensure surveys finalized weeks after a storm are automatically linked to the dashboard.

## [5.12.1] — 2026-05-02

### Added
- **Sounding Source Attribution.** Added the data source (Wyoming, IEM, or GSL) to the Discord sounding post captions. This allows operators to visually confirm which authority was used for each plot.

## [5.12.0] — 2026-05-02

### Added
- **Sounding Redundancy (GSL Fallback).** Implemented a high-authority fallback for RAOB sounding plots using the NOAA Global Systems Laboratory (GSL) service. The bot now has a completely independent data path from IEM, resolving the single-point-of-failure reliance on Iowa State for sounding graphics.
- **Circuit-Aware Sounding Retrieval.** Updated the sounding pipeline to automatically skip the primary IEM attempt and go directly to the GSL fallback if the IEM circuit breaker is open.
- **Local Damage Track Rendering.** The bot can now render tornado damage survey maps locally using Cartopy and direct GeoJSON geometry from the NWS DAT ArcGIS API. This serves as a reliable fallback if the IEM Autoplot 253 service is unavailable.
- **Expanded Test Suite.** Added comprehensive integration tests for `MaintenanceCog` (DB retention), `SCPCog` (daily maps), `RadarCog` (NEXRAD cleanup), and `SoundingCog` (auto-post windows).

### Fixed
- **SPS PIL Consistency.** Improved the radar image retry logic to handle Special Weather Statements (SPS) correctly when map polygons are delayed.

## [5.11.1] — 2026-05-02

### Added
- **Extended Radar Retry Window.** Increased the IEM Autoplot retry count to 6 (~30s window) for new warnings. This ensures that the high-speed NWWS-OI path still includes a radar map even if the product beats the IEM map generator by several seconds.

### Fixed
- **NWWS Firehose Log Pollution.** Resolved an issue where running tests would write mock data into the production `nwws_firehose.log`. The firehose log path is now configurable via `NWWS_FIREHOSE_LOG` and is redirected to a temporary file during test execution.

## [5.11.0] — 2026-05-02

### Added
- **NWWS-OI (XMPP) Authority.** Established a persistent XMPP connection to `nwws-oi.weather.gov` to serve as the bot's highest-authority alerting path. Pushes raw NWS text products via Multi-User Chat (MUC) with near-zero latency, beating IEMBot and NWS API polling.
- **NWWS XML Payload Parsing.** Developed custom stanza parsing for the `<x xmlns='nwws-oi'>` element, extracting 100% accurate metadata (`cccc`, `awipsid`, `ttaaii`) and raw product text directly from the NOAA wire.
- **NWWS Firehose Log.** Implemented a dedicated `nwws_firehose.log` with a 10MB rotating limit. This audit trail captures every incoming XMPP message in full detail without cluttering the main operational logs.

### Fixed
- **NWWS Connection Stability.** Resolved "event loop already running" errors by refactoring the XMPP client to use non-blocking async patterns. Disabled IPv6 and implemented manual `is_connected` tracking via XMPP events for robust failover behavior.
- **Failover Connection Gap.** Implemented `trigger_connection()` in the NWWS cog, hooked into the `FailoverCog` promotion sequence. This ensures near-instantaneous XMPP connectivity the moment a node promotes to Primary.
- **Channel Routing Bug.** Fixed a configuration issue where Special Weather Statements (SPS) were incorrectly routing to the SPC channel when `WARNINGS_CHANNEL_ID` was omitted from `.env`.

### Changed
- **Status Command Enhanced.** The `/status` command now includes a "Connectivity" section showing real-time `CONNECTED`/`STANDBY` states for NWWS-OI and the IEMBot feed.

## [5.10.0] — 2026-05-01

### Added
- Watchdog session probe now checks both `api.weather.gov` and `mesonet.agron.iastate.edu`; a failure only counts when both are unreachable, preventing single-endpoint NWS outages from triggering unnecessary session resets.
- Discord alerts posted to the dev channel at 2/3 probe failures (orange) and on session reset (red), giving operators advance notice before teardown fires.

### Changed
- `http_get_json` now uses Tenacity exponential backoff for retries, consistent with all other HTTP helpers.
- Named timeout constants (`TIMEOUT_FAST`, `TIMEOUT_STANDARD`, `TIMEOUT_SLOW`) and circuit breaker params extracted as module-level constants in `utils/http.py`.

### Fixed
- LSR and PNS timestamp parse failures now log at `DEBUG` instead of silently discarding.
- Already-applied DB migration steps now log at `DEBUG` instead of bare `pass`.

## [5.9.2] — 2026-05-01

### Fixed
- **MD Detail Fallback.** Switched from the broken `nwstext.json` endpoint to the reliable `retrieve.py` service for fetching Mesoscale Discussion text when the SPC site is unreachable. This fixes the recurring `404 Not Found` errors in the MD detailing path.
- **Sounding Availability Errors.** Skip IEM availability checks for 5-digit WMO IDs. These IDs are incompatible with IEM's JSON RAOB service and were triggering massive log volume with `422 Unprocessable Content` warnings.

## [5.9.1] — 2026-05-01

### Fixed
- **MD Cancellation Spam (root cause).** Eliminated two bugs that caused already-cancelled Mesoscale Discussions to be repeatedly re-cancelled on every SPC index outage. The HEAD validator early-return now signals a skip-cycle (`None`) rather than an empty index (`[]`), preventing the cancellation diff from firing against an empty set. The IEM fallback loop no longer adds historical MDs to `active_mds`; only the authoritative SPC index populates that set, with newly posted MDs added to `active_mds` only after a confirmed Discord send.

## [5.9.0] — 2026-05-01

### Changed
- **Sounding API Optimization.** Rewrote `get_available_sounding_times_iem` to use the centralized HTTP session pool. Previously, it spawned 25 separate `aiohttp` sessions per station check, causing massive connection spikes during high-risk sweeps.
- **Outlook Scraper Optimization.** Updated `get_spc_urls` to cache and reuse `ETag` and `Last-Modified` headers. The bot now relies on HTTP 304 Not Modified responses rather than fully re-downloading and parsing the SPC HTML indices every 30 seconds.
- **State Store Simplification.** Removed the active background reconciler task and the complex in-memory "dirty queue" from the shared state store. Nodes now employ a more efficient "Resync-on-Promotion" strategy, pushing pending SQLite writes to Upstash only when transitioning from Standby to Primary.
- **Redundant Task Cleanup.** Removed `check_all_urls_exist_parallel` from the outlook polling loops, as the partial updates system correctly handles 404s natively.

### Fixed
- **Pre-warming Cache Restored.** Fixed a bug where `post_soundings_for_watch` ignored pre-warmed sounding data by forcing a cache bypass (`skip_cache=True`). It now correctly leverages the data pre-fetched by the mesoscale discussion monitor.

## [5.8.1] — 2026-05-01

### Fixed
- **MD Cancellation Reliability.** Refined the Mesoscale Discussion cancellation logic to only process expirations when the authoritative SPC index is successfully reachable. During outages, the bot continues to discover new discussions via the IEM fallback but skips expiration checks, eliminating false cancellation notices caused by synchronization lag between the two sources.
- **Code Simplification.** Removed the complex multi-cycle verification shield and threshold guards in favor of the simpler source-based verification rule.

## [5.8.0] — 2026-05-01

### Added
- **Warning Lifecycle Updates.** The bot now posts real-time updates for existing warnings when their status changes (e.g., `CON` continues, `EXT` extends, `EXA` expands). This includes high-fidelity concise descriptions with automated 'cancels/continues' logic for county-level changes.
- **SVS & FFS Support.** Added full support for Severe Weather Statements (`SVS`) and Flash Flood Statements (`FFS`), ensuring critical mid-warning updates are no longer dropped.

### Fixed
- **MD Mass-Cancellation Shield (Final Fix).** Corrected a logic bug in the shield that caused legitimate mass expirations to be suppressed indefinitely. Mass disappearances are now suppressed for a single cycle only, ensuring data integrity while still blocking index flaps.

## [5.7.9] — 2026-05-01

## [5.7.8] — 2026-04-30

### Changed
- **Help Menu Redesign.** Reorganized and polished the `/help` command to eliminate redundant entries and improve readability. Commands are now logically grouped into higher-density categories (Outlooks, Watches & Tornadoes, Analysis & Analytics, and Models & System).
- **Enhanced Status Reporting.** The `/status` command now includes the bot's version number and a real-time list of open HTTP circuit breakers for improved diagnostic visibility.

### Added
- **Status & Help Tests.** Implemented unit tests in `tests/test_status.py` to verify command outputs and health reporting integrity.

## [5.7.7] — 2026-04-30

### Fixed
- **CI & Linting (Stability).** Resolved multiple linting errors (unused imports, undefined methods) and repaired the unit test suite to align with the v5.7 persistence model. This ensures a clean green pass for all 339 tests in GitHub Actions.

## [5.7.6] — 2026-04-30

### Added
- **Sounding State Persistence.** Migrated `SoundingCog` deduplication state to dedicated persistent SQLite tables (`posted_soundings`, `sounding_handled_watches`). This ensures that soundings are not re-posted across bot restarts or failover events.
- **Improved Failover State.** Added `active_mds` to the state serialization (`to_dict`), ensuring that the standby node has a complete picture of active discussions upon promotion.

### Changed
- **Syncthing Snapshot Optimization.** Gated the periodic `events.db` snapshot task on a dirty flag. Snapshots are now only performed if a write operation (new event, DAT link, or prune) has occurred, significantly reducing disk I/O on idle nodes.

## [5.7.5] — 2026-04-30

### Added
- **Comprehensive Test Coverage.** Implemented extensive unit and integration tests for the core alerting pipelines:
  - `tests/test_reports.py`: Full coverage for LSR/PNS parsing, tornado deduplication, lead-time calculation, and DAT track integration.
  - `tests/test_analytics.py`: Coverage for all six analytics slash commands (/topstats, /verify, /riskmap, etc.) and Autoplot URL construction.
  - `tests/test_warnings.py`: Added coverage for the NWS API `_tick` path, including disappeared warning detection, CON area updates, and initial discovery of active warnings.
  - `tests/test_mesoscale.py`: Added coverage for the IEM fallback parsing logic used during SPC index outages.

## [5.7.4] — 2026-04-30

### Changed
- **IEM Autoplot Image Download Extracted.** Deduplicated the identical 3-attempt retry loops in `warnings.py` (iembot path and NWS API path) into a single `_download_warning_image(url, filename)` helper. `BytesIO` is now a top-level import rather than inlined at each call site.
- **Shared Haversine Utility.** Extracted the haversine formula from `lib/vad_plotter/asos.py` and `cogs/sounding_utils.py` into `utils/geo.py`. Both callers now import from the shared module.

## [5.7.3] — 2026-04-30

### Fixed
- **Deprecated `datetime.utcnow()`.** Replaced with `datetime.now(timezone.utc).replace(tzinfo=None)` in `lib/vad_plotter/vad.py` and `vad_reader.py` to silence Python 3.12 deprecation warnings while preserving the existing naive-datetime semantics in those files.
- **Naive `datetime.now()` in outlook partial-update tracking.** All five call sites in `cogs/outlooks.py` now use `datetime.now(timezone.utc)` for consistency with the rest of the codebase.
- **Day 4–8 URL state not persisted.** `auto_post_spc48` now calls `set_posted_urls("day48", urls)` and updates `last_posted_urls` after a successful post, matching the Day 1–3 behavior.
- **Hardcoded S3 year prefix.** `cogs/radar/__init__.py` S3 connectivity probe now uses `datetime.now(timezone.utc).year` instead of the literal `"2026/"`.
- **IEM MCD fetch cap raised.** IEM nwstext fallback limit increased from 20 to 50 to reduce missed MDs during rapid-issuance outbreaks.
- **Stale `# noqa` comment removed.** `asyncio` import in `warnings.py` no longer carries the obsolete `# noqa: F401  # used by future PRs` annotation.

## [5.7.2] — 2026-04-30

### Fixed
- **S3 File Listing Truncation.** `list_files` in `cogs/radar/s3.py` now paginates using `ContinuationToken` so busy NEXRAD sites with more than 1,000 files per day are fully enumerated rather than silently cut off.
- **Botstalk Startup Flood.** On first run (persisted seqnum = 0), the botstalk poller now fast-forwards to the current tail seqnum without processing any backlogged messages, preventing hundreds of simultaneous `_handle_warning` tasks during an active outbreak.

## [5.7.1] — 2026-04-30

### Fixed
- **Sounding Monitor Race.** `monitor_special_soundings` and `monitor_high_risk_soundings` now claim each `_posted_watch_soundings` key immediately at check time (no intervening `await`), eliminating the TOCTOU window where both 15-minute tasks could see the same key as absent and both post the same sounding.
- **Orphaned Upgrade Tasks.** `WatchesCog` and `MesoscaleCog` now track all `_upgrade_watch_embed` / `_upgrade_md_message` background tasks in a `_pending_tasks` set and cancel them in `cog_unload`. Previously these tasks survived standby demotion and continued editing Discord messages from an inactive node.
- **No-Op Sounding Task Accumulation.** `auto_post_watches` now checks `watch_num not in sounding_cog._handled_watches` before scheduling a `post_soundings_for_watch` task for an already-posted watch. Previously 180+ tasks accumulated over a 6-hour watch period with no effect.

## [5.7.0] — 2026-04-30

### Fixed
- **Failover Override.** `/failover` manual-primary command now correctly stores the hostname when writing the Upstash override key. Previously it stored the role prefix (`P`/`S`) instead of the actual hostname, making the override a no-op.
- **SVR Detection Tags.** Added `windDetection` and `hailDetection` to `NWSAlertParameters` so Pydantic no longer silently strips these fields on parse. Detection-method tags ("RADAR INDICATED", "SPOTTER CONFIRMED") now populate correctly via the NWS API path.
- **IEM MD Fallback (`timedelta`).** Added missing `timedelta` to the `datetime` import in `mesoscale.py`. Without it the IEM fallback during SPC index outages would raise `NameError` at runtime.
- **Tornado Dashboard VTEC URL.** `TornadoDashboardView.build_card_embed()` now delegates URL construction to `_vtec_url()` instead of building the IEM link inline with hardcoded phenomenon/significance values.

## [5.6.6] — 2026-04-30

### Fixed
- **Warning Visibility.** Upgraded the backup poll to allow initial "discovery" posts for warnings that are already active (CON/EXT) if the real-time trigger was missed. This ensures 100% issuance visibility even during connection instability.
- **Cancellation Spam.** Implemented a `_cancelled_warnings` tracking set to prevent repeated cancellation notices when the NWS API index lags or flaps.

## [5.6.5] — 2026-04-30

### Fixed
- **Midnight Mass Cancellations.** Fixed a logic bug where active MDs from the previous day were suddenly filtered out of the IEM fallback at 00:00 UTC (7:00 PM CDT). The bot now uses a rolling 24-hour lookback to ensure continuity across the midnight flip.

## [5.6.4] — 2026-04-30

### Added
- **PNS Full-Text View.** Damage Survey posts now include a "📜 View Full Text" button that sends the complete raw NWS text as an ephemeral message, preventing truncation of long reports while keeping the channel clean.

### Fixed
- **MD Cancellation Spam (Improved).** Refined the IEM fallback logic to strictly only include discussions issued on the current UTC day. This prevents the bot from "discovering" old discussions in the IEM archive and then immediately cancelling them when the SPC index returns.
- **PNS Parsing.** Corrected a variable name mismatch in the damage survey handler that was causing a crash during tornado rating extraction.
- **Outbreak Multi-Survey Handling.** The damage survey header now correctly identifies the highest EF-scale rating across all events in a single product and displays a total tornado count.

## [5.6.3] — 2026-04-30

### Fixed
- **MD Fallback (Reliability).** Replaced the experimental IEM JSON endpoint with the stable text-based `retrieve.py` service for the active MD list fallback. This resolves the `422 Unprocessable Content` errors seen when the SPC website was slow or unreachable.

## [5.6.2] — 2026-04-30

### Fixed
- **Cancellation Spam.** Resolved a logic error in the NWS poll loop that caused active warnings (in CON/EXT state) to be incorrectly identified as "disappeared," triggering duplicate cancellation notices every 30 seconds.

## [5.6.1] — 2026-04-30

### Fixed
- **IEM Autoplot Mappings.** Verified all Autoplot numbers against the official IEM catalog and corrected major mapping errors. `/riskmap` now correctly shows SPC outlook frequencies instead of blizzard data, and `/dayssince` pulls the authoritative streak map.
- **Analytics URL Parameters.** Updated all analytics commands to use verified parameter names (`v1`, `filter`, `sdate`, etc.) required by the IEM API for accurate image generation.

## [5.6.0] — 2026-04-30

### Added
- **Single-Card Tornado Dashboard.** Overhauled the tornado viewer to use a detailed "Single Card" UI. Navigate chronologically through events using ⏮️ First, ⏭️ Last, and ◀ Prev, Next ▶ buttons.
- **Damage Photo Carousel.** Integrated a 📸 Photos button that lazy-fetches and displays a scrollable gallery of official NWS DAT damage photos for each matched tornado.
- **Meteorological Analytics Cog.** A suite of new commands for severe weather data:
    - `/topstats`: Rank states or WFOs by tornado warning or report counts (Autoplots #92, #141).
    - `/dayssince`: Track warning-free "streaks" for any state or WFO (Autoplot #235).
    - `/dailyrecap`: Visual summary maps of all warning polygons for a specific date (Autoplot #203).
    - `/tornadoheatmap`: Density maps of tornado reports over a custom timeframe (Autoplot #108).
    - `/riskmap`: Historical frequency maps of SPC Day 1 outlook categories (Autoplot #232).
    - `/verify`: Detailed storm-based verification metrics (POD, FAR, Lead Time) via the IEM Cow API.
- **Lead Time Tracking.** The bot now calculates and displays the warning-to-report lead time (in minutes) for confirmed tornadoes.

### Changed
- **Retention Policy.** Implemented a rolling 365-day retention window for the historical tornado database in the daily maintenance loop to ensure long-term performance.
- **Improved URL Encoding.** Switched the Tornado Archive link to a more robust query-parameter format for better Discord client compatibility.

## [5.5.7] — 2026-04-30

### Fixed
- **Dashboard Data Completeness.** Increased the internal event retrieval limit from 50 to 1000 to ensure the summary dashboard can display a full month of active tornado data.
- **Tornado Archive URL Encoding.** Fixed a bug where the dashboard's Tornado Archive button used unencoded URL fragments, causing link failures in some Discord clients.
- **Summary Dashboard Scannability.** Improved the summary layout to be more compact, allowing for up to 25 days of data to be displayed within Discord's embed field limits.

### Docs
- Comprehensive updates to `README.md`, `CONTRIBUTING.md`, and `CREDITS.md` to reflect the new Tornado Dashboard, EF rating distinctions, and third-party integrations (IEM, DAT, Tornado Archive).

## [5.5.6] — 2026-04-30

### Added
- **Tornado Dashboard.** Replaced the flat paginated list with `TornadoDashboardView`. It acts as a chronological, 'calendar-style' summary dashboard. EF ratings are distinct using color-coded emojis (🟣 EF5, 🔴 EF4, 🟠 EF3, 🟡 EF2, 🟢 EF1, 🔵 EF0). Includes a global button linking directly to the Tornado Archive Data Explorer.
- **DAT Track Links.** Added `dat_guid` column to `events.db`. The bot now automatically links new official NWS Damage Assessment Toolkit (DAT) tracks to database events and provides a direct hyperlink in the dashboard.
- **Specialized Warning Footers.** Added IDs (EMERG, PDS, EWX) to the footer of warning and cancellation embeds for downstream filtering.

### Changed
- **Significant Weather Filtering.** Refined PNS (Damage Survey) parsing to strictly log only tornado-related events (skipping wind-only surveys). Redundant commands `/significantwx` and `/cleartornadoes` removed.
- **New Command:** `/sigtor` filters the database for high-end (EF2+) or "Significant" tornado events.
- **Report Formatting.** Modernized LSR and PNS formatting with single-line descriptions, relative timestamps, and explicit `(ASOS)` or `(Automated Station)` tags. Peak wind strings like `PK WND` are extracted automatically.
- **Partial Cancellations.** Upgraded the warning tick to detect removed counties in `CON` actions, posting an `updates` formatted message showing `**cancels** X, **continues** Y`.

### Fixed
- **MD Fallback (404s).** Replaced deprecated IEM nwstext API endpoint with `retrieve.py` JSON service to correctly fetch the active MD list when SPC's index is unreachable, preventing false cancellation spam.

## [5.5.3] — 2026-04-30

### Fixed
- **Silent send failure in `auto_post_md`.** `except Exception: pass` around the Discord send / state-write block swallowed all errors without logging; a failed send could cause the same MD to be reposted next cycle. Replaced with `logger.exception(...)`.
- **Double station-availability lookup in `/sounding`.** `filter_stations_with_data` was called twice — once blocking, then again concurrently with the ACARS fetch. The blocking call's result was immediately discarded. Removed it, halving the API round-trip per `/sounding` invocation.
- **Unguarded `JSONDecodeError` in `get_posted_urls`.** Malformed JSON from Upstash raised `JSONDecodeError` uncaught (not a subclass of `_UpstashUnavailable`). Added explicit handler that falls back to SQLite.
- **MD cancellation spam.** SPC index flapping caused the New MDs loop to silently re-add an expired MD to `active_mds` each time it reappeared, posting a fresh "Cancelled" embed every poll cycle. Added `_cancelled_mds` set — once an MD is cancelled it cannot be re-activated by the index in the same session.
- **Warning null damage-threat params.** `tornadoDamageThreat` / `thunderstormDamageThreat` returned as explicit `null` from NWS API caused `TypeError` in `get_warning_style`, dropping the entire warnings tick. Fixed with `or []` fallback.

### Changed
- **Warning cancellations post as a new message** instead of editing the original embed in-place. The original post is left untouched; a separate "EWX cancels Severe Thunderstorm Warning" message appears below it.
- **Warning description format overhauled.** Action verb is now a hyperlink to the IEM VTEC event page. County areas include `[STATE]` abbreviation grouped by state using NWS `geocode.UGC` codes (`Ashley, Chicot [AR] and Washington [MS]`). A relative timestamp `[<t:unix_ts:R>]` on the second line shows "N minutes ago" in Discord. Added `extends time of` verb for EXT VTEC action.

### Tests
- New coverage for `post_md_now` and `post_watch_now` (iembot fast-path): dedup guard, successful post, no-channel early return, send-failure state invariant, sounding dispatch.

## [5.5.2] — 2026-04-30

### Fixed
- **Warning tick crash on null NWS damage-threat params.** NWS API returns explicit `null` for `tornadoDamageThreat` / `thunderstormDamageThreat` when no threat level is set. `dict.get(key, [])` returns `None` (not the default) when the key exists with value `null`, causing `TypeError: argument of type 'NoneType' is not iterable` and dropping the entire warnings poll cycle. Happened 3× on 2026-04-29. Fixed with `or []` fallback.

## [5.5.1] — 2026-04-30

### Fixed
- **Split-brain lease reclaim during Upstash reconnect.** `_primary_cycle` used a blind `SET EX` after reads returned `None` (indistinguishable from "key missing" vs "Upstash error"). When a node's connectivity partially returned, it overwrote a legitimate standby-held lease, causing a ~30 s dual-primary window and duplicate posts. Fix: use `SET NX EX` to reclaim only if the key is genuinely absent; re-read and demote if NX is blocked.
- **Standby pre-acknowledging slash commands.** Both nodes connect with the same Discord token; the standby's `on_app_command_error` was calling `send_message` on `CommandNotFound`, pre-acknowledging the interaction before the primary could `defer()`. Caused `40060` cascades and `Task exception was never retrieved` noise. Fix: drop `CommandNotFound` silently on standby; wrap all error-reply paths in `HTTPException` guard.
- **Stale manual cache serving yesterday's outlook.** `should_use_cache_for_manual` accepted files up to 3 days old. Reduced to 3 hours — safely within the longest SPC inter-update gap.
- **Hodograph command always failed.** `vad_plotter` and its I/O stack (`download_vad`, `find_file_times`, `get_asos_surface_wind`) were blocking the event loop with `requests`/`urlopen`. Converted to async using shared `http_get_bytes`/`http_get_text`. Two runtime bugs in the conversion: missing module-level `asyncio` import (`NameError` on every invocation) and naive-vs-aware datetime comparison (`TypeError` in `find_file_times`).

### Tests
- **CI unblocked** — broken since PR #175 which made `suppress_create_task` global autouse. `asyncio.wait` hung forever waiting on `MagicMock` objects returned by the suppressed `create_task`. Fix: `@pytest.mark.real_create_task` marker opts specific tests out of the suppression.
- **No live network calls in test suite.** Upstash credentials from `.env` were leaking via `config.py`'s `load_dotenv()`. Blocked with `os.environ.setdefault` in conftest before any project import.
- **`TestGenerateHodograph`** updated to patch `vad_plotter` directly; previous tests patched `get_running_loop`/`run_in_executor` which no longer exist after the async conversion.

## [5.5.0] — 2026-04-29

### Added
- **Persistent Dirty Write Queue.** Failed Upstash writes are now stored in SQLite (`dirty_writes` table) instead of an in-memory list, ensuring synchronization consistency across restarts.
- **Failover State Mirroring.** Standby nodes now pull authoritative state from Upstash (`mirror_to_sqlite`) when promoted to Primary, ensuring local SQLite is fresh before taking new writes.

### Changed
- **Refined Circuit Breaker.** The HTTP circuit breaker now ignores `404 Not Found` responses and only trips on connection errors, timeouts, `429`, or `5xx` server errors.
- **Improved Sync Logic.** `resync_to_upstash` is now surgical, only pushing entries in the `dirty_writes` table by default.
- **SoundingCog Lifecycle.** Moved task loop starts to `cog_load` to prevent race conditions during bot startup.

### Fixed
- **Database Snapshot Integrity.** Added WAL checkpoints (`wal_checkpoint(RESTART)`) before DB snapshots to ensure consistency during Syncthing replication.

## [5.4.1] — 2026-04-29

### Fixed
- **`SCPCog` task started in `cog_load` instead of `__init__`.** Moving
  `auto_post_scp.start()` out of `__init__` prevents the loop from firing
  before the bot is fully ready, matching the discord.py lifecycle contract.

### Tests
- **`tests/test_iembot.py`** — 26 new unit tests covering `IEMBotCog` seqnum
  persistence, feed filtering, product dispatch, and `_handle_watch` /
  `_handle_md` paths.
- **`tests/test_mesoscale.py`** — 9 new unit tests covering MD cancellation
  (including the empty-index regression from #171), lag protection, year
  wraparound, standby guard, and Discord send failure rollback.
- **`suppress_create_task` fixture promoted to autouse** in `conftest.py`,
  removing the need to opt-in per test and eliminating the duplicate fixture
  that lived in `test_integration.py`.

## [5.4.0] — 2026-04-29

### Added
- **Global circuit breaker and retry middleware.** All outbound HTTP calls now
  go through a unified retry layer (`tenacity` exponential backoff) and a
  per-host circuit breaker that fails fast when NWS/SPC/IEM APIs are degraded,
  preventing cascading delays from one unreachable upstream from blocking the
  entire poll cycle.
- **Pydantic models for NWS Alerts API.** Strict schema validation at the API
  boundary replaces unsafe `dict.get()` traversal throughout the warnings
  pipeline. Malformed responses now raise immediately rather than propagating
  `None` values deep into embed-building logic.
- **Automated cache and artifact lifecycle manager.** New `cogs/maintenance.py`
  runs a daily background task that prunes map image files and temporary
  download artifacts older than 48 hours, keeping the cache directory bounded.

### Performance
- **VAD/Hodograph plotter migrated to `ProcessPoolExecutor`.** Hodograph
  generation now uses the same pre-warmed worker pool as sounding plots,
  eliminating the ~1.5 s cold-import penalty (sounderpy, matplotlib) on the
  first radar request after a restart.
- **Multi-stage Docker build.** Wheel builder pattern strips build tools from
  the final runtime image — smaller container, reduced attack surface.

### Fixed
- **`send_bot_alert` health channel now falls back to `fetch_channel`.**
  Previously `bot.get_channel()` returned `None` during a Discord reconnect
  (cache not yet populated) and the health alert was silently dropped. Now
  falls back to `await bot.fetch_channel()` before giving up.
- **Bare `except: pass` blocks removed from the reporting pipeline.** Silent
  failures in timestamp parsing and magnitude extraction in `cogs/reports.py`
  now log at DEBUG level so anomalies are visible in logs.
- **Persistent LSR deduplication.** `posted_reports` moved from an in-memory
  set to SQLite + Upstash state — LSR dedup now survives bot restarts.
  Hail/wind `event_id` generation standardized between the iembot fast-path
  and GeoJSON poll path so the poll path correctly triggers `ON CONFLICT UPDATE`
  rather than inserting a duplicate row.
- **Atomic LSR event logging.** `add_significant_event()` is now called before
  `channel.send()` in `_handle_lsr`, closing the window where a crash between
  Discord send and DB write could cause the same tornado to repost.
- **MD cancellation fires on quiet days.** The cancellation detection loop was
  guarded by `if current_mds:` — when the SPC index returned an empty list
  (normal on days with no active MDs), all cancellations were silently skipped.
  MDs now receive cancellation embeds correctly regardless of index state.
- **`_dirty_queue` capped at 5 000 entries.** During extended Upstash outages
  with active severe weather the reconciler queue could grow unboundedly in RAM.
  Oldest entries are dropped with a warning on overflow.
- **CSU-MLP double-reset on late restart fixed.** `_last_reset_date` is now
  pre-set to today on cog startup when the bot restarts after 15 UTC, preventing
  a second reset that would clear products already posted in the 15:00–restart
  window.
- **Failover dual-primary window reduced.** A 2-second sleep after writing the
  Upstash lease in `_promote()` gives the outgoing Primary's next sync cycle
  time to demote before cogs start posting, shrinking the window where both
  nodes are simultaneously active.
- **WAL checkpoint before Syncthing snapshot.** `PRAGMA wal_checkpoint(RESTART)`
  is now issued before `db.backup()` so the snapshot Syncthing replicates to the
  Standby includes all committed writes, not just pages already flushed to the
  main database file.

## [5.3.0] — 2026-04-28

### Added
- **Autoposting Tornado Tracks (Autoplot 253).** The bot now automatically
  monitors Public Information Statements (PNS) for "DAMAGE SURVEY" results.
  When a completed survey is detected, it polls the IEM metadata API to
  resolve the corresponding **Autoplot 253 (Tornado Tracks + Lead Time)**
  graphic and posts it to the warnings channel.
- **Persistent Survey Tracking.** Added `posted_surveys` table to SQLite and
  `spcbot:posted_surveys` set to Upstash Redis to ensure each tornado track
  is only posted once.
- **Improved IEM Image Reliability.** Added a 404-retry mechanism with a
  5-second delay for all warning graphics. This accounts for the lag between
  a product issuance and IEM's map generation, significantly reducing the
  frequency of missing images on the iembot fast-path.

### Fixed
- **Restored IEM Autoplot 208 for VTEC maps.** Corrected a regression where
  warnings were using Autoplot 20 (resulting in irrelevant bar graphs).
  Standard VTEC events now correctly use the single-event map plot.
- **Special Weather Statement (SPS) Mapping.** SPS products now use
  **Autoplot 217**, which is specifically designed to map their unique
  polygon identifiers (PIDs).
- **SPS Narrative Extraction.** Improved regex to capture "At" narrative
  bullets that lack a preceding asterisk (common in SPS products).
- **SPS Anti-Cancellation.** Prevented the bot from incorrectly marking
  SPS products as "Expired" when they drop out of the NWS API active
  alerts feed.
- **IEM Parameter Naming.** Fixed the Autoplot 208 URL construction to use
  `phenomena` / `significance` instead of `phenomenav` / `significancev`.

### Removed
- **Legacy SPS Severity Filter.** The `is_severe_sps` function and its
  associated tests have been removed; all SPS products are now processed.

## [Unreleased]

## [5.3.2] — 2026-04-29

### Added
- **Tornado Database and EF Rating Tracking.** Significant weather events (confirmed tornadoes, hail ≥ 3 in, wind ≥ 80 mph) are now logged to a dedicated `cache/events.db` SQLite file — completely separate from the operational `bot_state.db` and never synced to Upstash Redis. EF ratings are backfilled automatically when NWS damage survey (PNS) products are published.
- **`/recenttornadoes` and `/significantwx` Slash Commands.** Query the event archive with configurable time ranges (1 h – 30 days). `/recenttornadoes` shows confirmed tornadoes; `/significantwx` shows the full significant-weather picture (tornadoes + giant hail + high-end wind).
- **Syncthing cross-node replication for `events.db`.** The Primary snapshots `events.db` into a Syncthing-watched directory every 5 minutes. On failover promotion the Standby restores from the latest snapshot before loading cogs. Folder mode (`sendonly` / `receiveonly`) is flipped automatically via the Syncthing REST API on promotion and demotion. Opt-in via `SYNCTHING_API_KEY` and `SYNCTHING_FOLDER_ID` in `.env`.
- **High-risk-day sounding sweep.** On SPC Day 1 Moderate or High Risk days, every RAOB station and ACARS airport inside the categorical polygon (100 km geodesic buffer) is swept for new soundings and posted as they arrive. New module `utils/spc_outlook.py`; `shapely` and `pyproj` added as runtime dependencies.

### Fixed
- **LSR event-type misclassification.** The iembot-path significance logger (`_check_and_log_report`) used a naive `"TORNADO" in raw_text` check that tagged any LSR mentioning an active tornado watch as a tornado event. Replaced with a parser that uses the already-correct event type from the fixed-width LSR header column.
- **LSR `None None` magnitude.** The GeoJSON poll path stored `f"{mag} {unit}"` for tornadoes where `magf` is null, producing `"None None"`. Tornadoes now always store `"Confirmed"`; hail and wind use formatted inch/mph strings.
- **LSR location quality.** The iembot path now appends the state code from the county/date line that follows each LSR header entry. The GeoJSON poll path, when finding a duplicate tornado, updates the existing DB entry with the cleaner `"City, ST"` location instead of skipping — self-healing abbreviated entries within one poll cycle (~5 min).
- **Duplicate log entries eliminated.** `logger.propagate = False` prevents root-level handlers added by libraries at runtime from double-emitting every `spc_bot` record. Sounding plot workers also have inherited handlers cleared on startup.
- **Failover pre-emption.** A rebooting Primary no longer stays stuck in Standby when a promoted Standby holds the lease; it correctly pre-empts and reclaims the Primary role.
- **High-risk sounding captions.** MDT-only days now show `MDT-Risk Sounding` / `MDT-Risk ACARS` instead of `High-Risk`.

### Changed
- **MD posts now include the full discussion text.** SPC mesoscale discussions are posted with the complete body text in the embed (paginated with `(N/M)` titles for long discussions). The graphic-backfill path rebuilds the same structure when SPC catches up.
- **`events.db` separated from Upstash budget.** Significant events were previously double-written to Upstash Redis on every insert, eating into the free-tier daily command budget. The archive now lives solely in `cache/events.db` with no Redis involvement.

## [5.2.6] — 2026-04-27

### Changed
- **Dependency floor bumps** (no runtime change — already running these
  versions): `aioboto3` ≥ 15.5.0, `aiosqlite` ≥ 0.22.1, `matplotlib` ≥
  3.10.9, `pytest-asyncio` ≥ 1.3.0, `ruff` ≥ 0.15.12. CI workflow
  updated to `actions/checkout@6` and `actions/upload-artifact@7`.

### Fixed
- **Failover tests no longer host-dependent.** Three tests in
  `test_failover_coverage.py` reproduce the 2026-04-23 incident using
  hardcoded node names (`ubunt-server`, `3cape`). When the test runner's
  hostname matched one of those literals, the bare-hostname fallback in
  `_is_our_node` inverted the assertions. An autouse fixture now pins
  `socket.gethostname` to a sentinel, making the suite hermetic on any
  host. Production logic is unchanged.

## [5.2.5] — 2026-04-27

### Fixed
- **iembot MD fast-path now delivers under SPC index lag.** When iembot
  detects a new MD before the SPC HTML page is published, `post_md_now`
  no longer bails out on the missing graphic. It posts the header (with
  the iembot-cached text summary) immediately and queues the existing
  `_upgrade_md_message` poller to backfill the graphic once SPC catches
  up — matching the behavior already in place for index-lag 403s after
  the URL was resolved. Previously these triggers logged
  `iembot trigger: could not resolve image` and were silently picked up
  1–3 minutes later by the 30-second poll loop.
- **Reduced log spam during SPC outages.** `[MD] SPC index unreachable —
  falling back to IEM for active MD list` now logs once on transition
  into the outage and once on recovery, instead of every 30 seconds for
  the entire outage window.
- **Stop polluting `active_mds` on failed iembot triggers.** `post_md_now`
  now adds the MD to `active_mds` only after a successful Discord send,
  so a failed fast-path post can no longer interact with the cancellation
  logic in `auto_post_md`.

## [5.2.4] — 2026-04-23

### Fixed
- **Improved watch graphic backfilling.** Increased the initial upgrade
  retry window from 5 to 10 minutes and added a secondary slow-poll
  loop (up to 30 minutes total) if the watch graphic is still missing
  after the final probabilities are posted. This handles cases where
  SPC takes longer than 5 minutes to generate watch GIFs during
  intense weather events.

## [5.2.1] — 2026-04-23


### Fixed
- **Persistent watch upgrades.** The `_upgrade_watch_embed` task now
  continues retrying even if it finds a text summary, as long as the
  watch graphic is still missing or a placeholder. This ensures that
  watches posted via the IEMBot fast-path (which often lack images
  initially) are correctly edited later when SPC generates the GIF.
- **Auto-post upgrade safety.** Added the upgrade-edit trigger to the
  `auto_post_watches` loop (previously it was only in the IEMBot path),
  ensuring that any watch detected first via the 2-minute poll still
  benefits from the image-backfill logic.

## [5.2.2] — 2026-04-23

### Added
- **HTTP Recovery Logging.** Successfully completed HTTP requests after
  one or more failures/timeouts are now logged with a `Successfully
  recovered` message. This provides visibility into the bot's ability
  to catch up during intermittent network instability or API outages.

## [5.2.1] — 2026-04-23

### Added
- **Special Sounding Monitor.** Added `monitor_special_soundings` task
  to `SoundingCog` that runs every 15 minutes. It identifies RAOB
  stations near all currently active watches and checks IEM for *any*
  new sounding release (not just 00z/12z). This ensures that 18z, 20z,
  and other intermediate "special" releases requested by WFOs/SPC are
  automatically detected and posted during the lifetime of a watch.

### Fixed
- **State restoration robustness.** Added idempotent `_ensure_restored`
  safety net to `SoundingCog` auto-post paths to handle cases where
  the `cog_load` hook is skipped by the library.
- **Full Upstash synchronization.** Expanded `resync_to_upstash` to
  include `bot_state` and `posted_urls` tables. This ensures that a
  rebooting primary node with a more recent SQLite mirror than Upstash
  (e.g. from an outage) pushes its full state before cogs start
  running.
- **Startup Resync.** Trigger `resync_to_upstash` immediately during
  `startup_lease_check` in `FailoverCog` if the node claims the
  primary role.

## [5.2.0] — 2026-04-23

### Fixed
- **Failover race on primary reboot.** The primary node loaded all cogs
  immediately based on the `IS_PRIMARY` env var, opening a ~30 s window
  before the failover sync loop's first tick during which cogs ran MD /
  watch / outlook scans against stale in-memory state. This caused
  duplicate posts when the primary rebooted while the standby held the
  Upstash lease (2026-04-23 incident: MD #0505 was posted twice within
  13 s). New `startup_lease_check()` synchronously probes
  `spcbot:manual_primary` and the lease key during `setup_hook` and
  yields to standby if another node owns either.
- **`_rehydrate_bot_state()` now refreshes `csu_mlp_posted`** on
  promotion so CSU-MLP panels aren't re-posted after a failover.
- **Sounding dedup scoped by `watch_num`** caused the same RAOB/ACARS
  profile to post once per geographically-overlapping watch (e.g. ACARS
  OMA posted three times for TOR #0134 + SVR #0135 + TOR #0136). Dedup
  keys are now `raob:{sid}:{time}` / `acars:{airport}:{time}` — watch-
  agnostic — and the set is persisted to Upstash under
  `posted_watch_soundings` keyed by UTC date so a restart mid-event
  doesn't replay every already-posted station.
- **Auto ACARS soundings posted to the watches-announcement channel**
  instead of the observed-soundings channel. `post_soundings_for_watch`
  used `target_channel` (SOUNDING_CHANNEL_ID) for RAOB posts but fell
  back to the passed-in `channel` for ACARS. Both now use the sounding
  channel consistently.

### Changed
- Auto-sounding captions now list **all active watches near the
  station**, not just the one that triggered the post. With three
  overlapping watches and a station in the middle, the caption reads
  `Near active watches #0134 (Tornado), #0135 (SVR), #0136 (Tornado)`.
  Radius threshold is 500 km from each watch's centroid. Watch
  centroids are memoized per-process to avoid N re-fetches of NWS zone
  geometry when captioning multiple stations.

## [5.1.6] — 2026-04-22

### Changed
- Auto-posting loops now fetch all RAOB and ACARS sounding data
  concurrently via `asyncio.gather` instead of sequentially. Post keys
  are claimed before the gather to prevent double-posts.
- Sounding plot generation switched from a serialising `asyncio.Lock`
  + thread executor to a `ProcessPoolExecutor` (max 3 workers). Each
  worker has its own matplotlib instance so multiple plots run in
  parallel. Workers are pre-warmed at spawn to amortize the sounderpy
  cold-import cost. Expected reduction: 3-station batch ~60–90 s → ~15–20 s.
- `shutdown_plot_executor()` called in `main.py` graceful shutdown to
  clean up worker processes on SIGTERM/SIGINT.

## [5.1.5] — 2026-04-22

### Fixed
- IEM RAOB profiles now go through per-level QC (direction 0–360°,
  speed 0–300 kt, pressure 1–1100 hPa, Td ≤ T). Levels failing QC are
  dropped before plotting, eliminating the "starburst" hodograph
  artifacts reported in #87 (e.g. KILX 00z 2026-04-18).
- IEM profiles are now sorted by pressure (descending) and deduped on
  near-duplicate pressures (< 0.1 hPa apart). IEM occasionally returns
  multiple wind vectors at the same pressure which produced radial
  spokes in the hodograph.
- `generate_plot` now catches `ValueError: zero-size array to reduction`
  (and `fmin`/`fmax`) at `WARNING` level instead of surfacing a full
  traceback.

### Added
- `sounding_quality_warning()` returns a short human-readable note when
  a profile is plottable but low-quality (sparse winds or shallow
  pressure coverage). RAOB captions in `cogs/sounding.py` and
  `cogs/sounding_views.py` append the warning rather than suppressing
  the plot.
- `tests/test_sounding_qc.py`: 16 tests covering per-level QC, dedup,
  pressure sorting, and the validator/warning split.

## [5.1.2] — 2026-04-22

### Changed
- `logger.error(..., exc_info=True)` and in-`except` `logger.error(...)`
  calls converted to `logger.exception(...)` across cogs and utils so
  tracebacks are captured consistently (ruff G201/TRY400).
- Re-raised `RuntimeError` / `ValueError` in `cogs/radar/downloads.py`,
  `cogs/radar/s3.py`, `cogs/sounding_utils.py` now use `raise ... from e`
  to preserve the original exception cause (ruff B904).
- `zip(...)` calls in `main.py` and `utils/cache.py` pass `strict=True`
  to catch length mismatches instead of silently truncating (ruff B905).
- `config.py` opens `products.json` with `encoding="utf-8"` for
  portability.
- `main.py` hoists the `aiohttp` import to the top of the file
  (ruff E402).

### Removed
- Stray empty `│/` directory at repo root.

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
## [5.0.2] — 2026-04-21

- fix(status): wire /help footer to __version__ instead of stale literal
- perf: reduce redundant HTTP, add missing is_primary guards, tighten heuristics
- chore: remove dead code
- fix(critical): /md slash crash and ncar state-reset bugs
- log(diagnostics): use repr() for watchdog and failover exception logs

## [5.0.1] — 2026-04-20

- test(failover): make 'past grace' setup robust on fresh CI runners
- docs: bring README, CONTRIBUTING, .env.example in line with v5

## [5.0.0] — 2026-04-20

- fix(iembot): remove dangling FailoverCog.get_upstash_seqnum call
- experimental: shared state in Upstash, simplify failover
- chore(deps): bump aiohttp in the python-minor-patch group
- chore(deps): bump docker/login-action from 3 to 4
- chore(deps): bump docker/metadata-action from 5 to 6
- chore(deps): bump docker/build-push-action from 5 to 7
- chore(deps): bump docker/setup-buildx-action from 3 to 4
- chore(deps): bump actions/setup-python from 5 to 6

## [4.13.2] — 2026-04-19

- fix(failover): separate primary liveness from hydration reachability

## [4.13.1] — 2026-04-19

- fix(failover): close three promotion races that nearly triggered a split-brain

## [4.13.0] — 2026-04-19

- refactor: split BotState into HashStore, PostingLog, TimingTracker

## [4.12.3] — 2026-04-19

- test(failover): drop unused imports flagged by new F401 lint rule
- test: expand cogs/failover coverage from 18% to 62%

## [4.12.2] — 2026-04-19

- refactor: drop 67 unused imports, enable F401 in CI lint

## [4.12.1] — 2026-04-19

- ci: add dependabot for pip, github-actions, and docker

## [4.12.0] — 2026-04-19

- test: expand coverage on db, http, backoff, cache conditional GET, and main

## [4.11.16] — 2026-04-19

- test: harden fixture layer — opt-in patches, real BotState bot, isolated DB

## [4.11.15] — 2026-04-19

- ci: overhaul pipeline — match Docker runtime, cache pip, gate publish on tests

## [4.11.14] — 2026-04-19

- fix: fail fast when FAILOVER_TOKEN is unset or is the 'changeme' default

## [4.11.13] — 2026-04-19

- perf: replace HEAD+GET with conditional GET in partial-update path

## [4.11.12] — 2026-04-19

- refactor: consolidate DB write boilerplate, prune product cache on timer

## [4.11.11] — 2026-04-19

- fix: harden main.py lifecycle (primary flag order, shutdown guard, watchdog cancel)

## [4.11.10] — 2026-04-19

- refactor: centralize extension list, require products.json, clean up imports
- fix: correctly handle sudo user permissions in deploy.sh

## [4.11.9] — 2026-04-18

- fix: refactor watchdog for dynamic task discovery and add health channel redirection

## [4.11.8] — 2026-04-18

- Chore: Enable local builds in docker-compose.yml
- Fix: Remove lib/ from .dockerignore to resolve ModuleNotFoundError in Docker
- Fix: Add 'Custom/Other' option to radar downloader and improve UI clarity

## [4.11.7] — 2026-04-18

- Fix: Enable custom time range for multi-site radar downloads

## [4.11.6] — 2026-04-18

- Fix help menu inaccuracies

## [4.11.5] — 2026-04-18

- Feat: Add comprehensive /help slash command
- Fix: Add ACARS data depth check and validation to prevent fmin plotter crashes
- Fix: Prioritize Wyoming RAOB and add data validation to prevent plotter crashes
- Real fix for log silencing: change watch status to DEBUG
- Update documentation: v4.11.3 release notes, Docker support, and project structure
- Add pytest-asyncio to requirements.txt
- Fix permissions permanently (portable deploy) and quiet watch logs
- Add IEM fallback for MD index when SPC is unreachable
- Fix sounding autoposting for iembot-triggered watches
- docs: finalize docker instructions and build methods
- fix: switch to debian-slim to resolve scientific library build issues
- fix: set C_INCLUDE_PATH for netcdf4 build on alpine
- fix: allow binary wheels for all packages to avoid netcdf4 build issues
- fix: set HDF5_DIR for netcdf4 build
- fix: remove syntax error in Dockerfile
- fix: add hdf5 and netcdf dependencies for netcdf4
- fix: remove conflicting lapack package from runtime
- fix: resolve lapack dependency conflict in alpine
- ci: add setup-buildx-action to support cache export
- docs: update readme and docker-compose to use GHCR image
- ci: add docker build and publish workflow
- feat: dockerize bot with alpine linux and docker-compose (#86)
- docs: update README and CONTRIBUTING for GUILD_ID and CSU command; add metpy to requirements
- feat: store iembot_last_seqnum in Upstash/Redis for reliable failover
- Feat: add RSS memory to /status; suppress no-change cache log spam
- Docs: update project structure in README.md
- Refactor: consolidate IEM and NCAR URLs into config.py
- Refactor: optimize session handling and startup performance
- fix: correct CSU type check, watches embed duplication, and sounding race
- merge: resolve conflict with origin/main shutdown attempt
- fix: prevent 90s SIGKILL hang on shutdown by not orphaning discord's _closing_task
- fix: resolve db deadlock, slow shutdown, and duplicate IEM fetch (#80)
- Fix NameError: datetime is not defined in cogs/failover.py
- fix: resolve ImportError from missing migrate_from_json and optimize shutdown speed
- fix: resolve ImportError by removing legacy migration logic and finalize setup_hook hydration
- fix: resolve reposting flood by hydrating state in setup_hook and prioritize Wyoming soundings
- fix: restore sounding priority and finalize state synchronization to prevent reposts
- fix: resolve TypeErrors in download calls and NameErrors in sounding cogs
- fix: resolve test hangs, add resource cleanup, and fix double-post race condition
- fix: track products.json, resolve absolute paths, and restore robust cache logic
- test: update utils tests for refactored persistence
- refactor: automated task management, externalized product logic, and finalized sqlite transition
- fix: make watchdog and iembot respect standby state
- feat: re-add SOUNDING_CHANNEL_ID configuration
- feat: persistent product cache, MD pre-warming, and enhanced observability
- chore: ignore GEMINI.md
- test: make IEM fallback test deterministic by mocking asyncio.wait
- fix: resolve NCAR TypeError and system-wide task InvalidStateError
- fix: false cancellation, preliminary probs, and SPC upgrade edit for iembot watch posts
- fix: add missing post_md_now and post_watch_now methods
- feat: iembot-triggered immediate posting for watches and MDs
- feat: IEM iembot real-time feed for instant watch/MD text pre-caching
- chore: remove dead legacy globals from utils/cache.py
- fix: rewrite fetch_watch_details_iem to use IEM watches JSON API
- feat: watch-triggered soundings and IEM/SPC/Wyoming race fetching
- feat: IEM fallback for watch and MD details when SPC is unreachable
- fix: periodic command sync primary only
- fix: standby skips command sync on startup to prevent overwriting primary's commands with 0 (#64)
- fix: add periodic 24h command tree sync to recover from silent Discord command drops
- fix: correct all DB function names in _persist_hydrated_state
- fix: persist hydrated state to standby SQLite DB so restarts load current data
- fix: downgrade Upstash heartbeat log to DEBUG to reduce log noise
- docs: update README bot structure and CONTRIBUTING failover architecture
- fix: serialize matplotlib plot generation with asyncio lock, defer+followup for RAOB time picker
- fix: use defer+followup for RAOB time picker so station picker remains visible
- fix: IEMTimeSelectionView uses defer+followup to keep time picker visible, auto-post bypasses availability cache
- fix: demotion check before URL write, sounding UI keeps selection embed, station availability cache
- fix: delete Upstash key on graceful shutdown to prevent stale tunnel URL causing false failover
- fix: Wyoming first for 00z/12z plots, IEM for special soundings, cache availability results 15min, longer UI timeout, don't auto-delete after posting
- fix: check for existing primary before writing URL, add _ready flag to prevent premature Upstash writes
- fix: add demotion check — standby steps down when primary URL changes in Upstash
- fix: hydrate from standby on primary restart, 30s poll interval, failure counter for promotion
- fix: cloudflared URL parsing — read stderr, match https:// prefix
- fix: use Upstash POST body format for URL values with slashes
- feat: HTTP failover system with cloudflared tunnel and Upstash coordination
- fix: status cog use bot.state for posted_mds/watches display and all fetch_and_send_weather_images calls
- fix: remove debug logging from _execute_watches; replace shallow integration tests with ones that actually execute code paths
- fix: replace undefined auto_cache/manual_cache with bot.state equivalents in watches cog
- debug: add logging to _execute_watches to trace NWS API and SPC scrape fallback
- fix: update SPC watch index scrape — SPC removed alt attributes from watch links, now matches href only and fetches individual watch page to determine tornado vs SVR type
- chore: update README bot structure to reflect v4.8.4
- fix: guard task.exception() with done() check in after_aggressive_loop
- fix: initialize bot.state at bot creation time, add integration tests for BotState and cog instantiation
- fix: correct check_and_post_day call sites and remove double bot.state reference
- fix: pass state explicitly to standalone functions in outlooks and status cogs
- refactor: encapsulate global state in BotState class attached to bot.state
- chore: pre-push hook skips tag and branch-delete pushes
- fix: suppress chatty Wyoming fallback warnings — downgrade to debug, skip non-standard hours
- feat: ACARS auto-post during active watches, fix sounding log messages, suppress SounderPy plot output
- fix: add K prefix for ACARS airport lat/lon lookup (3-letter codes need KATL not ATL)
- chore: add install-hooks.sh for pre-push syntax and test checks
- fix: apply CombinedSoundingView to /sounding command — ACARS and IEM multi-hour support
- feat: add IEM sounding source (all hours), ACARS aircraft profiles to /sounding
- feat: auto-post soundings near active SPC watches at 00z/12z
- fix: reduce sounding station verification from 10 candidates/2 times to 6/1 for faster response
- fix: remove startup cleanup block that was silently killing on_ready before command sync
- feat: /download count param for N most recent, fix startup cleanup blocking event loop
- feat: add quick-start options to /download — site codes and time preset bypass interactive flow
- fix: set group ownership to spcbot on install dir so radar cleanup works
- fix: set 775 on install dir so spcbot can create/delete radar_data subdirs
- fix: rename s3 download_file to s3_download_file to avoid conflict with downloads.py local function
- fix: create radar_data dir with correct permissions during deploy
- fix: get_radar_sites is now async, remove run_in_executor wrapper in StartView
- refactor: replace boto3 with aioboto3 for native async S3 operations
- fix: load posted_mds and posted_watches from DB on startup; fix triple DB connection
- fix: add missing asyncio import to mesoscale and watches; add cog import smoke tests
- fix: skip JSON migration if DB already populated to prevent overwriting current hashes
- fix: load auto_cache and manual_cache from DB on startup so hashes survive restarts
- fix: persist last_posted_urls to SQLite so Day 1-3 outlooks don't repost on restart
- Update CONTRIBUTING.md for SQLite database changes
- docs: fix alignment of backoff.py and db.py in project structure
- docs: update directory tree with db.py
- Fix formatting of backoff.py entry in README
- refactor: migrate all persistent state from JSON files to SQLite via aiosqlite
- feat: add exponential backoff to auto_post_spc, auto_post_md, auto_post_watches loops
- refactor: consolidate /csu1-8 and panel commands into single /csu with Choice dropdown
- docs: update CONTRIBUTING with sounding, fresh option, persistence; add SounderPy to CREDITS
- fix: log file and matplotlib permissions, suppress SounderPy banner, add logout note to deploy
- fix: complete deploy.sh rewrite — self-copy detection, venv arch check, correct permissions, aliases via /etc/bash.bashrc
- fix: add git safe.directory config for root during deploy
- fix: split ownership so admins can git pull, spcbot only owns cache and .env, add spcupdate alias
- fix: deploy.sh installs to /opt/spc-bot, add shell aliases spcon/spcoff/spcstatus/spclog
- fix: keep partial update state waiting until 20min timeout instead of clearing after 2min
- feat: add fresh option to /scp /spc1 /spc2 /spc3 to bypass cache
- fix: check all sounding times concurrently per station for faster verification
- fix: verify station data availability before showing options, search wider candidate pool
- fix: immediate loading state, auto-fallback to previous sounding times, cleanup ephemeral messages on success
- fix: only show sounding times that are in the past
- feat: add RAOB sounding cog with /sounding slash command, interactive station/time selection, dark mode preference
- fix: widen CSU-MLP poll window to 22 UTC, WxNext2 to 12 UTC, add periodic_cleanup status label
- docs: update README with hodograph feature, new dependencies, and project structure
- fix(hodograph): use sys.executable to run vad.py in active venv
- feat: add hodograph cog with /hodograph slash command
- docs: fix slash command names and add missing /wpc and /downloaderstatus
- docs: add CONTRIBUTING.md with architecture and operator reference
- test: add unit tests for watches VTEC parsing and API failure handling
- fix(watches): distinguish API failure from empty watch list
- chore: remove cig_migration() dead code
- improve: tighten CSU-MLP and WxNext2 poll windows, add friendly task labels to /status
- refactor: rename SCP_CHANNEL_ID to MODELS_CHANNEL_ID for clarity
- docs: add Prerequisites section with Python version and dependency requirements
- docs: add venv creation step to manual setup instructions
- docs: restructure setup section for consistency, remove redundant systemctl commands
- feat: add deploy.sh with systemd service setup and update README
- test: add tests for CSU-MLP URL builders, state persistence, and NCAR WxNext2
- ci: add GitHub Actions workflow to run tests on push and PR
- docs: update README with NCAR WxNext2 feature and project structure
- feat: add NCAR WxNext2 cog with /wxnext slash command and daily auto-post
- fix: use key=str in sorted() to handle mixed int/str posted state
- feat: add CSU-MLP 6-panel slash commands and auto-post, update README
- fix: persist CSU-MLP posted state across reboots using cache JSON
- fix: CSU-MLP days 4-8 use 00z only, use Content-Type check instead of HEAD for URL validation
- feat: add CSU-MLP cog with /csu1-8 slash commands and polling auto-post
- v2.0.0: major refactor
- reduce watchdog interval from 10 to 2 minutes for faster task recovery
- Update README to remove discord.py reference
- Initialize README with project details and features

## [4.11.4] — 2026-04-18

- Fix: Prioritize Wyoming RAOB and add data validation to prevent plotter crashes

## [4.11.3] — 2026-04-18

- Real fix for log silencing: change watch status to DEBUG
- Update documentation: v4.11.3 release notes, Docker support, and project structure

## [4.11.2] — 2026-04-18

- Add pytest-asyncio to requirements.txt

## [4.11.1] — 2026-04-18

- Fix permissions permanently (portable deploy) and quiet watch logs
- Add IEM fallback for MD index when SPC is unreachable
- Fix sounding autoposting for iembot-triggered watches
- docs: finalize docker instructions and build methods
- fix: switch to debian-slim to resolve scientific library build issues
- fix: set C_INCLUDE_PATH for netcdf4 build on alpine
- fix: allow binary wheels for all packages to avoid netcdf4 build issues
- fix: set HDF5_DIR for netcdf4 build
- fix: remove syntax error in Dockerfile
- fix: add hdf5 and netcdf dependencies for netcdf4
- fix: remove conflicting lapack package from runtime
- fix: resolve lapack dependency conflict in alpine
- ci: add setup-buildx-action to support cache export
- docs: update readme and docker-compose to use GHCR image
- ci: add docker build and publish workflow
- feat: dockerize bot with alpine linux and docker-compose (#86)
- docs: update README and CONTRIBUTING for GUILD_ID and CSU command; add metpy to requirements
- feat: store iembot_last_seqnum in Upstash/Redis for reliable failover
- Feat: add RSS memory to /status; suppress no-change cache log spam
- Docs: update project structure in README.md
- Refactor: consolidate IEM and NCAR URLs into config.py
- Refactor: optimize session handling and startup performance
- fix: correct CSU type check, watches embed duplication, and sounding race
- merge: resolve conflict with origin/main shutdown attempt
- fix: prevent 90s SIGKILL hang on shutdown by not orphaning discord's _closing_task
- fix: resolve db deadlock, slow shutdown, and duplicate IEM fetch (#80)
- fix: resolve db deadlock, slow shutdown, and duplicate IEM fetch (#80)
- Fix NameError: datetime is not defined in cogs/failover.py
- fix: resolve ImportError from missing migrate_from_json and optimize shutdown speed
- fix: resolve ImportError by removing legacy migration logic and finalize setup_hook hydration
- fix: resolve reposting flood by hydrating state in setup_hook and prioritize Wyoming soundings
- fix: restore sounding priority and finalize state synchronization to prevent reposts
- fix: resolve TypeErrors in download calls and NameErrors in sounding cogs
- fix: resolve test hangs, add resource cleanup, and fix double-post race condition
- fix: track products.json, resolve absolute paths, and restore robust cache logic
- test: update utils tests for refactored persistence
- refactor: automated task management, externalized product logic, and finalized sqlite transition
- fix: make watchdog and iembot respect standby state
- feat: re-add SOUNDING_CHANNEL_ID configuration
- feat: persistent product cache, MD pre-warming, and enhanced observability
- chore: ignore GEMINI.md
- test: make IEM fallback test deterministic by mocking asyncio.wait
- fix: resolve NCAR TypeError and system-wide task InvalidStateError
- fix: false cancellation, preliminary probs, and SPC upgrade edit for iembot watch posts
- fix: add missing post_md_now and post_watch_now methods
- feat: iembot-triggered immediate posting for watches and MDs
- feat: IEM iembot real-time feed for instant watch/MD text pre-caching
- chore: remove dead legacy globals from utils/cache.py
- fix: rewrite fetch_watch_details_iem to use IEM watches JSON API
- feat: watch-triggered soundings and IEM/SPC/Wyoming race fetching
- feat: IEM fallback for watch and MD details when SPC is unreachable
- fix: periodic command sync primary only
- fix: standby skips command sync on startup to prevent overwriting primary's commands with 0 (#64)
- fix: add periodic 24h command tree sync to recover from silent Discord command drops
- fix: correct all DB function names in _persist_hydrated_state
- fix: persist hydrated state to standby SQLite DB so restarts load current data
- fix: downgrade Upstash heartbeat log to DEBUG to reduce log noise
- docs: update README bot structure and CONTRIBUTING failover architecture
- fix: serialize matplotlib plot generation with asyncio lock, defer+followup for RAOB time picker
- fix: use defer+followup for RAOB time picker so station picker remains visible
- fix: IEMTimeSelectionView uses defer+followup to keep time picker visible, auto-post bypasses availability cache
- fix: demotion check before URL write, sounding UI keeps selection embed, station availability cache
- fix: delete Upstash key on graceful shutdown to prevent stale tunnel URL causing false failover
- fix: Wyoming first for 00z/12z plots, IEM for special soundings, cache availability results 15min, longer UI timeout, don't auto-delete after posting
- fix: check for existing primary before writing URL, add _ready flag to prevent premature Upstash writes
- fix: add demotion check — standby steps down when primary URL changes in Upstash
- fix: hydrate from standby on primary restart, 30s poll interval, failure counter for promotion
- fix: cloudflared URL parsing — read stderr, match https:// prefix
- fix: use Upstash POST body format for URL values with slashes
- feat: HTTP failover system with cloudflared tunnel and Upstash coordination
- fix: status cog use bot.state for posted_mds/watches display and all fetch_and_send_weather_images calls
- fix: remove debug logging from _execute_watches; replace shallow integration tests with ones that actually execute code paths
- fix: replace undefined auto_cache/manual_cache with bot.state equivalents in watches cog
- debug: add logging to _execute_watches to trace NWS API and SPC scrape fallback
- fix: update SPC watch index scrape — SPC removed alt attributes from watch links, now matches href only and fetches individual watch page to determine tornado vs SVR type
- chore: update README bot structure to reflect v4.8.4
- fix: guard task.exception() with done() check in after_aggressive_loop
- fix: initialize bot.state at bot creation time, add integration tests for BotState and cog instantiation
- fix: correct check_and_post_day call sites and remove double bot.state reference
- fix: pass state explicitly to standalone functions in outlooks and status cogs
- refactor: encapsulate global state in BotState class attached to bot.state
- chore: pre-push hook skips tag and branch-delete pushes
- fix: suppress chatty Wyoming fallback warnings — downgrade to debug, skip non-standard hours
- feat: ACARS auto-post during active watches, fix sounding log messages, suppress SounderPy plot output
- fix: add K prefix for ACARS airport lat/lon lookup (3-letter codes need KATL not ATL)
- chore: add install-hooks.sh for pre-push syntax and test checks
- fix: apply CombinedSoundingView to /sounding command — ACARS and IEM multi-hour support
- feat: add IEM sounding source (all hours), ACARS aircraft profiles to /sounding
- feat: auto-post soundings near active SPC watches at 00z/12z
- fix: reduce sounding station verification from 10 candidates/2 times to 6/1 for faster response
- fix: remove startup cleanup block that was silently killing on_ready before command sync
- feat: /download count param for N most recent, fix startup cleanup blocking event loop
- feat: add quick-start options to /download — site codes and time preset bypass interactive flow
- fix: set group ownership to spcbot on install dir so radar cleanup works
- fix: set 775 on install dir so spcbot can create/delete radar_data subdirs
- fix: rename s3 download_file to s3_download_file to avoid conflict with downloads.py local function
- fix: create radar_data dir with correct permissions during deploy
- fix: get_radar_sites is now async, remove run_in_executor wrapper in StartView
- refactor: replace boto3 with aioboto3 for native async S3 operations
- fix: load posted_mds and posted_watches from DB on startup; fix triple DB connection
- fix: add missing asyncio import to mesoscale and watches; add cog import smoke tests
- fix: skip JSON migration if DB already populated to prevent overwriting current hashes
- fix: load auto_cache and manual_cache from DB on startup so hashes survive restarts
- fix: persist last_posted_urls to SQLite so Day 1-3 outlooks don't repost on restart
- Update CONTRIBUTING.md for SQLite database changes
- docs: fix alignment of backoff.py and db.py in project structure
- docs: update directory tree with db.py
- Fix formatting of backoff.py entry in README
- refactor: migrate all persistent state from JSON files to SQLite via aiosqlite
- feat: add exponential backoff to auto_post_spc, auto_post_md, auto_post_watches loops
- refactor: consolidate /csu1-8 and panel commands into single /csu with Choice dropdown
- docs: update CONTRIBUTING with sounding, fresh option, persistence; add SounderPy to CREDITS
- fix: log file and matplotlib permissions, suppress SounderPy banner, add logout note to deploy
- fix: complete deploy.sh rewrite — self-copy detection, venv arch check, correct permissions, aliases via /etc/bash.bashrc
- fix: add git safe.directory config for root during deploy
- fix: split ownership so admins can git pull, spcbot only owns cache and .env, add spcupdate alias
- fix: deploy.sh installs to /opt/spc-bot, add shell aliases spcon/spcoff/spcstatus/spclog
- fix: keep partial update state waiting until 20min timeout instead of clearing after 2min
- feat: add fresh option to /scp /spc1 /spc2 /spc3 to bypass cache
- fix: check all sounding times concurrently per station for faster verification
- fix: verify station data availability before showing options, search wider candidate pool
- fix: immediate loading state, auto-fallback to previous sounding times, cleanup ephemeral messages on success
- fix: only show sounding times that are in the past
- feat: add RAOB sounding cog with /sounding slash command, interactive station/time selection, dark mode preference
- fix: widen CSU-MLP poll window to 22 UTC, WxNext2 to 12 UTC, add periodic_cleanup status label
- docs: update README with hodograph feature, new dependencies, and project structure
- fix(hodograph): use sys.executable to run vad.py in active venv
- feat: add hodograph cog with /hodograph slash command
- docs: fix slash command names and add missing /wpc and /downloaderstatus
- docs: add CONTRIBUTING.md with architecture and operator reference
- test: add unit tests for watches VTEC parsing and API failure handling
- fix(watches): distinguish API failure from empty watch list
- chore: remove cig_migration() dead code
- improve: tighten CSU-MLP and WxNext2 poll windows, add friendly task labels to /status
- refactor: rename SCP_CHANNEL_ID to MODELS_CHANNEL_ID for clarity
- docs: add Prerequisites section with Python version and dependency requirements
- docs: add venv creation step to manual setup instructions
- docs: restructure setup section for consistency, remove redundant systemctl commands
- feat: add deploy.sh with systemd service setup and update README
- test: add tests for CSU-MLP URL builders, state persistence, and NCAR WxNext2
- ci: add GitHub Actions workflow to run tests on push and PR
- docs: update README with NCAR WxNext2 feature and project structure
- feat: add NCAR WxNext2 cog with /wxnext slash command and daily auto-post
- fix: use key=str in sorted() to handle mixed int/str posted state
- feat: add CSU-MLP 6-panel slash commands and auto-post, update README
- fix: persist CSU-MLP posted state across reboots using cache JSON
- fix: CSU-MLP days 4-8 use 00z only, use Content-Type check instead of HEAD for URL validation
- feat: add CSU-MLP cog with /csu1-8 slash commands and polling auto-post
- v2.0.0: major refactor
- reduce watchdog interval from 10 to 2 minutes for faster task recovery
- Update README to remove discord.py reference
- Initialize README with project details and features

## [4.11.0] — 2026-04-17

- docs: finalize docker instructions and build methods
- fix: switch to debian-slim to resolve scientific library build issues
- fix: set C_INCLUDE_PATH for netcdf4 build on alpine
- fix: allow binary wheels for all packages to avoid netcdf4 build issues
- fix: set HDF5_DIR for netcdf4 build
- fix: remove syntax error in Dockerfile
- fix: add hdf5 and netcdf dependencies for netcdf4
- fix: remove conflicting lapack package from runtime
- fix: resolve lapack dependency conflict in alpine
- ci: add setup-buildx-action to support cache export
- docs: update readme and docker-compose to use GHCR image
- ci: add docker build and publish workflow
- feat: dockerize bot with alpine linux and docker-compose (#86)

## [4.10.1] — 2026-04-17

- docs: update README and CONTRIBUTING for GUILD_ID and CSU command; add metpy to requirements

## [4.10.0] — 2026-04-16

- feat: store iembot_last_seqnum in Upstash/Redis for reliable failover

## [4.9.31] — 2026-04-16

- Feat: add RSS memory to /status; suppress no-change cache log spam

## [4.9.30] — 2026-04-14

- Docs: update project structure in README.md
- Refactor: consolidate IEM and NCAR URLs into config.py
- Refactor: optimize session handling and startup performance
- fix: correct CSU type check, watches embed duplication, and sounding race
- merge: resolve conflict with origin/main shutdown attempt
- fix: prevent 90s SIGKILL hang on shutdown by not orphaning discord's _closing_task
- fix: resolve db deadlock, slow shutdown, and duplicate IEM fetch (#80)
- Fix NameError: datetime is not defined in cogs/failover.py
- fix: resolve ImportError from missing migrate_from_json and optimize shutdown speed
- fix: resolve ImportError by removing legacy migration logic and finalize setup_hook hydration
- fix: resolve reposting flood by hydrating state in setup_hook and prioritize Wyoming soundings
- fix: restore sounding priority and finalize state synchronization to prevent reposts
- fix: resolve TypeErrors in download calls and NameErrors in sounding cogs
- fix: resolve test hangs, add resource cleanup, and fix double-post race condition
- fix: track products.json, resolve absolute paths, and restore robust cache logic
- test: update utils tests for refactored persistence
- refactor: automated task management, externalized product logic, and finalized sqlite transition
- fix: make watchdog and iembot respect standby state
- feat: re-add SOUNDING_CHANNEL_ID configuration
- feat: persistent product cache, MD pre-warming, and enhanced observability
- chore: ignore GEMINI.md
- test: make IEM fallback test deterministic by mocking asyncio.wait
- fix: resolve NCAR TypeError and system-wide task InvalidStateError
- fix: false cancellation, preliminary probs, and SPC upgrade edit for iembot watch posts
- fix: add missing post_md_now and post_watch_now methods
- feat: iembot-triggered immediate posting for watches and MDs
- feat: IEM iembot real-time feed for instant watch/MD text pre-caching
- chore: remove dead legacy globals from utils/cache.py
- fix: rewrite fetch_watch_details_iem to use IEM watches JSON API
- feat: watch-triggered soundings and IEM/SPC/Wyoming race fetching
- feat: IEM fallback for watch and MD details when SPC is unreachable
- fix: periodic command sync primary only
- fix: standby skips command sync on startup to prevent overwriting primary's commands with 0 (#64)
- fix: add periodic 24h command tree sync to recover from silent Discord command drops
- fix: correct all DB function names in _persist_hydrated_state
- fix: persist hydrated state to standby SQLite DB so restarts load current data
- fix: downgrade Upstash heartbeat log to DEBUG to reduce log noise
- docs: update README bot structure and CONTRIBUTING failover architecture
- fix: serialize matplotlib plot generation with asyncio lock, defer+followup for RAOB time picker
- fix: use defer+followup for RAOB time picker so station picker remains visible
- fix: IEMTimeSelectionView uses defer+followup to keep time picker visible, auto-post bypasses availability cache
- fix: demotion check before URL write, sounding UI keeps selection embed, station availability cache
- fix: delete Upstash key on graceful shutdown to prevent stale tunnel URL causing false failover
- fix: Wyoming first for 00z/12z plots, IEM for special soundings, cache availability results 15min, longer UI timeout, don't auto-delete after posting
- fix: check for existing primary before writing URL, add _ready flag to prevent premature Upstash writes
- fix: add demotion check — standby steps down when primary URL changes in Upstash
- fix: hydrate from standby on primary restart, 30s poll interval, failure counter for promotion
- fix: cloudflared URL parsing — read stderr, match https:// prefix
- fix: use Upstash POST body format for URL values with slashes
- feat: HTTP failover system with cloudflared tunnel and Upstash coordination
- fix: status cog use bot.state for posted_mds/watches display and all fetch_and_send_weather_images calls
- fix: remove debug logging from _execute_watches; replace shallow integration tests with ones that actually execute code paths
- fix: replace undefined auto_cache/manual_cache with bot.state equivalents in watches cog
- debug: add logging to _execute_watches to trace NWS API and SPC scrape fallback
- fix: update SPC watch index scrape — SPC removed alt attributes from watch links, now matches href only and fetches individual watch page to determine tornado vs SVR type
- chore: update README bot structure to reflect v4.8.4
- fix: guard task.exception() with done() check in after_aggressive_loop
- fix: initialize bot.state at bot creation time, add integration tests for BotState and cog instantiation
- fix: correct check_and_post_day call sites and remove double bot.state reference
- fix: pass state explicitly to standalone functions in outlooks and status cogs
- refactor: encapsulate global state in BotState class attached to bot.state
- chore: pre-push hook skips tag and branch-delete pushes
- fix: suppress chatty Wyoming fallback warnings — downgrade to debug, skip non-standard hours
- feat: ACARS auto-post during active watches, fix sounding log messages, suppress SounderPy plot output
- fix: add K prefix for ACARS airport lat/lon lookup (3-letter codes need KATL not ATL)
- chore: add install-hooks.sh for pre-push syntax and test checks
- fix: apply CombinedSoundingView to /sounding command — ACARS and IEM multi-hour support
- feat: add IEM sounding source (all hours), ACARS aircraft profiles to /sounding
- feat: auto-post soundings near active SPC watches at 00z/12z
- fix: reduce sounding station verification from 10 candidates/2 times to 6/1 for faster response
- fix: remove startup cleanup block that was silently killing on_ready before command sync
- feat: /download count param for N most recent, fix startup cleanup blocking event loop
- feat: add quick-start options to /download — site codes and time preset bypass interactive flow
- fix: set group ownership to spcbot on install dir so radar cleanup works
- fix: set 775 on install dir so spcbot can create/delete radar_data subdirs
- fix: rename s3 download_file to s3_download_file to avoid conflict with downloads.py local function
- fix: create radar_data dir with correct permissions during deploy
- fix: get_radar_sites is now async, remove run_in_executor wrapper in StartView
- refactor: replace boto3 with aioboto3 for native async S3 operations
- fix: load posted_mds and posted_watches from DB on startup; fix triple DB connection
- fix: add missing asyncio import to mesoscale and watches; add cog import smoke tests
- fix: skip JSON migration if DB already populated to prevent overwriting current hashes
- fix: load auto_cache and manual_cache from DB on startup so hashes survive restarts
- fix: persist last_posted_urls to SQLite so Day 1-3 outlooks don't repost on restart
- Update CONTRIBUTING.md for SQLite database changes
- docs: fix alignment of backoff.py and db.py in project structure
- docs: update directory tree with db.py
- Fix formatting of backoff.py entry in README
- refactor: migrate all persistent state from JSON files to SQLite via aiosqlite
- feat: add exponential backoff to auto_post_spc, auto_post_md, auto_post_watches loops
- refactor: consolidate /csu1-8 and panel commands into single /csu with Choice dropdown
- docs: update CONTRIBUTING with sounding, fresh option, persistence; add SounderPy to CREDITS
- fix: log file and matplotlib permissions, suppress SounderPy banner, add logout note to deploy
- fix: complete deploy.sh rewrite — self-copy detection, venv arch check, correct permissions, aliases via /etc/bash.bashrc
- fix: add git safe.directory config for root during deploy
- fix: split ownership so admins can git pull, spcbot only owns cache and .env, add spcupdate alias
- fix: deploy.sh installs to /opt/spc-bot, add shell aliases spcon/spcoff/spcstatus/spclog
- fix: keep partial update state waiting until 20min timeout instead of clearing after 2min
- feat: add fresh option to /scp /spc1 /spc2 /spc3 to bypass cache
- fix: check all sounding times concurrently per station for faster verification
- fix: verify station data availability before showing options, search wider candidate pool
- fix: immediate loading state, auto-fallback to previous sounding times, cleanup ephemeral messages on success
- fix: only show sounding times that are in the past
- feat: add RAOB sounding cog with /sounding slash command, interactive station/time selection, dark mode preference
- fix: widen CSU-MLP poll window to 22 UTC, WxNext2 to 12 UTC, add periodic_cleanup status label
- docs: update README with hodograph feature, new dependencies, and project structure
- fix(hodograph): use sys.executable to run vad.py in active venv
- feat: add hodograph cog with /hodograph slash command
- docs: fix slash command names and add missing /wpc and /downloaderstatus
- docs: add CONTRIBUTING.md with architecture and operator reference
- test: add unit tests for watches VTEC parsing and API failure handling
- fix(watches): distinguish API failure from empty watch list
- chore: remove cig_migration() dead code
- improve: tighten CSU-MLP and WxNext2 poll windows, add friendly task labels to /status
- refactor: rename SCP_CHANNEL_ID to MODELS_CHANNEL_ID for clarity
- docs: add Prerequisites section with Python version and dependency requirements
- docs: add venv creation step to manual setup instructions
- docs: restructure setup section for consistency, remove redundant systemctl commands
- feat: add deploy.sh with systemd service setup and update README
- test: add tests for CSU-MLP URL builders, state persistence, and NCAR WxNext2
- ci: add GitHub Actions workflow to run tests on push and PR
- docs: update README with NCAR WxNext2 feature and project structure
- feat: add NCAR WxNext2 cog with /wxnext slash command and daily auto-post
- fix: use key=str in sorted() to handle mixed int/str posted state
- feat: add CSU-MLP 6-panel slash commands and auto-post, update README
- fix: persist CSU-MLP posted state across reboots using cache JSON
- fix: CSU-MLP days 4-8 use 00z only, use Content-Type check instead of HEAD for URL validation
- feat: add CSU-MLP cog with /csu1-8 slash commands and polling auto-post
- v2.0.0: major refactor
- reduce watchdog interval from 10 to 2 minutes for faster task recovery
- Update README to remove discord.py reference
- Initialize README with project details and features

## [4.9.29] — 2026-04-14

- merge: resolve conflict with origin/main shutdown attempt
- fix: prevent 90s SIGKILL hang on shutdown by not orphaning discord's _closing_task
- fix: resolve db deadlock, slow shutdown, and duplicate IEM fetch (#80)
- Fix NameError: datetime is not defined in cogs/failover.py
- fix: resolve ImportError from missing migrate_from_json and optimize shutdown speed
- fix: resolve ImportError by removing legacy migration logic and finalize setup_hook hydration
- fix: resolve reposting flood by hydrating state in setup_hook and prioritize Wyoming soundings
- fix: restore sounding priority and finalize state synchronization to prevent reposts
- fix: resolve TypeErrors in download calls and NameErrors in sounding cogs
- fix: resolve test hangs, add resource cleanup, and fix double-post race condition
- fix: track products.json, resolve absolute paths, and restore robust cache logic
- test: update utils tests for refactored persistence
- refactor: automated task management, externalized product logic, and finalized sqlite transition
- fix: make watchdog and iembot respect standby state
- feat: re-add SOUNDING_CHANNEL_ID configuration
- feat: persistent product cache, MD pre-warming, and enhanced observability
- chore: ignore GEMINI.md
- test: make IEM fallback test deterministic by mocking asyncio.wait
- fix: resolve NCAR TypeError and system-wide task InvalidStateError
- fix: false cancellation, preliminary probs, and SPC upgrade edit for iembot watch posts
- fix: add missing post_md_now and post_watch_now methods
- feat: iembot-triggered immediate posting for watches and MDs
- feat: IEM iembot real-time feed for instant watch/MD text pre-caching
- chore: remove dead legacy globals from utils/cache.py
- fix: rewrite fetch_watch_details_iem to use IEM watches JSON API
- feat: watch-triggered soundings and IEM/SPC/Wyoming race fetching
- feat: IEM fallback for watch and MD details when SPC is unreachable
- fix: periodic command sync primary only
- fix: standby skips command sync on startup to prevent overwriting primary's commands with 0 (#64)
- fix: add periodic 24h command tree sync to recover from silent Discord command drops
- fix: correct all DB function names in _persist_hydrated_state
- fix: persist hydrated state to standby SQLite DB so restarts load current data
- fix: downgrade Upstash heartbeat log to DEBUG to reduce log noise
- docs: update README bot structure and CONTRIBUTING failover architecture
- fix: serialize matplotlib plot generation with asyncio lock, defer+followup for RAOB time picker
- fix: use defer+followup for RAOB time picker so station picker remains visible
- fix: IEMTimeSelectionView uses defer+followup to keep time picker visible, auto-post bypasses availability cache
- fix: demotion check before URL write, sounding UI keeps selection embed, station availability cache
- fix: delete Upstash key on graceful shutdown to prevent stale tunnel URL causing false failover
- fix: Wyoming first for 00z/12z plots, IEM for special soundings, cache availability results 15min, longer UI timeout, don't auto-delete after posting
- fix: check for existing primary before writing URL, add _ready flag to prevent premature Upstash writes
- fix: add demotion check — standby steps down when primary URL changes in Upstash
- fix: hydrate from standby on primary restart, 30s poll interval, failure counter for promotion
- fix: cloudflared URL parsing — read stderr, match https:// prefix
- fix: use Upstash POST body format for URL values with slashes
- feat: HTTP failover system with cloudflared tunnel and Upstash coordination
- fix: status cog use bot.state for posted_mds/watches display and all fetch_and_send_weather_images calls
- fix: remove debug logging from _execute_watches; replace shallow integration tests with ones that actually execute code paths
- fix: replace undefined auto_cache/manual_cache with bot.state equivalents in watches cog
- debug: add logging to _execute_watches to trace NWS API and SPC scrape fallback
- fix: update SPC watch index scrape — SPC removed alt attributes from watch links, now matches href only and fetches individual watch page to determine tornado vs SVR type
- chore: update README bot structure to reflect v4.8.4
- fix: guard task.exception() with done() check in after_aggressive_loop
- fix: initialize bot.state at bot creation time, add integration tests for BotState and cog instantiation
- fix: correct check_and_post_day call sites and remove double bot.state reference
- fix: pass state explicitly to standalone functions in outlooks and status cogs
- refactor: encapsulate global state in BotState class attached to bot.state
- chore: pre-push hook skips tag and branch-delete pushes
- fix: suppress chatty Wyoming fallback warnings — downgrade to debug, skip non-standard hours
- feat: ACARS auto-post during active watches, fix sounding log messages, suppress SounderPy plot output
- fix: add K prefix for ACARS airport lat/lon lookup (3-letter codes need KATL not ATL)
- chore: add install-hooks.sh for pre-push syntax and test checks
- fix: apply CombinedSoundingView to /sounding command — ACARS and IEM multi-hour support
- feat: add IEM sounding source (all hours), ACARS aircraft profiles to /sounding
- feat: auto-post soundings near active SPC watches at 00z/12z
- fix: reduce sounding station verification from 10 candidates/2 times to 6/1 for faster response
- fix: remove startup cleanup block that was silently killing on_ready before command sync
- feat: /download count param for N most recent, fix startup cleanup blocking event loop
- feat: add quick-start options to /download — site codes and time preset bypass interactive flow
- fix: set group ownership to spcbot on install dir so radar cleanup works
- fix: set 775 on install dir so spcbot can create/delete radar_data subdirs
- fix: rename s3 download_file to s3_download_file to avoid conflict with downloads.py local function
- fix: create radar_data dir with correct permissions during deploy
- fix: get_radar_sites is now async, remove run_in_executor wrapper in StartView
- refactor: replace boto3 with aioboto3 for native async S3 operations
- fix: load posted_mds and posted_watches from DB on startup; fix triple DB connection
- fix: add missing asyncio import to mesoscale and watches; add cog import smoke tests
- fix: skip JSON migration if DB already populated to prevent overwriting current hashes
- fix: load auto_cache and manual_cache from DB on startup so hashes survive restarts
- fix: persist last_posted_urls to SQLite so Day 1-3 outlooks don't repost on restart
- Update CONTRIBUTING.md for SQLite database changes
- docs: fix alignment of backoff.py and db.py in project structure
- docs: update directory tree with db.py
- Fix formatting of backoff.py entry in README
- refactor: migrate all persistent state from JSON files to SQLite via aiosqlite
- feat: add exponential backoff to auto_post_spc, auto_post_md, auto_post_watches loops
- refactor: consolidate /csu1-8 and panel commands into single /csu with Choice dropdown
- docs: update CONTRIBUTING with sounding, fresh option, persistence; add SounderPy to CREDITS
- fix: log file and matplotlib permissions, suppress SounderPy banner, add logout note to deploy
- fix: complete deploy.sh rewrite — self-copy detection, venv arch check, correct permissions, aliases via /etc/bash.bashrc
- fix: add git safe.directory config for root during deploy
- fix: split ownership so admins can git pull, spcbot only owns cache and .env, add spcupdate alias
- fix: deploy.sh installs to /opt/spc-bot, add shell aliases spcon/spcoff/spcstatus/spclog
- fix: keep partial update state waiting until 20min timeout instead of clearing after 2min
- feat: add fresh option to /scp /spc1 /spc2 /spc3 to bypass cache
- fix: check all sounding times concurrently per station for faster verification
- fix: verify station data availability before showing options, search wider candidate pool
- fix: immediate loading state, auto-fallback to previous sounding times, cleanup ephemeral messages on success
- fix: only show sounding times that are in the past
- feat: add RAOB sounding cog with /sounding slash command, interactive station/time selection, dark mode preference
- fix: widen CSU-MLP poll window to 22 UTC, WxNext2 to 12 UTC, add periodic_cleanup status label
- docs: update README with hodograph feature, new dependencies, and project structure
- fix(hodograph): use sys.executable to run vad.py in active venv
- feat: add hodograph cog with /hodograph slash command
- docs: fix slash command names and add missing /wpc and /downloaderstatus
- docs: add CONTRIBUTING.md with architecture and operator reference
- test: add unit tests for watches VTEC parsing and API failure handling
- fix(watches): distinguish API failure from empty watch list
- chore: remove cig_migration() dead code
- improve: tighten CSU-MLP and WxNext2 poll windows, add friendly task labels to /status
- refactor: rename SCP_CHANNEL_ID to MODELS_CHANNEL_ID for clarity
- docs: add Prerequisites section with Python version and dependency requirements
- docs: add venv creation step to manual setup instructions
- docs: restructure setup section for consistency, remove redundant systemctl commands
- feat: add deploy.sh with systemd service setup and update README
- test: add tests for CSU-MLP URL builders, state persistence, and NCAR WxNext2
- ci: add GitHub Actions workflow to run tests on push and PR
- docs: update README with NCAR WxNext2 feature and project structure
- feat: add NCAR WxNext2 cog with /wxnext slash command and daily auto-post
- fix: use key=str in sorted() to handle mixed int/str posted state
- feat: add CSU-MLP 6-panel slash commands and auto-post, update README
- fix: persist CSU-MLP posted state across reboots using cache JSON
- fix: CSU-MLP days 4-8 use 00z only, use Content-Type check instead of HEAD for URL validation
- feat: add CSU-MLP cog with /csu1-8 slash commands and polling auto-post
- v2.0.0: major refactor
- reduce watchdog interval from 10 to 2 minutes for faster task recovery
- Update README to remove discord.py reference
- Initialize README with project details and features

## [4.9.28] — 2026-04-14

- fix: resolve db deadlock, slow shutdown, and duplicate IEM fetch (#80)

## [4.9.27-hotfix] — 2026-04-14

- (tag-only / no code changes since v4.9.27)

## [4.9.27] — 2026-04-14

- Fix NameError: datetime is not defined in cogs/failover.py

## [4.9.26] — 2026-04-14

- fix: resolve ImportError from missing migrate_from_json and optimize shutdown speed

## [4.9.25] — 2026-04-14

- fix: resolve ImportError by removing legacy migration logic and finalize setup_hook hydration

## [4.9.24] — 2026-04-14

- fix: resolve reposting flood by hydrating state in setup_hook and prioritize Wyoming soundings

## [4.9.23] — 2026-04-14

- fix: restore sounding priority and finalize state synchronization to prevent reposts

## [4.9.22] — 2026-04-14

- fix: resolve TypeErrors in download calls and NameErrors in sounding cogs

## [4.9.21] — 2026-04-14

- fix: resolve test hangs, add resource cleanup, and fix double-post race condition

## [4.9.20] — 2026-04-14

- fix: track products.json, resolve absolute paths, and restore robust cache logic

## [4.9.19] — 2026-04-14

- test: update utils tests for refactored persistence
- refactor: automated task management, externalized product logic, and finalized sqlite transition

## [4.9.18] — 2026-04-14

- fix: make watchdog and iembot respect standby state

## [4.9.17] — 2026-04-14

- feat: re-add SOUNDING_CHANNEL_ID configuration
- feat: persistent product cache, MD pre-warming, and enhanced observability
- chore: ignore GEMINI.md
- test: make IEM fallback test deterministic by mocking asyncio.wait
- fix: resolve NCAR TypeError and system-wide task InvalidStateError
- fix: false cancellation, preliminary probs, and SPC upgrade edit for iembot watch posts
- fix: add missing post_md_now and post_watch_now methods
- feat: iembot-triggered immediate posting for watches and MDs
- feat: IEM iembot real-time feed for instant watch/MD text pre-caching
- chore: remove dead legacy globals from utils/cache.py
- fix: rewrite fetch_watch_details_iem to use IEM watches JSON API
- feat: watch-triggered soundings and IEM/SPC/Wyoming race fetching
- feat: IEM fallback for watch and MD details when SPC is unreachable
- fix: periodic command sync primary only
- fix: standby skips command sync on startup to prevent overwriting primary's commands with 0 (#64)
- fix: add periodic 24h command tree sync to recover from silent Discord command drops
- fix: correct all DB function names in _persist_hydrated_state
- fix: persist hydrated state to standby SQLite DB so restarts load current data
- fix: downgrade Upstash heartbeat log to DEBUG to reduce log noise
- docs: update README bot structure and CONTRIBUTING failover architecture
- fix: serialize matplotlib plot generation with asyncio lock, defer+followup for RAOB time picker
- fix: use defer+followup for RAOB time picker so station picker remains visible
- fix: IEMTimeSelectionView uses defer+followup to keep time picker visible, auto-post bypasses availability cache
- fix: demotion check before URL write, sounding UI keeps selection embed, station availability cache
- fix: delete Upstash key on graceful shutdown to prevent stale tunnel URL causing false failover
- fix: Wyoming first for 00z/12z plots, IEM for special soundings, cache availability results 15min, longer UI timeout, don't auto-delete after posting
- fix: check for existing primary before writing URL, add _ready flag to prevent premature Upstash writes
- fix: add demotion check — standby steps down when primary URL changes in Upstash
- fix: hydrate from standby on primary restart, 30s poll interval, failure counter for promotion
- fix: cloudflared URL parsing — read stderr, match https:// prefix
- fix: use Upstash POST body format for URL values with slashes
- feat: HTTP failover system with cloudflared tunnel and Upstash coordination
- fix: status cog use bot.state for posted_mds/watches display and all fetch_and_send_weather_images calls
- fix: remove debug logging from _execute_watches; replace shallow integration tests with ones that actually execute code paths
- fix: replace undefined auto_cache/manual_cache with bot.state equivalents in watches cog
- debug: add logging to _execute_watches to trace NWS API and SPC scrape fallback
- fix: update SPC watch index scrape — SPC removed alt attributes from watch links, now matches href only and fetches individual watch page to determine tornado vs SVR type
- chore: update README bot structure to reflect v4.8.4
- fix: guard task.exception() with done() check in after_aggressive_loop
- fix: initialize bot.state at bot creation time, add integration tests for BotState and cog instantiation
- fix: correct check_and_post_day call sites and remove double bot.state reference
- fix: pass state explicitly to standalone functions in outlooks and status cogs
- refactor: encapsulate global state in BotState class attached to bot.state
- chore: pre-push hook skips tag and branch-delete pushes
- fix: suppress chatty Wyoming fallback warnings — downgrade to debug, skip non-standard hours
- feat: ACARS auto-post during active watches, fix sounding log messages, suppress SounderPy plot output
- fix: add K prefix for ACARS airport lat/lon lookup (3-letter codes need KATL not ATL)
- chore: add install-hooks.sh for pre-push syntax and test checks
- fix: apply CombinedSoundingView to /sounding command — ACARS and IEM multi-hour support
- feat: add IEM sounding source (all hours), ACARS aircraft profiles to /sounding
- feat: auto-post soundings near active SPC watches at 00z/12z
- fix: reduce sounding station verification from 10 candidates/2 times to 6/1 for faster response
- fix: remove startup cleanup block that was silently killing on_ready before command sync
- feat: /download count param for N most recent, fix startup cleanup blocking event loop
- feat: add quick-start options to /download — site codes and time preset bypass interactive flow
- fix: set group ownership to spcbot on install dir so radar cleanup works
- fix: set 775 on install dir so spcbot can create/delete radar_data subdirs
- fix: rename s3 download_file to s3_download_file to avoid conflict with downloads.py local function
- fix: create radar_data dir with correct permissions during deploy
- fix: get_radar_sites is now async, remove run_in_executor wrapper in StartView
- refactor: replace boto3 with aioboto3 for native async S3 operations
- fix: load posted_mds and posted_watches from DB on startup; fix triple DB connection
- fix: add missing asyncio import to mesoscale and watches; add cog import smoke tests
- fix: skip JSON migration if DB already populated to prevent overwriting current hashes
- fix: load auto_cache and manual_cache from DB on startup so hashes survive restarts
- fix: persist last_posted_urls to SQLite so Day 1-3 outlooks don't repost on restart
- Update CONTRIBUTING.md for SQLite database changes
- docs: fix alignment of backoff.py and db.py in project structure
- docs: update directory tree with db.py
- Fix formatting of backoff.py entry in README
- refactor: migrate all persistent state from JSON files to SQLite via aiosqlite
- feat: add exponential backoff to auto_post_spc, auto_post_md, auto_post_watches loops
- refactor: consolidate /csu1-8 and panel commands into single /csu with Choice dropdown
- docs: update CONTRIBUTING with sounding, fresh option, persistence; add SounderPy to CREDITS
- fix: log file and matplotlib permissions, suppress SounderPy banner, add logout note to deploy
- fix: complete deploy.sh rewrite — self-copy detection, venv arch check, correct permissions, aliases via /etc/bash.bashrc
- fix: add git safe.directory config for root during deploy
- fix: split ownership so admins can git pull, spcbot only owns cache and .env, add spcupdate alias
- fix: deploy.sh installs to /opt/spc-bot, add shell aliases spcon/spcoff/spcstatus/spclog
- fix: keep partial update state waiting until 20min timeout instead of clearing after 2min
- feat: add fresh option to /scp /spc1 /spc2 /spc3 to bypass cache
- fix: check all sounding times concurrently per station for faster verification
- fix: verify station data availability before showing options, search wider candidate pool
- fix: immediate loading state, auto-fallback to previous sounding times, cleanup ephemeral messages on success
- fix: only show sounding times that are in the past
- feat: add RAOB sounding cog with /sounding slash command, interactive station/time selection, dark mode preference
- fix: widen CSU-MLP poll window to 22 UTC, WxNext2 to 12 UTC, add periodic_cleanup status label
- docs: update README with hodograph feature, new dependencies, and project structure
- fix(hodograph): use sys.executable to run vad.py in active venv
- feat: add hodograph cog with /hodograph slash command
- docs: fix slash command names and add missing /wpc and /downloaderstatus
- docs: add CONTRIBUTING.md with architecture and operator reference
- test: add unit tests for watches VTEC parsing and API failure handling
- fix(watches): distinguish API failure from empty watch list
- chore: remove cig_migration() dead code
- improve: tighten CSU-MLP and WxNext2 poll windows, add friendly task labels to /status
- refactor: rename SCP_CHANNEL_ID to MODELS_CHANNEL_ID for clarity
- docs: add Prerequisites section with Python version and dependency requirements
- docs: add venv creation step to manual setup instructions
- docs: restructure setup section for consistency, remove redundant systemctl commands
- feat: add deploy.sh with systemd service setup and update README
- test: add tests for CSU-MLP URL builders, state persistence, and NCAR WxNext2
- ci: add GitHub Actions workflow to run tests on push and PR
- docs: update README with NCAR WxNext2 feature and project structure
- feat: add NCAR WxNext2 cog with /wxnext slash command and daily auto-post
- fix: use key=str in sorted() to handle mixed int/str posted state
- feat: add CSU-MLP 6-panel slash commands and auto-post, update README
- fix: persist CSU-MLP posted state across reboots using cache JSON
- fix: CSU-MLP days 4-8 use 00z only, use Content-Type check instead of HEAD for URL validation
- feat: add CSU-MLP cog with /csu1-8 slash commands and polling auto-post
- v2.0.0: major refactor
- reduce watchdog interval from 10 to 2 minutes for faster task recovery
- Update README to remove discord.py reference
- Initialize README with project details and features

## [4.9.16] — 2026-04-14

- feat: persistent product cache, MD pre-warming, and enhanced observability
- chore: ignore GEMINI.md

## [4.9.15] — 2026-04-13

- test: make IEM fallback test deterministic by mocking asyncio.wait
- fix: resolve NCAR TypeError and system-wide task InvalidStateError

## [4.9.14] — 2026-04-13

- fix: false cancellation, preliminary probs, and SPC upgrade edit for iembot watch posts

## [4.9.13] — 2026-04-13

- fix: add missing post_md_now and post_watch_now methods

## [4.9.12] — 2026-04-13

- feat: iembot-triggered immediate posting for watches and MDs

## [4.9.11] — 2026-04-13

- feat: IEM iembot real-time feed for instant watch/MD text pre-caching

## [4.9.10] — 2026-04-13

- chore: remove dead legacy globals from utils/cache.py

## [4.9.9] — 2026-04-13

- fix: rewrite fetch_watch_details_iem to use IEM watches JSON API

## [4.9.8] — 2026-04-13

- feat: watch-triggered soundings and IEM/SPC/Wyoming race fetching

## [4.9.7] — 2026-04-13

- feat: IEM fallback for watch and MD details when SPC is unreachable

## [4.9.6] — 2026-04-12

- fix: periodic command sync primary only

## [4.9.5] — 2026-04-12

- fix: standby skips command sync on startup to prevent overwriting primary's commands with 0 (#64)

## [4.9.4] — 2026-04-12

- fix: add periodic 24h command tree sync to recover from silent Discord command drops

## [4.9.3] — 2026-04-12

- fix: correct all DB function names in _persist_hydrated_state

## [4.9.2] — 2026-04-12

- fix: persist hydrated state to standby SQLite DB so restarts load current data

## [4.9.1] — 2026-04-12

- fix: downgrade Upstash heartbeat log to DEBUG to reduce log noise

## [4.9.0] — 2026-04-12

- docs: update README bot structure and CONTRIBUTING failover architecture
- fix: serialize matplotlib plot generation with asyncio lock, defer+followup for RAOB time picker
- fix: use defer+followup for RAOB time picker so station picker remains visible
- fix: IEMTimeSelectionView uses defer+followup to keep time picker visible, auto-post bypasses availability cache
- fix: demotion check before URL write, sounding UI keeps selection embed, station availability cache
- fix: delete Upstash key on graceful shutdown to prevent stale tunnel URL causing false failover
- fix: Wyoming first for 00z/12z plots, IEM for special soundings, cache availability results 15min, longer UI timeout, don't auto-delete after posting
- fix: check for existing primary before writing URL, add _ready flag to prevent premature Upstash writes
- fix: add demotion check — standby steps down when primary URL changes in Upstash
- fix: hydrate from standby on primary restart, 30s poll interval, failure counter for promotion
- fix: cloudflared URL parsing — read stderr, match https:// prefix
- fix: use Upstash POST body format for URL values with slashes
- feat: HTTP failover system with cloudflared tunnel and Upstash coordination
- fix: status cog use bot.state for posted_mds/watches display and all fetch_and_send_weather_images calls

## [4.8.8] — 2026-04-11

- fix: remove debug logging from _execute_watches; replace shallow integration tests with ones that actually execute code paths

## [4.8.7] — 2026-04-11

- fix: replace undefined auto_cache/manual_cache with bot.state equivalents in watches cog

## [4.8.6] — 2026-04-11

- debug: add logging to _execute_watches to trace NWS API and SPC scrape fallback

## [4.8.5] — 2026-04-11

- fix: update SPC watch index scrape — SPC removed alt attributes from watch links, now matches href only and fetches individual watch page to determine tornado vs SVR type
- chore: update README bot structure to reflect v4.8.4

## [4.8.4] — 2026-04-11

- fix: guard task.exception() with done() check in after_aggressive_loop

## [4.8.3] — 2026-04-11

- fix: initialize bot.state at bot creation time, add integration tests for BotState and cog instantiation

## [4.8.2] — 2026-04-11

- fix: correct check_and_post_day call sites and remove double bot.state reference

## [4.8.1] — 2026-04-11

- fix: pass state explicitly to standalone functions in outlooks and status cogs

## [4.8.0] — 2026-04-11

- refactor: encapsulate global state in BotState class attached to bot.state
- chore: pre-push hook skips tag and branch-delete pushes

## [4.7.4] — 2026-04-11

- fix: suppress chatty Wyoming fallback warnings — downgrade to debug, skip non-standard hours

## [4.7.3] — 2026-04-11

- feat: ACARS auto-post during active watches, fix sounding log messages, suppress SounderPy plot output

## [4.7.2] — 2026-04-11

- fix: add K prefix for ACARS airport lat/lon lookup (3-letter codes need KATL not ATL)
- chore: add install-hooks.sh for pre-push syntax and test checks

## [4.7.1] — 2026-04-11

- fix: apply CombinedSoundingView to /sounding command — ACARS and IEM multi-hour support

## [4.7.0] — 2026-04-11

- feat: add IEM sounding source (all hours), ACARS aircraft profiles to /sounding

## [4.6.0] — 2026-04-11

- feat: auto-post soundings near active SPC watches at 00z/12z

## [4.5.3] — 2026-04-11

- fix: reduce sounding station verification from 10 candidates/2 times to 6/1 for faster response

## [4.5.2] — 2026-04-11

- fix: remove startup cleanup block that was silently killing on_ready before command sync

## [4.5.1] — 2026-04-11

- feat: /download count param for N most recent, fix startup cleanup blocking event loop

## [4.5.0] — 2026-04-11

- feat: add quick-start options to /download — site codes and time preset bypass interactive flow

## [4.4.5] — 2026-04-11

- fix: set group ownership to spcbot on install dir so radar cleanup works

## [4.4.4] — 2026-04-11

- fix: set 775 on install dir so spcbot can create/delete radar_data subdirs

## [4.4.3] — 2026-04-11

- fix: rename s3 download_file to s3_download_file to avoid conflict with downloads.py local function

## [4.4.2] — 2026-04-11

- fix: create radar_data dir with correct permissions during deploy

## [4.4.1] — 2026-04-11

- fix: get_radar_sites is now async, remove run_in_executor wrapper in StartView

## [4.4.0] — 2026-04-11

- refactor: replace boto3 with aioboto3 for native async S3 operations

## [4.3.4] — 2026-04-11

- fix: load posted_mds and posted_watches from DB on startup; fix triple DB connection

## [4.3.3] — 2026-04-11

- fix: add missing asyncio import to mesoscale and watches; add cog import smoke tests

## [4.3.2] — 2026-04-11

- fix: skip JSON migration if DB already populated to prevent overwriting current hashes
- fix: load auto_cache and manual_cache from DB on startup so hashes survive restarts
- fix: persist last_posted_urls to SQLite so Day 1-3 outlooks don't repost on restart
- Update CONTRIBUTING.md for SQLite database changes
- docs: fix alignment of backoff.py and db.py in project structure
- docs: update directory tree with db.py
- Fix formatting of backoff.py entry in README

## [4.3.1] — 2026-04-11

- (tag-only / no code changes since v4.3.0)

## [4.3.0] — 2026-04-11

- refactor: migrate all persistent state from JSON files to SQLite via aiosqlite

## [4.2.0] — 2026-04-11

- feat: add exponential backoff to auto_post_spc, auto_post_md, auto_post_watches loops

## [4.1.0] — 2026-04-11

- refactor: consolidate /csu1-8 and panel commands into single /csu with Choice dropdown

## [4.0.10] — 2026-04-10

- docs: update CONTRIBUTING with sounding, fresh option, persistence; add SounderPy to CREDITS

## [4.0.9] — 2026-04-10

- fix: log file and matplotlib permissions, suppress SounderPy banner, add logout note to deploy

## [4.0.8] — 2026-04-10

- fix: complete deploy.sh rewrite — self-copy detection, venv arch check, correct permissions, aliases via /etc/bash.bashrc
- fix: add git safe.directory config for root during deploy
- fix: split ownership so admins can git pull, spcbot only owns cache and .env, add spcupdate alias

## [4.0.7] — 2026-04-10

- fix: deploy.sh installs to /opt/spc-bot, add shell aliases spcon/spcoff/spcstatus/spclog

## [4.0.6] — 2026-04-10

- fix: keep partial update state waiting until 20min timeout instead of clearing after 2min

## [4.0.5] — 2026-04-10

- feat: add fresh option to /scp /spc1 /spc2 /spc3 to bypass cache

## [4.0.4] — 2026-04-10

- fix: check all sounding times concurrently per station for faster verification

## [4.0.3] — 2026-04-10

- fix: verify station data availability before showing options, search wider candidate pool

## [4.0.2] — 2026-04-10

- fix: immediate loading state, auto-fallback to previous sounding times, cleanup ephemeral messages on success

## [4.0.1] — 2026-04-10

- fix: only show sounding times that are in the past

## [4.0.0] — 2026-04-10

- feat: add RAOB sounding cog with /sounding slash command, interactive station/time selection, dark mode preference

## [3.0.1] — 2026-04-10

- fix: widen CSU-MLP poll window to 22 UTC, WxNext2 to 12 UTC, add periodic_cleanup status label
- docs: update README with hodograph feature, new dependencies, and project structure

## [3.0.0] — 2026-04-09

- fix(hodograph): use sys.executable to run vad.py in active venv
- feat: add hodograph cog with /hodograph slash command
- docs: fix slash command names and add missing /wpc and /downloaderstatus

## [2.6.0] — 2026-04-09

- docs: add CONTRIBUTING.md with architecture and operator reference
- test: add unit tests for watches VTEC parsing and API failure handling
- fix(watches): distinguish API failure from empty watch list
- chore: remove cig_migration() dead code

## [2.5.1] — 2026-04-09

- improve: tighten CSU-MLP and WxNext2 poll windows, add friendly task labels to /status

## [2.5.0] — 2026-04-08

- refactor: rename SCP_CHANNEL_ID to MODELS_CHANNEL_ID for clarity

## [2.4.3] — 2026-04-08

- docs: add Prerequisites section with Python version and dependency requirements

## [2.4.2] — 2026-04-08

- docs: add venv creation step to manual setup instructions

## [2.4.1] — 2026-04-08

- docs: restructure setup section for consistency, remove redundant systemctl commands

## [2.4.0] — 2026-04-08

- feat: add deploy.sh with systemd service setup and update README

## [2.3.3] — 2026-04-08

- test: add tests for CSU-MLP URL builders, state persistence, and NCAR WxNext2

## [2.3.2] — 2026-04-08

- ci: add GitHub Actions workflow to run tests on push and PR

## [2.3.1] — 2026-04-08

- docs: update README with NCAR WxNext2 feature and project structure

## [2.3.0] — 2026-04-08

- feat: add NCAR WxNext2 cog with /wxnext slash command and daily auto-post

## [2.2.1] — 2026-04-08

- fix: use key=str in sorted() to handle mixed int/str posted state

## [2.2.0] — 2026-04-08

- feat: add CSU-MLP 6-panel slash commands and auto-post, update README

## [2.1.2] — 2026-04-08

- fix: persist CSU-MLP posted state across reboots using cache JSON

## [2.1.1] — 2026-04-08

- fix: CSU-MLP days 4-8 use 00z only, use Content-Type check instead of HEAD for URL validation

## [2.1.0] — 2026-04-08

- feat: add CSU-MLP cog with /csu1-8 slash commands and polling auto-post

## [2.0.0] — 2026-04-08

- v2.0.0: major refactor
- reduce watchdog interval from 10 to 2 minutes for faster task recovery
- Update README to remove discord.py reference
- Initialize README with project details and features

## [1.4.3-hotfix] — 2026-04-14

- Fix NameError: datetime is not defined in cogs/failover.py
- fix: resolve ImportError from missing migrate_from_json and optimize shutdown speed
- fix: resolve ImportError by removing legacy migration logic and finalize setup_hook hydration
- fix: resolve reposting flood by hydrating state in setup_hook and prioritize Wyoming soundings
- fix: restore sounding priority and finalize state synchronization to prevent reposts
- fix: resolve TypeErrors in download calls and NameErrors in sounding cogs
- fix: resolve test hangs, add resource cleanup, and fix double-post race condition
- fix: track products.json, resolve absolute paths, and restore robust cache logic
- test: update utils tests for refactored persistence
- refactor: automated task management, externalized product logic, and finalized sqlite transition
- fix: make watchdog and iembot respect standby state
- feat: re-add SOUNDING_CHANNEL_ID configuration
- feat: persistent product cache, MD pre-warming, and enhanced observability
- chore: ignore GEMINI.md
- test: make IEM fallback test deterministic by mocking asyncio.wait
- fix: resolve NCAR TypeError and system-wide task InvalidStateError
- fix: false cancellation, preliminary probs, and SPC upgrade edit for iembot watch posts
- fix: add missing post_md_now and post_watch_now methods
- feat: iembot-triggered immediate posting for watches and MDs
- feat: IEM iembot real-time feed for instant watch/MD text pre-caching
- chore: remove dead legacy globals from utils/cache.py
- fix: rewrite fetch_watch_details_iem to use IEM watches JSON API
- feat: watch-triggered soundings and IEM/SPC/Wyoming race fetching
- feat: IEM fallback for watch and MD details when SPC is unreachable
- fix: periodic command sync primary only
- fix: standby skips command sync on startup to prevent overwriting primary's commands with 0 (#64)
- fix: add periodic 24h command tree sync to recover from silent Discord command drops
- fix: correct all DB function names in _persist_hydrated_state
- fix: persist hydrated state to standby SQLite DB so restarts load current data
- fix: downgrade Upstash heartbeat log to DEBUG to reduce log noise
- docs: update README bot structure and CONTRIBUTING failover architecture
- fix: serialize matplotlib plot generation with asyncio lock, defer+followup for RAOB time picker
- fix: use defer+followup for RAOB time picker so station picker remains visible
- fix: IEMTimeSelectionView uses defer+followup to keep time picker visible, auto-post bypasses availability cache
- fix: demotion check before URL write, sounding UI keeps selection embed, station availability cache
- fix: delete Upstash key on graceful shutdown to prevent stale tunnel URL causing false failover
- fix: Wyoming first for 00z/12z plots, IEM for special soundings, cache availability results 15min, longer UI timeout, don't auto-delete after posting
- fix: check for existing primary before writing URL, add _ready flag to prevent premature Upstash writes
- fix: add demotion check — standby steps down when primary URL changes in Upstash
- fix: hydrate from standby on primary restart, 30s poll interval, failure counter for promotion
- fix: cloudflared URL parsing — read stderr, match https:// prefix
- fix: use Upstash POST body format for URL values with slashes
- feat: HTTP failover system with cloudflared tunnel and Upstash coordination
- fix: status cog use bot.state for posted_mds/watches display and all fetch_and_send_weather_images calls
- fix: remove debug logging from _execute_watches; replace shallow integration tests with ones that actually execute code paths
- fix: replace undefined auto_cache/manual_cache with bot.state equivalents in watches cog
- debug: add logging to _execute_watches to trace NWS API and SPC scrape fallback
- fix: update SPC watch index scrape — SPC removed alt attributes from watch links, now matches href only and fetches individual watch page to determine tornado vs SVR type
- chore: update README bot structure to reflect v4.8.4
- fix: guard task.exception() with done() check in after_aggressive_loop
- fix: initialize bot.state at bot creation time, add integration tests for BotState and cog instantiation
- fix: correct check_and_post_day call sites and remove double bot.state reference
- fix: pass state explicitly to standalone functions in outlooks and status cogs
- refactor: encapsulate global state in BotState class attached to bot.state
- chore: pre-push hook skips tag and branch-delete pushes
- fix: suppress chatty Wyoming fallback warnings — downgrade to debug, skip non-standard hours
- feat: ACARS auto-post during active watches, fix sounding log messages, suppress SounderPy plot output
- fix: add K prefix for ACARS airport lat/lon lookup (3-letter codes need KATL not ATL)
- chore: add install-hooks.sh for pre-push syntax and test checks
- fix: apply CombinedSoundingView to /sounding command — ACARS and IEM multi-hour support
- feat: add IEM sounding source (all hours), ACARS aircraft profiles to /sounding
- feat: auto-post soundings near active SPC watches at 00z/12z
- fix: reduce sounding station verification from 10 candidates/2 times to 6/1 for faster response
- fix: remove startup cleanup block that was silently killing on_ready before command sync
- feat: /download count param for N most recent, fix startup cleanup blocking event loop
- feat: add quick-start options to /download — site codes and time preset bypass interactive flow
- fix: set group ownership to spcbot on install dir so radar cleanup works
- fix: set 775 on install dir so spcbot can create/delete radar_data subdirs
- fix: rename s3 download_file to s3_download_file to avoid conflict with downloads.py local function
- fix: create radar_data dir with correct permissions during deploy
- fix: get_radar_sites is now async, remove run_in_executor wrapper in StartView
- refactor: replace boto3 with aioboto3 for native async S3 operations
- fix: load posted_mds and posted_watches from DB on startup; fix triple DB connection
- fix: add missing asyncio import to mesoscale and watches; add cog import smoke tests
- fix: skip JSON migration if DB already populated to prevent overwriting current hashes
- fix: load auto_cache and manual_cache from DB on startup so hashes survive restarts
- fix: persist last_posted_urls to SQLite so Day 1-3 outlooks don't repost on restart
- Update CONTRIBUTING.md for SQLite database changes
- docs: fix alignment of backoff.py and db.py in project structure
- docs: update directory tree with db.py
- Fix formatting of backoff.py entry in README
- refactor: migrate all persistent state from JSON files to SQLite via aiosqlite
- feat: add exponential backoff to auto_post_spc, auto_post_md, auto_post_watches loops
- refactor: consolidate /csu1-8 and panel commands into single /csu with Choice dropdown
- docs: update CONTRIBUTING with sounding, fresh option, persistence; add SounderPy to CREDITS
- fix: log file and matplotlib permissions, suppress SounderPy banner, add logout note to deploy
- fix: complete deploy.sh rewrite — self-copy detection, venv arch check, correct permissions, aliases via /etc/bash.bashrc
- fix: add git safe.directory config for root during deploy
- fix: split ownership so admins can git pull, spcbot only owns cache and .env, add spcupdate alias
- fix: deploy.sh installs to /opt/spc-bot, add shell aliases spcon/spcoff/spcstatus/spclog
- fix: keep partial update state waiting until 20min timeout instead of clearing after 2min
- feat: add fresh option to /scp /spc1 /spc2 /spc3 to bypass cache
- fix: check all sounding times concurrently per station for faster verification
- fix: verify station data availability before showing options, search wider candidate pool
- fix: immediate loading state, auto-fallback to previous sounding times, cleanup ephemeral messages on success
- fix: only show sounding times that are in the past
- feat: add RAOB sounding cog with /sounding slash command, interactive station/time selection, dark mode preference
- fix: widen CSU-MLP poll window to 22 UTC, WxNext2 to 12 UTC, add periodic_cleanup status label
- docs: update README with hodograph feature, new dependencies, and project structure
- fix(hodograph): use sys.executable to run vad.py in active venv
- feat: add hodograph cog with /hodograph slash command
- docs: fix slash command names and add missing /wpc and /downloaderstatus
- docs: add CONTRIBUTING.md with architecture and operator reference
- test: add unit tests for watches VTEC parsing and API failure handling
- fix(watches): distinguish API failure from empty watch list
- chore: remove cig_migration() dead code
- improve: tighten CSU-MLP and WxNext2 poll windows, add friendly task labels to /status
- refactor: rename SCP_CHANNEL_ID to MODELS_CHANNEL_ID for clarity
- docs: add Prerequisites section with Python version and dependency requirements
- docs: add venv creation step to manual setup instructions
- docs: restructure setup section for consistency, remove redundant systemctl commands
- feat: add deploy.sh with systemd service setup and update README
- test: add tests for CSU-MLP URL builders, state persistence, and NCAR WxNext2
- ci: add GitHub Actions workflow to run tests on push and PR
- docs: update README with NCAR WxNext2 feature and project structure
- feat: add NCAR WxNext2 cog with /wxnext slash command and daily auto-post
- fix: use key=str in sorted() to handle mixed int/str posted state
- feat: add CSU-MLP 6-panel slash commands and auto-post, update README
- fix: persist CSU-MLP posted state across reboots using cache JSON
- fix: CSU-MLP days 4-8 use 00z only, use Content-Type check instead of HEAD for URL validation
- feat: add CSU-MLP cog with /csu1-8 slash commands and polling auto-post
- v2.0.0: major refactor
- reduce watchdog interval from 10 to 2 minutes for faster task recovery
- Update README to remove discord.py reference
- Initialize README with project details and features

## [1.2.6] — 2026-04-06

- fix SCP auto-post to always download all 5 images instead of only changed ones

## [1.2.5] — 2026-04-03

- fix filename timestamp parsing for NEXRAD files with underscore separator

## [1.2.4] — 2026-04-03

- fix partial update state being cleared too early when SPC publishes images a minute apart

## [1.2.3] — 2026-04-03

- fix progress embed overflow with smart truncation, fix multi-site zip naming per site

## [1.2.2] — 2026-04-03

- improve radar time selection with Z-to-Z, start+duration, explicit range, and better error messages
- fix gitignore to catch rotated log files
- remove log files from tracking

## [1.2.1] — 2026-04-03

- fix radar timeout with retries, quiet routine logs
- status: fix IP detection using UDP socket
- status: add hostname and IP to /status output
- radar: add download timeout, better error handling and user messaging

## [1.2.0] — 2026-03-24

- (tag-only / no code changes since v1.1.2)

## [1.1.2] — 2026-04-01

- status: fix IP detection using UDP socket

## [1.1.1] — 2026-04-01

- status: add hostname and IP to /status output

## [1.1.0] — 2026-04-01

- radar: add download timeout, better error handling and user messaging
- add radar downloader cog
- update gitignore
- remove cache dir from git tracking

## [1.0.0] — 2026-03-24

### Initial
- initial working cog structure

