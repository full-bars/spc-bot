# Release Notes - v5.36.4

## 🚀 Proactive AI Analysis
This release significantly improves the performance of the AI analysis features by making them proactive.

### Instant AI Analysis
Previously, clicking the AI button would trigger a fresh generation that took ~10 seconds. Now, the bot proactively generates and caches the analysis for both Convective Outlooks and Mesoscale Discussions as soon as they are posted. 

When you click the button, the result is now **instant**.

### UI Unification
- Renamed the Mesoscale Discussion **🪄 TL;DR** button to **🪄 AI Analysis** to match the Convective Outlook UI.

### Technical Details
- Refactored AI generation logic into standalone, reusable functions.
- Integrated background task triggers into `cogs/outlooks.py` and `cogs/mesoscale.py`.
- Improved cache hit rates by pre-warming the cache upon product detection.

### Full Changelog
[v5.36.3...v5.36.4](https://github.com/full-bars/spc-bot/compare/v5.36.3...v5.36.4)
