# Welcome to the SPCBot Wiki 🛰️

**SPCBot** (v5.13.0) is a high-performance, severe weather monitoring Discord bot. Built for enthusiasts and researchers, it delivers near-zero latency alerts, real-time analytics, and automated scientific plots.

## 🚀 Key Features

- **Gold-Standard Alerting:** NWWS-OI (XMPP) integration for sub-second latency on NWS products.
- **Warning Lifecycle:** Real-time tracking of `SVR`, `TOR`, and `FFW` warnings including status updates (`CON`, `EXT`, `EXA`).
- **Tornado Dashboard:** Automated damage survey detection, official DAT photo carousels, and local OSM track rendering.
- **Scientific Analysis:** Automated RAOB/ACARS sounding plots, VWP hodographs, and CSU-MLP/WxNext2 AI forecasts.
- **High Availability:** Active/Standby failover with leader election and state synchronization.

## 📖 Wiki Sections

### [Getting Started](Getting-Started)
Learn how to install SPCBot via Docker or systemd, configure your environment, and get the bot online.

### [Core Features](Core-Features)
- **[Alerting & Authority](Alerting-Authority-&-NWWS-OI)**: Hierarchy of data sources and latency advantages.
- **[Warning Lifecycle & Updates](Warning-Lifecycle-&-Updates)**: How VTEC products are parsed and tracked.
- **[Tornado Dashboard & DAT Integration](Tornado-Dashboard-&-DAT-Integration)**: Survey tracking and photo carousels.

### [Scientific Tools](Scientific-Tools)
- **[Soundings & Hodographs](Soundings-&-Hodographs)**: Observed data plotting and auto-posting logic.
- **[Forecast Models](Forecast-Models)**: CSU-MLP, NCAR WxNext2, and SCP graphics.

### [Advanced Architecture](Advanced-Architecture)
- **[High Availability & Failover](High-Availability-&-Failover)**: Primary/Standby logic and Upstash leases.
- **[State Persistence Model](State-Persistence-Model)**: Hybrid Upstash Redis + SQLite architecture.

### [Reference](Reference)
- **[Slash Command Reference](Slash-Command-Reference)**: Complete guide to bot interactions.
- **[Maintenance & Operations](Maintenance-&-Operations)**: Monitoring tools and admin commands.
