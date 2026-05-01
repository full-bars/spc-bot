# WxAlert SPCBot v5.6 Development Progress

## Completed Pull Requests (v5.5 - v5.6 Overhaul)

- **#176: feat(state): persistent sync queue and robust failover mirroring → v5.5.0**
- **#177: fix(warnings): handle null damage-threat params from NWS API → v5.5.1**
- **#178: fix/feat: codebase audit fixes + warning format overhaul → v5.5.2**
  - Hyperlinked action verbs (IEM VTEC links).
  - State grouping ([MS], [AR] and [MS]).
  - Relative timestamps ([<t:ts:R>]).
- **#179: feat: severity-specific event names and detailed warning tags → v5.5.3**
  - "Tornado Emergency" and "Flash Flood Emergency" event names.
  - (PDS) suffix for Particularly Dangerous Situation warnings.
  - Detailed SVR/FFW detection tags (parenthetical formatting).
- **#180: feat: partial cancellation detection and 'updates' format → v5.5.4**
  - Stored 'area' in posted_warnings to detect changes in CON products.
  - "**cancels** X, **continues** Y" formatting.
- **#181: feat: modernize LSR and PNS report formats → v5.5.5**
  - Single-line descriptions with hyperlinked events and relative timestamps.
- **#182: feat: specialized footer IDs (EMERG, PDS, EWX) → v5.5.6**
  - Added specialized IDs to warning/cancellation embeds for downstream filtering.
- **#183: feat: specialized ASOS/AWOS parsing and peak wind extraction → v5.5.7**
  - Auto-identifies automated sensors and extracts peak gusts (e.g., "Peak wind 45kt at 22:20Z").
- **#184: fix: MD fallback, SigWX cleanup, and LSR explanation → v5.5.8**
  - Fixed 404s in MD fallback; limited SigWX tracking to confirmed Tornadoes only.
- **#185: fix: Tornado tracking refinements and pagination → v5.5.9**
- **#186: feat: Tornado Dashboard, EF distinctions, and DAT link integration → v5.6.0**
  - Interactive "Calendar-style" summary dashboard for /recenttornadoes.
  - Color-coded EF rating emojis.
  - Automatic linking to official NWS Damage Assessment Toolkit (DAT) tracks.
- **#187: fix: Dashboard refinements and documentation updates → v5.6.1**
  - Increased retrieval limits to 1000; fixed Tornado Archive URL encoding.
  - Full updates to README, CONTRIBUTING, and CREDITS.
- **#188: feat: Tornado Analytics Overhaul and Dashboard Redesign → v5.6.2**
  - Switched to "Single Card" UI for /recenttornadoes and /sigtor.
  - Photos button with scrollable damage photo carousel from DAT.
  - New Analytics Cog: /topstats, /dayssince, /dailyrecap, /tornadoheatmap, /riskmap, /verify (IEM Cow).
  - Calculated warning-to-report Lead Time tracking.
- **#189: fix: correct all IEM Autoplot numbers and parameters → v5.6.3**
- **#190: fix: prevent false warning cancellation spam → v5.6.4**
  - Correctly tracked CON/EXT/UPG actions as 'active' in the backup poll.
- **#191: fix: reliable MD fallback via stable IEM endpoint → v5.6.5**
  - Switched to retrieve.py text-based endpoint to resolve 422 errors.
- **#192: fix: Damage Survey parsing and MD spam prevention → v5.6.5 patch**
  - Fixed PNS NameError; added date-filtering to IEM MD fallback.
- **#193: fix: prevent mass MD cancellations at UTC midnight → v5.6.6 patch**
  - Implemented rolling 24-hour lookback for IEM fallback to handle midnight flip.
- **#194: fix: warning cancellation spam and missing issuances → v5.6.6**
  - Added session-based `_cancelled_warnings` set to block re-activation loops.
  - Enabled "Initial Discovery" posts for active warnings missed during startup.
- **#195: fix(bugs): four critical one-liner fixes → v5.7.0**
  - `failover.py`: `/failover` command now stores hostname instead of role prefix in Upstash override key.
  - `models/nws.py`: Added `windDetection` and `hailDetection` fields to `NWSAlertParameters`; Pydantic no longer strips SVR detection-method tags.
  - `mesoscale.py`: Added `timedelta` to datetime import, fixing latent `NameError` in IEM MD fallback.
  - `warnings.py`: `TornadoDashboardView.build_card_embed()` uses `_vtec_url()` helper instead of inline URL with hardcoded fields.
- **#196: fix(reliability): async task race conditions and lifecycle → v5.7.1**
  - `sounding.py`: `monitor_special_soundings` and `monitor_high_risk_soundings` now claim sounding keys atomically at check time to prevent TOCTOU double-posts.
  - `watches.py` / `mesoscale.py`: Upgrade background tasks tracked in `_pending_tasks` and cancelled on `cog_unload`, preventing standby nodes from editing Discord messages.
  - `watches.py`: `auto_post_watches` guards sounding task creation with `_handled_watches` check, eliminating no-op task accumulation over long watches.
- **#197: fix(reliability): S3 pagination and botstalk startup flood → v5.7.2**
  - `cogs/radar/s3.py`: `list_files` now paginates via `ContinuationToken` — previously silently truncated at 1,000 objects on busy sites.
  - `cogs/iembot.py`: botstalk poller fast-forwards to current tail seqnum on first run (seqnum=0) instead of firing hundreds of `_handle_warning` tasks from backlog.
- **#198: chore(debt): datetime modernization and state hygiene → v5.7.3**
  - `datetime.utcnow()` replaced in `vad.py` and `vad_reader.py`.
  - Naive `datetime.now()` → `datetime.now(timezone.utc)` in `outlooks.py` (5 sites).
  - `auto_post_spc48` now persists posted URLs on success.
  - Hardcoded `"2026/"` S3 prefix made dynamic.
  - IEM MCD nwstext limit raised 20 → 50.
  - Stale `# noqa` comment removed from `warnings.py`.
- **#199: refactor(debt): extract shared utilities → v5.7.4**
  - `_download_warning_image()` helper replaces two identical IEM image retry blocks in `warnings.py`.
  - `utils/geo.py` created with `haversine()`; `asos.py` and `sounding_utils.py` updated to import from it.
- **#200: test: comprehensive pipeline coverage → v5.7.5**
  - `tests/test_reports.py`: Full coverage for LSR/PNS parsing, lead-time, and DAT integration.
  - `tests/test_analytics.py`: Coverage for all analytics slash commands and Autoplot URL logic.
  - `tests/test_warnings.py`: Added coverage for the NWS API `_tick` poll path (discovery, updates, expirations).
  - `tests/test_mesoscale.py`: Added coverage for IEM fallback parsing logic.
- **#201: feat: persistence overhaul and Syncthing optimization → v5.7.6**
  - `SoundingCog`: Migrated deduplication sets to persistent SQLite tables with Upstash sync.
  - `BotState`: Added `active_mds` to failover state serialization.
  - `events_db.py`: Implemented dirty-flag gating for Syncthing snapshots.

---

## v5.6 Status: COMPLETE — v5.6.6 live in production.

## v5.7 Status: COMPLETE — v5.7.6 ready for deployment.

---

## v5.7 Roadmap: Stability & Maintenance (COMPLETE)

Items below were identified in a full codebase audit (2026-04-30). Ordered by severity.

### Bugs (confirmed broken behavior)
- [x] **`/failover` command silently fails** (Fixed in #195)
- [x] **`windDetection`/`hailDetection` silently dropped by Pydantic** (Fixed in #195)
- [x] **`timedelta` not imported in `mesoscale.py`** (Fixed in #195)
- [x] **`TornadoDashboardView` hardcodes `"NEW"` in VTEC URL** (Fixed in #195)

### Reliability / Race Conditions
- [x] **`monitor_special_soundings` and `monitor_high_risk_soundings` race** (Fixed in #196)
- [x] **Orphaned upgrade tasks survive standby demotion** (Fixed in #196)
- [x] **`auto_post_watches` fires no-op sounding tasks** (Fixed in #196)
- [x] **S3 `list_files` has no pagination** (Fixed in #197)
- [x] **Botstalk seqnum=0 on first run floods the event loop** (Fixed in #197)

### Tech Debt
- [x] **Hardcoded year `2026` in radar S3 prefix** (Fixed in #198)
- [x] **`datetime.utcnow()` deprecated** (Fixed in #198)
- [x] **Naive `datetime.now()` in outlooks** (Fixed in #198)
- [x] **`auto_post_spc48` doesn't persist posted URLs** (Fixed in #198)
- [x] **Stale `# noqa` comment in `warnings.py`** (Fixed in #198)
- [x] **IEM MCD `limit=20` cap** (Fixed in #198)
- [x] **Duplicated IEM image download retry logic** (Fixed in #199)
- [x] **Duplicated haversine** (Fixed in #199)

### Test Coverage
- [x] **`tests/test_reports.py`** (Fixed in #200)
- [x] **`tests/test_md.py` (via `test_mesoscale.py`)** (Fixed in #200)
- [x] **`AnalyticsCog` tests** (Fixed in #200)
- [x] **NWS API warning poll path (`_tick`) tests** (Fixed in #200)
- [ ] **`SoundingCog` auto-posting methods untested** (Planned for v5.8)
- [ ] **`MaintenanceCog`, `SCPCog`, `RadarCog` have no tests** (Planned for v5.8)

### Persistence & Cleanup (COMPLETE)
- [x] **Sounding State Persistence** (Fixed in #201)
- [x] **BotState Cleanup** (Fixed in #201)
- [x] **Syncthing Snapshot Optimization** (Fixed in #201)

---

## Known Issues (active in production)

- **IEMBot connection instability**: Circuit breaker occasionally opens for weather.im during high load; backup NWS API poll mitigates delay.
- **PNS Summary Extraction**: Some WFOs use non-standard summary formats, leading to "No summary available" placeholders.
- **SPC MD index instability**: SPC's MD HTML index experiences frequent short outages (observed multiple times daily). IEM fallback is working but IEM's own SWOMCD endpoint returns 422 intermittently.
- **`SVS`/`FFS` products arrive via botstalk but produce no output**: Needs a decision on whether to post these text-only updates.
- **SPS severe-only filter not implemented**: All SPS products are posted unconditionally. 
- **TGFTP dependency for hodograph**: VAD data is fetched from `tgftp.nws.noaa.gov`. Fallback needs investigation.
