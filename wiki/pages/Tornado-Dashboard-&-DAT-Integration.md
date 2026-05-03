# Tornado Dashboard & DAT Integration 📂

SPCBot includes a sophisticated suite for tracking confirmed tornadoes and damage surveys, bridging the gap between real-time alerts and post-storm analysis.

## 📋 The Dashboard (`/recenttornadoes` & `/sigtor`)

The `/recenttornadoes` command provides a centralized view of all confirmed tornadoes logged by the bot.
- **Summary View:** A high-level breakdown of EF ratings (🟣EF5 to ⚪EFU) for the last 30 days.
- **Card View:** Detailed interactive cards for each event, including location, rating, and office.
- **Significant Events (`/sigtor`):** A filtered view specifically for EF2+ or PDS (Particularly Dangerous Situation) tornadoes.

## 🏁 Lead Time Tracking

For every confirmed tornado, the bot attempts to calculate the **Lead Time**—the duration between the initial Tornado Warning issuance and the first report of the tornado on the ground.
- This metric is automatically added to the dashboard cards once a matching LSR (Local Storm Report) is received.

## 🛰️ NWS Damage Assessment Toolkit (DAT)

The bot is deeply integrated with the NWS DAT for high-resolution post-storm data:
- **Automated Survey Detection:** The bot monitors PNS (Public Information Statements) for "Damage Survey" keywords.
- **Local Track Rendering:** Using official DAT geometry, the bot renders high-detail tornado track maps on OpenStreetMap (OSM) tiles locally, providing a cleaner alternative to standard static maps.
- **Interactive Photo Carousels:** If official damage photos are available in the DAT, the bot provides a paginated carousel (via the **📸 Photos** button) to view them directly in Discord.

## 🧬 Matching Logic

The bot uses a **geographic and temporal matching engine** to link real-time warnings to post-storm surveys:
1. **LSR Linkage:** Links "Confirmed Tornado" reports to the warning that covered them.
2. **Survey Linkage:** Uses a haversine distance search (50km radius) to link DAT tracks to the original "Significant Events" logged in the bot's `events.db`.

## 💾 The `events.db` Archive

All significant events are stored in a dedicated SQLite archive (`cache/events.db`) with a rolling **365-day retention**. This database is separate from the operational state to ensure it can grow to thousands of records without impacting bot performance.
