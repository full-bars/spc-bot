# Changelog

All notable changes to this project will be documented in this file. Format
loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
version numbers follow [SemVer](https://semver.org/).

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

