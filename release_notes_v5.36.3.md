# Release Notes - v5.36.3

## 🪄 AI Analysis Cache Refresh
This release fixes a high-priority issue where the AI analysis for SPC outlooks would serve stale data if a new outlook was issued at the same URL (e.g., Day 1 Convective Outlook updates).

### What's Changed
- **Content-Based Caching:** The bot now hashes the outlook text and uses it to version the AI summary cache. This ensures that any update to the outlook content immediately triggers a fresh AI analysis.
- **Improved Reliability:** Added a new test suite to verify caching logic and prevent future regressions.

### Full Changelog
[v5.36.2...v5.36.3](https://github.com/full-bars/spc-bot/compare/v5.36.2...v5.36.3)
