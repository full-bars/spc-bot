# Release Notes - v5.36.5

## 🪄 AI Sounding & Hodograph Analysis

This release extends the bot's AI capabilities to vertical profiles, allowing users to get a plain-English, meteorologist-grade summary of the environment when viewing RAOB soundings or VWP hodographs.

### What's Changed
- **Thermodynamic & Kinematic Synthesis:** The bot seamlessly extracts complex parameters (SBCAPE, SRH, Bulk Shear, Effective Inflow Layer, etc.) computed by `sounderpy` and feeds them into Gemini to determine storm mode and hazard types.
- **Proactive Generation:** The AI analysis is generated in the background the moment a plot is generated. This pre-warms the cache so the result is displayed instantly when a user clicks the "🪄 AI Analysis" button.

### Full Changelog
[v5.36.4...v5.36.5](https://github.com/full-bars/spc-bot/compare/v5.36.4...v5.36.5)