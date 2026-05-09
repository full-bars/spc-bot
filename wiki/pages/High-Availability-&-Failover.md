# High Availability & Failover 🔄

SPCBot supports a robust Active/Standby failover pair to ensure near-100% uptime for severe weather alerts.

## 🏗️ Architecture

The failover system uses **Upstash Redis** as a distributed lock manager and shared operational state store. No direct network connection or HTTP tunnel is required between the two nodes.

- **Primary Node:** Holds the Upstash lease, runs all polling loops, and posts to Discord.
- **Standby Node:** Heartbeats to Upstash. If the lease is available or expired, it promotes itself to Primary.

## 🧭 Deployment Decision Table

| Setup | Use When | Required Services | Tradeoffs |
|---|---|---|---|
| Single node | Personal server, development, or low operational complexity. | Discord token and channel IDs. | Simple, but one host outage stops automation. |
| Single node + NWWS | You want the low-latency text-product fast path without HA. | NWWS-OI credentials. | Better alert latency, still one host. |
| Primary/Standby | Severe-weather operations where host uptime matters. | Upstash Redis and two bot hosts. | More moving parts, but automated promotion. |
| Primary/Standby + Syncthing | You need the historical events archive available immediately after promotion. | Upstash Redis, Syncthing, shared folder config. | Best continuity, requires storage sync care. |

## 🗳️ Leader Election Logic

Every node runs a `sync_loop` that heartbeats to Upstash every 30 seconds:
1. **Lease Acquisition:** Uses `SET NX EX` to atomically claim the primary lease at `spcbot:primary_url`.
2. **Extension:** The current primary extends a 420-second lease as long as it remains healthy.
3. **Standby Promotion:** A standby promotes after the lease is missing for `MAX_FAILURES` cycles, currently 7 cycles, or about 210 seconds after the key disappears.
4. **Startup Grace:** Newly loaded failover cogs ignore missing-lease failures for 120 seconds to avoid startup flapping.
5. **Manual Override:** `/failover` can write `spcbot:manual_primary` to designate a hostname until the override is cleared.

## 💾 State Synchronization

While the failover manages *who* posts, the state must remain consistent.
- **Upstash Redis:** Serves as the operational "Source of Truth." All MD/Watch/Warning IDs are double-written to Upstash.
- **SQLite Mirror:** A local `bot_state.db` provides a durable mirror and handles outage survival if Upstash is unreachable.
- **Syncthing:** Replicates the historical `events.db` archive cross-node, ensuring the Standby has the full record if it promotes.

## ⚙️ Environment Checklist

Set these on both nodes:

| Variable | Primary | Standby |
|---|---|---|
| `DISCORD_TOKEN` | Same bot token | Same bot token |
| `GUILD_ID` | Same guild ID | Same guild ID |
| `SPC_CHANNEL_ID` / `MODELS_CHANNEL_ID` | Same channel IDs | Same channel IDs |
| `UPSTASH_REDIS_REST_URL` | Same Upstash URL | Same Upstash URL |
| `UPSTASH_REDIS_REST_TOKEN` | Same Upstash token | Same Upstash token |
| `FAILOVER_TOKEN` | Same shared secret | Same shared secret |
| `ADMIN_USER_ID` | Same authorized operator | Same authorized operator |
| `IS_PRIMARY` | `true` | `false` |

## 🎮 Manual Intervention

Authorized operators can manage failover using:
- `/failover`: Opens an interactive selector populated from the active node registry. Selecting a node designates that host as primary; clearing the override returns the pair to automatic election.
- `/status`: Shows which node is currently primary, its hostname, and IP.

## 🛡️ Standby Behavior

To prevent Discord interaction hijacking and double-posting:
- Standby nodes suppress all automated polling loops.
- All cogs are set to "idle" state.
- `CommandNotFound` errors are swallowed to prevent the Standby from responding to commands intended for the Primary.

## ✅ Promotion Verification

After a planned or automatic promotion:

1. `/status` shows exactly one `PRIMARY`.
2. The promoted node has loaded posting cogs and background loops.
3. Syncthing folder mode is `send-only` on the primary and `receive-only` on standby when configured.
4. New MD/watch/warning posts are deduplicated against already-posted state.
5. The demoted node does not sync slash commands or run auto-post loops.
6. If a manual override was used, `/failover` shows the expected override or has been cleared.
