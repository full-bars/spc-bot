# High Availability & Failover 🔄

SPCBot supports a robust Active/Standby failover pair to ensure near-100% uptime for severe weather alerts.

## 🏗️ Architecture

The failover system uses **Upstash Redis** as a distributed lock manager (Lease Store). No direct network connection or HTTP tunnel is required between the two nodes.

- **Primary Node:** Holds the Upstash lease, runs all polling loops, and posts to Discord.
- **Standby Node:** Heartbeats to Upstash. If the lease is available or expired, it promotes itself to Primary.

## 🗳️ Leader Election Logic

Every node runs a `sync_loop` that heartbeats to Upstash every 10 seconds:
1. **Lease Acquisition:** Uses `SET NX EX` to atomically claim the "Primary" role.
2. **Extension:** The current Primary extends its lease as long as it remains healthy.
3. **Safety Shield:** A "Startup Shield" prevents a newly rebooted node from immediately stealing the lease if the current Primary is healthy, protecting against "flapping" during network instability.

## 💾 State Synchronization

While the failover manages *who* posts, the state must remain consistent.
- **Upstash Redis:** Serves as the operational "Source of Truth." All MD/Watch/Warning IDs are double-written to Upstash.
- **SQLite Mirror:** A local `bot_state.db` provides a durable mirror and handles outage survival if Upstash is unreachable.
- **Syncthing:** Replicates the historical `events.db` archive cross-node, ensuring the Standby has the full record if it promotes.

## 🎮 Manual Intervention

Authorized operators can force a role swap using:
- `/failover`: Triggers a graceful demotion of the current Primary and allows the Standby to promote.
- `/status`: Shows which node is currently Primary, its hostname, and IP.

## 🛡️ Standby Behavior

To prevent Discord interaction hijacking and double-posting:
- Standby nodes suppress all automated polling loops.
- All cogs are set to "idle" state.
- `CommandNotFound` errors are swallowed to prevent the Standby from responding to commands intended for the Primary.
