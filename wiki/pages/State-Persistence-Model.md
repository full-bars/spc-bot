# State Persistence Model 💾

SPCBot employs a sophisticated hybrid persistence architecture designed for high performance, reliability, and ease of backup.

## 🏛️ The Three Tiers

### 1. In-Memory (`BotState`)
The `bot.state` object provides high-speed access to volatile data (active tasks, current latencies, temporary caches). This is rehydrated from persistent storage at startup.

### 2. Shared Operational State (Redis)
Used for shared state and leader election in High Availability setups. Backed by a self-hosted Redis 7+ instance on the primary node, accessible to the standby via Tailscale.
- **Efficiency:** Only small identifiers (VTEC IDs, MD numbers, URLs) are stored here.
- **Sync:** A "Dirty Write Reconciler" ensures that if Redis is temporarily unreachable, local writes are queued and synced once connectivity returns.

### 3. Durable Local Mirror (SQLite)
The `cache/bot_state.db` file acts as the local durability layer. State writes land in SQLite before the best-effort Redis write, so a single node can keep operating through a Redis outage.
- **WAL Mode:** Uses Write-Ahead Logging for safety and performance.
- **Tables:**
  - `image_hashes`: Change detection for SPC/WPC graphics.
  - `posted_mds` / `posted_watches`: Deduplication sets.
  - `posted_warnings`: Warning lifecycle tracking metadata.
  - `bot_state`: Key/Value store for feature-specific state (CSU-MLP, NCAR).

## 🌪️ Significant Events Archive (`events.db`)

Historical weather records are stored in a separate `cache/events.db` file.
- **Rationale:** This database is excluded from Redis to avoid spending operational state bandwidth on historical records.
- **Content:** Confirmed tornadoes, EF ratings, lead times, DAT damage survey links, and VAD environmental forensics. Hail and wind are no longer tracked in this archive.
- **Retention:** A 365-day rolling retention policy is enforced by `cogs/maintenance.py` for tornado events. Operational dedupe sets such as MDs, watches, warnings, and reports are pruned separately by their state-store helpers.

## 🔄 Replication via Syncthing

For HA pairs, the `events.db` is replicated using **Syncthing**. The bot automatically manages Syncthing folder modes:
- **Primary:** Sets folder to `Send-Only`.
- **Standby:** Sets folder to `Receive-Only`.
- Mode flipping occurs automatically during promotion/demotion cycles.

## 🧾 State Ownership Cheat Sheet

| State | Primary Location | Mirror/Fallback | Purpose |
|---|---|---|---|
| Active runtime metrics | `bot.state` | None | Latency, uptime, role, current task health. |
| Image hashes | Redis / `bot_state.db` | In-memory cache | Prevent duplicate SPC/model image posts. |
| Posted MD/watch/report IDs | Redis / `bot_state.db` | In-memory cache | Deduplicate alert products across restarts and HA nodes. |
| Posted warning metadata | Redis / `bot_state.db` | In-memory cache | Track lifecycle updates and Discord message targets. |
| Simple feature state | Redis key/value state | `bot_state` table | CSU-MLP, NCAR, IEMBot sequence numbers, sounding preferences, and similar feature flags. |
| HTTP validators | `http_validators` table | In-memory LRU | Avoid unnecessary downloads using ETag/Last-Modified. |
| Product text cache | `product_text_cache` table | Redis when configured | Short-lived NWS/SPC product text cache. |
| Significant event history | `events.db` | Syncthing copy in HA setups | Confirmed tornado/survey archive and DAT enrichment. |
| VAD evolution GIFs | Filesystem archive | rclone remote when configured | Forensic media output. |

## 🚀 Startup Hydration Flow

1. `main.py` initializes SQLite and checks DB integrity.
2. In-memory dedupe caches are hydrated from the state store.
3. HTTP validators are loaded so conditional GETs work after restart.
4. Startup cache cleanup runs before cogs begin regular polling.
5. The failover cog checks the Redis lease.
6. Posting cogs load only if the node should run as primary.
