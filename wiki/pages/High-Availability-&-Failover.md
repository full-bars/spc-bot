# High Availability & Failover 🔄

SPCBot supports a robust Active/Standby failover pair to ensure near-100% uptime for severe weather alerts.

## 🏗️ Architecture

The failover system uses a **self-hosted Redis 7+** instance on the primary node as a distributed lock manager and shared operational state store. Nodes communicate over **Tailscale** (or any private network with Redis port reachability). No direct HTTP tunnel is required between nodes.

- **Primary Node:** Holds the Redis lease, runs all polling loops, and posts to Discord.
- **Standby Node:** Points `ELECTION_REDIS_URL` at the primary's Redis. If the primary's Redis becomes unreachable, or the lease is missing for enough consecutive heartbeat cycles, it promotes itself to Primary.

## 🧭 Deployment Decision Table

| Setup | Use When | Required Services | Tradeoffs |
|---|---|---|---|
| Single node | Personal server, development, or low operational complexity. | Discord token and channel IDs. | Simple, but one host outage stops automation. |
| Single node + NWWS | You want the low-latency text-product fast path without HA. | NWWS-OI credentials. | Better alert latency, still one host. |
| Primary/Standby | Severe-weather operations where host uptime matters. | Local Redis 7+, Tailscale, and two bot hosts. | More moving parts, but automated promotion. |
| Primary/Standby + Syncthing | You need the historical events archive available immediately after promotion. | Local Redis, Tailscale, Syncthing, shared folder config. | Best continuity, requires storage sync care. |

## 🗳️ Leader Election Logic

Every node runs a `sync_loop` that heartbeats every 30 seconds:

1. **Node Registration:** Writes `self._identity` (e.g. `P:3cape:ac8e06b3`) with a timestamp into `spcbot:nodes` hash so `/status` can list all live nodes.
2. **Lease Acquisition:** Uses `SET NX EX` to atomically claim the primary lease at `spcbot:primary_url`.
3. **Extension:** The current primary conditionally extends a 420-second lease via a Lua script that only writes if the caller still holds the key — prevents a demoted node from accidentally reclaiming after a split-brain.
4. **Standby Promotion:** A standby promotes after the primary's Redis is unreachable or the lease is missing for `MAX_FAILURES` consecutive cycles (currently 7 × 30 s = **210 seconds**).
5. **Startup Grace:** Newly loaded failover cogs ignore missing-lease failures for 120 seconds to avoid startup flapping.
6. **Manual Override:** `/failover` can write `spcbot:manual_primary` to designate a hostname until the override is cleared.

## ⏱️ Measured Failover Timing

| Scenario | Time |
|---|---|
| Primary crash → standby fully live as primary | ~210 s (3m 30s) |
| Restore primary (stop standby → restart primary → restart standby) | ~30 s |

## 💾 State Synchronization

While the failover manages *who* posts, the state must remain consistent.

- **Redis (primary node):** Serves as the operational source of truth. All MD/Watch/Warning IDs are double-written to Redis and SQLite.
- **SQLite Mirror:** A local `bot_state.db` provides durable outage survival when Redis is temporarily unreachable.
- **Dirty Write Reconciler:** If a Redis write fails, the key is queued and retried every 30 s until it lands.
- **Syncthing:** Replicates the historical `events.db` archive cross-node, ensuring the Standby has the full record if it promotes.

## ⚙️ Environment Checklist

### Both nodes

| Variable | Primary | Standby |
|---|---|---|
| `DISCORD_TOKEN` | Same bot token | Same bot token |
| `GUILD_ID` | Same guild ID | Same guild ID |
| `SPC_CHANNEL_ID` / `MODELS_CHANNEL_ID` | Same channel IDs | Same channel IDs |
| `FAILOVER_TOKEN` | Same shared secret | Same shared secret |
| `ADMIN_USER_ID` | Same authorized operator | Same authorized operator |
| `IS_PRIMARY` | `true` | `false` |
| `REDIS_URL` | `redis://localhost:6379/0` | `redis://localhost:6379/0` |

### Standby node only

| Variable | Value |
|---|---|
| `ELECTION_REDIS_URL` | `redis://<primary-tailscale-ip>:6379/0` |

> **Why two Redis URLs?** The standby's local Redis is a read-only replica of the primary's. `REDIS_URL` (localhost) is used for all application state reads — replicated data is available there. `ELECTION_REDIS_URL` (primary's Tailscale IP) is used exclusively for leader-election writes and lease checks. When the primary goes down, connection errors to `ELECTION_REDIS_URL` are what trigger the failure counter, not a stale replica TTL.

## 🎮 Manual Intervention

Authorized operators can manage failover using:
- `/failover`: Opens an interactive selector populated from the active node registry. Selecting a node designates that host as primary; clearing the override returns the pair to automatic election.
- `/status`: Shows which node is currently primary, its hostname, and IP. Displays an orange **PRIMARY ⚠️ FAILOVER** badge when a standby-configured node is acting as primary.

## 🛡️ Standby Behavior

To prevent Discord interaction hijacking and double-posting:
- Standby nodes suppress all automated polling loops.
- All posting cogs are unloaded.
- `CommandNotFound` errors are swallowed to prevent the Standby from responding to commands intended for the Primary.

## 🔧 Promotion Steps (What the Bot Does)

When a standby promotes:
1. Updates its node identity from `S:` to `P:` prefix and purges old entries from the nodes hash.
2. Issues `REPLICAOF NO ONE` to its local Redis replica (via a dedicated localhost client) so writes succeed immediately.
3. Invalidates the in-process cache.
4. Writes its own leader lease.
5. Rehydrates `bot.state` from Redis.
6. Pushes any SQLite-queued dirty writes to Redis.
7. Loads all posting cogs and syncs the Discord slash-command tree.

## ✅ Promotion Verification

After a planned or automatic promotion:

1. `/status` shows exactly one `PRIMARY` (orange badge if it was a standby).
2. The promoted node has loaded posting cogs and background loops.
3. Syncthing folder mode is `send-only` on the primary and `receive-only` on standby when configured.
4. New MD/watch/warning posts are deduplicated against already-posted state.
5. The demoted node does not sync slash commands or run auto-post loops.
6. If a manual override was used, `/failover` shows the expected override or has been cleared.
