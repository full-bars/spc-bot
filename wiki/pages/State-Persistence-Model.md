# State Persistence Model 💾

SPCBot employs a sophisticated hybrid persistence architecture designed for high performance, reliability, and ease of backup.

## 🏛️ The Three Tiers

### 1. In-Memory (`BotState`)
The `bot.state` object provides high-speed access to volatile data (active tasks, current latencies, temporary caches). This is rehydrated from persistent storage at startup.

### 2. Operational Truth (Upstash Redis)
Used for shared state in High Availability setups. 
- **Efficiency:** Only small identifiers (VTEC IDs, MD numbers, URLs) are stored here.
- **Sync:** A "Dirty Write Reconciler" ensures that if Upstash is temporarily down, local writes are queued and synced once connectivity returns.

### 3. Durable Local Mirror (SQLite)
The `cache/bot_state.db` file acts as the ultimate durability layer.
- **WAL Mode:** Uses Write-Ahead Logging for safety and performance.
- **Tables:**
  - `image_hashes`: Change detection for SPC/WPC graphics.
  - `posted_mds` / `posted_watches`: Deduplication sets.
  - `posted_warnings`: Warning lifecycle tracking metadata.
  - `bot_state`: Key/Value store for feature-specific state (CSU-MLP, NCAR).

## 🌪️ Significant Events Archive (`events.db`)

Historical weather records are stored in a separate `cache/events.db` file.
- **Rationale:** This database grows indefinitely and is excluded from Upstash to avoid hitting Redis storage limits.
- **Content:** Confirmed tornadoes, EF ratings, lead times, and DAT damage survey links.
- **Retention:** A 365-day rolling retention policy is enforced by `cogs/maintenance.py` for significant events, while the main state DB prunes ephemeral data (like MD numbers) automatically based on size caps.

## 🔄 Replication via Syncthing

For HA pairs, the `events.db` is replicated using **Syncthing**. The bot automatically manages Syncthing folder modes:
- **Primary:** Sets folder to `Send-Only`.
- **Standby:** Sets folder to `Receive-Only`.
- Mode flipping occurs automatically during promotion/demotion cycles.
