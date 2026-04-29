# WxAlert SPCBot v5.4 Development Progress

## ✅ Completed Pull Requests

- **#164: build(docker): Multi-stage Docker optimization**
  - Implemented `pip wheel` builder pattern.
  - Stripped build tools from final runtime image for a smaller, more secure container.
- **#165: chore(storage): Cache Artifact Lifecycle Manager**
  - Created `cogs/maintenance.py`.
  - Added daily background task to prune old map images and temporary files (>48h).
- **#166: perf(plotting): VAD/Hodograph Executor Migration**
  - Migrated VAD plots from CLI subprocesses to the pre-warmed `ProcessPoolExecutor`.
  - Eliminated ~1.5s cold-start import penalty for radar requests.
- **#167: feat(http): Global Circuit Breaker & Retries**
  - Integrated `tenacity` for exponential backoff.
  - Implemented `CircuitBreaker` to fail-fast when NWS/SPC APIs are degraded.
  - Added global Discord error handler for degraded upstream services.
- **#168: feat(api): Pydantic Models for NWS Alerts**
  - Introduced strict schema validation at the API boundary.
  - Replaced unsafe dict traversal with type-safe model access.
- **#169: fix(core): Critical Alerting & Cleanup**
  - Fixed `fetch_channel` fallback in `send_bot_alert` (verified production gap).
  - Resolved several bare `except: pass` blocks in the reporting pipeline.

## 🚧 In Progress

- **#170: fix(reports): Persistent LSR Deduplication**
  - Moved `posted_reports` to persistent SQLite + Upstash state.
  - Standardized Hail/Wind `event_id` generation for consistent DB tracking.
  - Made LSR logging atomic (persist before Discord send).
  - *Current Status:* Resolving minor test regression in `test_state_split.py`.

## 📋 Remaining v5.4 Tasks

- **#171: fix(warnings): Warning Pipeline Hardening**
  - Implement atomic check-and-claim for VTEC IDs.
  - Isolate cancellation edit errors from blocking the poll loop.
  - Fix MD cancellation logic when the index is empty.
- **#172: fix(state): State Sync & Failover Tightening**
  - Cap `_dirty_queue` size to prevent memory leaks during outages.
  - Tighten Dual-Primary window during failover pre-emption.
  - Refine CSU-MLP daily reset logic for early UTC restarts.
- **#173: chore(db): Syncthing Snapshot Consistency**
  - Add `PRAGMA wal_checkpoint(RESTART)` before database backups.
