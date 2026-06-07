# Release Notes - v5.37.0

## 🧠 Context-Aware AI Engine & PDS Enhancements

This release significantly upgrades the bot's scientific analysis capabilities by making the AI subsystem "environmentally aware" and introduces high-impact styling for Particularly Dangerous Situation (PDS) watches.

### 🪄 Context-Aware AI Analysis
The "🪄 AI Analysis" for hodographs and soundings is no longer a localized data dump. The engine now automatically cross-references local parameters with broad SPC thinking:
- **Regional Outlook Synthesis:** Gemini now receives the Day 1 Outlook regional summaries to understand the expected convective mode (e.g., QLCS vs Discrete).
- **Mesoscale & Watch Context:** Active MDs and nearby watches are injected into the prompt to provide real-time hazard context.
- **Thermodynamic Injection:** VAD hodographs now automatically "pull" thermodynamic data (CAPE/CIN) from the nearest RAOB station, giving Gemini a complete atmospheric profile for its analysis.

### 🚨 High-Impact PDS Alerts
PDS watches now demand attention with an updated styling suite:
- **Black Sidebar:** Switched to a high-impact black color (`0x000001`) for maximum contrast.
- **PDS Branding:** Embedded title is prefixed with `⚠️ PDS` and includes a prominent emergency banner.

### What's Changed
- **Accuracy Fixes:** Updated AI prompts to reduce overstated hail risks in multicellular/MCS environments.
- **Rust Performance:** Fully integrated `nom`-based Rust parsers for VTEC and warning polygons.
- **Improved Location Engine:** AI can now resolve site IDs from cache keys even during manual button interactions.

### Full Changelog
[v5.36.5...v5.37.0](https://github.com/full-bars/spc-bot/compare/v5.36.5...v5.37.0)
