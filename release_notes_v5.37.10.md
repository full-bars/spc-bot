> [!NOTE]
> **Reliability & Coverage Update:** This release focuses heavily on fortifying the bot's core architecture through massive test coverage expansions, alongside squashing edge-case bugs related to Discord image caching and watch cancellations.

**🐛 Bug Fixes**
* **Watch Cancellation Glitch**: Implemented an SPC active watch index fallback in `watches.py` and `watch_fetch.py` so cancelled watches accurately clear from the active board.
* **WPC Surface Fronts**: Bypassed persistent Discord CDN/crawler cache blocks by downloading fronts images in-memory and uploading them as raw file attachments.

**🧪 Testing & Reliability**
* **Massive Unit Test Expansion**: Added dozens of new `pytest` unit tests, significantly boosting coverage across critical systems:
  * Warning routing and Discord channels (`warning_channels.py`)
  * Weather summaries (`wxsummary.py`)
  * Warning UI embeds (`warning_ui.py`)
  * Sounding math and views (`sounding_utils.py`, `sounding_views.py`)
  * Radar S3 downloading and animations (`radar/downloads.py`, `radar/views.py`)
  * XMPP NWWS ingestion (`nwws.py`)
  * SPC Categorical Outlook processing (`outlooks.py`)

--------
## What's Changed
* fix: watch cancellation glitch by @full-bars in https://github.com/full-bars/spc-bot/pull/521
* test: test coverage for warning_channels and wxsummary by @full-bars in https://github.com/full-bars/spc-bot/pull/522
* fix: upload WPC fronts images directly to bypass Discord crawler blocks by @full-bars in https://github.com/full-bars/spc-bot/pull/523

**Full Changelog**: https://github.com/full-bars/spc-bot/compare/v5.37.9...v5.37.10
