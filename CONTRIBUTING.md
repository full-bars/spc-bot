# Contributing to WxAlert / SPCBot

This document covers the bot's internal architecture, slash command reference,
channel configuration, and operational behavior for contributors and operators.

---

## Channel Configuration

Two channel IDs are required in `.env`:

| Variable | Purpose |
|---|---|
| `SPC_CHANNEL_ID` | Receives all severe weather alerts — SPC outlooks (Days 1–3), Day 4–8 outlooks, mesoscale discussions, watch alerts and cancellations, and bot health alerts from the watchdog |
| `MODELS_CHANNEL_ID` | Receives model/forecast graphics — SCP twice-daily posts, CSU-MLP daily forecasts, and NCAR WxNext2 daily forecasts |

Slash commands can be used from any channel — they always respond ephemerally
or inline where invoked, not into the configured channels.

---

## Slash Command Reference

### SPC Outlooks
| Command | Description |
|---|---|
| `/spc1` | Fetch and display the latest SPC Day 1 outlook graphics |
| `/spc2` | Fetch and display the latest SPC Day 2 outlook graphics |
| `/spc3` | Fetch and display the latest SPC Day 3 outlook graphics |
| `/spc48` | Fetch and display the latest SPC Day 4–8 outlook graphics |

### Watches & Mesoscale Discussions
| Command | Description |
|---|---|
| `/watches` | Show all currently active SPC watches with details and probabilities |
| `/ww` | Alias for `/watches` |
| `/md` | Show the latest active SPC mesoscale discussion |

### Model Forecasts
| Command | Description |
|---|---|
| `/scp` | Show the latest NIU/Gensini SCP forecast graphics |
| `/csu1` – `/csu8` | Show CSU-MLP ML severe weather forecast for Days 1–8 |
| `/csupanel12` | Show CSU-MLP 6-panel summary for Days 1–2 |
| `/csupanel38` | Show CSU-MLP 6-panel summary for Days 3–8 |
| `/wxnext` | Show the latest NCAR WxNext2 Mean AI convective hazard forecast |

### Radar
| Command | Description |
|---|---|
| `/radar` | Open the NEXRAD Level 2 radar downloader UI (site selection, time range, ZIP download) |

### Status
| Command | Description |
|---|---|
| `/status` | Show bot health: task states, last auto-post times, partial update state, tracked MD/watch counts. Ephemeral. |

---

## How Auto-Posting Works

### SPC Outlooks (Days 1–3): Normal and Aggressive Check Mode

The outlook cog runs two loops concurrently:

**`auto_post_spc` (every 30 seconds)** — the normal loop. For each day (1, 2, 3)
it scrapes the SPC HTML page to resolve the current issuance-time PNG URLs, then
checks if the URLs have changed since the last post. If they have, it downloads
the images and posts them to `SPC_CHANNEL_ID`.

**Partial update detection** — sometimes the SPC page updates its tab URLs before
all images are actually available (returning placeholder content). When this
happens, the cog enters *partial update state* for that day: it records the new
URLs and the time it first saw them, but does not post yet.

**`aggressive_check_spc` (every 20 seconds)** — only runs when `partial_update_state`
is non-empty. It re-checks the affected days more frequently, attempting to
download the images until they are all non-placeholder. Once all images are
confirmed real, it posts and clears partial update state. If partial update state
persists beyond a timeout, the cog posts whatever it has and resets.

You can see which days are in partial update state via `/status`.

### SPC Day 4–8 Outlooks

Posted once daily when the SPC updates the Day 4–8 graphic. Uses HEAD-based
change detection on the static URL rather than HTML scraping.

### Mesoscale Discussions

The MD cog polls the SPC mesoscale discussion index every 60 seconds. It tracks
posted MD numbers in a persistent set and posts new ones as they appear. When an
MD is no longer listed on the index, it posts a cancellation embed.

### Watches

The watch cog runs every 2 minutes. It calls the NWS Alerts API as the primary
source. The return value has three distinct states:

- `None` — API call failed (HTTP error or bad JSON). The cycle is skipped
  entirely. `active_watches` is not modified, preventing false cancellations
  during a transient outage.
- `{}` — API succeeded and returned zero active watches. Normal processing
  continues — any watches in `active_watches` that are missing or expired will
  have cancellation embeds posted.
- `{...}` — one or more active watches. New ones are posted; expired or missing
  ones get cancellation embeds.

If the NWS API returns `None`, the `/watches` slash command falls back to
scraping the SPC watch index HTML directly.

### SCP Graphics

Posted at 6am and 6pm Pacific daily, but only if the images have actually
changed (hash-based detection). Uses `MODELS_CHANNEL_ID`.

### CSU-MLP and NCAR WxNext2

Both poll once daily around model update time. State is persisted to a JSON file
in `CACHE_DIR` so restarts don't cause duplicate posts.

---

## Watchdog

A `watchdog_task` loop runs every 2 minutes. It:

1. Probes the HTTP session with a lightweight request and recreates it if dead.
2. Checks every registered task. If a task has stopped, it restarts it.
3. After a threshold number of failures, posts a health alert embed to
   `SPC_CHANNEL_ID`. Watch and MD task failures are flagged as critical.

Tasks are registered with the watchdog in `main.py` after cogs load.

---

## Persistence

All persistent state lives in `CACHE_DIR` (default: `cache/`):

| File | Contents |
|---|---|
| `auto_posted_records.json` | Hashes of auto-posted images, used for change detection |
| `posted_records.json` | Hashes of manually-fetched images |
| `watch_posted.json` | Set of posted watch numbers (pruned to last 50) |
| `csu_mlp_posted.json` | Date and set of CSU-MLP days posted today |
| `ncar_posted.json` | Date and hash of last posted NCAR image |

All writes go through `atomic_json_dump` which writes to a temp file then renames,
preventing corruption on sudden shutdown.

---

## Running Tests

```bash
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

Tests use monkeypatched HTTP helpers and fixture payloads — no network access or
Discord connection required.
