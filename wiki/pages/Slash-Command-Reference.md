# Slash Command Reference ⌨️

SPCBot uses Discord Slash Commands for all user interactions. Commands are organized into logical groups.

## 📅 Outlooks & Discussions
| Command | Description | Parameters |
|---|---|---|
| `/spc1` | Get latest Day 1 Convective Outlook images. | `fresh`: Bypass cache |
| `/spc2` | Get latest Day 2 Convective Outlook images. | `fresh`: Bypass cache |
| `/spc3` | Get latest Day 3 Convective Outlook images. | `fresh`: Bypass cache |
| `/spc48` | Get Day 4-8 Convective Probability images. | None |
| `/md` | Show a paginated view of all active Mesoscale Discussions. | None |

## 🚨 Watches & Tornadoes
| Command | Description | Parameters |
|---|---|---|
| `/watches` | List all currently active SPC Tornado and Severe Tstorm watches. | None |
| `/recenttornadoes` | List the most recent confirmed tornado events. | None |
| `/sigtor` | Show significant (EF2+) tornado events from the archive. | None |

## 📊 Analysis & Analytics
| Command | Description | Parameters |
|---|---|---|
| `/sounding` | Plot observed RAOB/ACARS soundings. | `loc`, `time` (optional) |
| `/hodograph` | Generate VWP hodographs for NEXRAD/TDWR sites. | `site` |
| `/verify` | View WFO warning verification metrics (IEM Cow). | `wfo`, `days` |
| `/riskmap` | Generate a heatmap of historical SPC risk categories. | `days` |
| `/topstats` | View leaderboards for WFOs/States. | `by`, `year` |
| `/dayssince` | Check the current streak since the last Tornado Warning. | None |
| `/dailyrecap` | Summary of all warning polygons for a specific day. | `date` |
| `/tornadoheatmap` | Global density map of recent tornado reports. | `days` |

## 🧪 Models & System
| Command | Description | Parameters |
|---|---|---|
| `/csu` | CSU-MLP Machine Learning forecasts. | `product` |
| `/wxnext` | NCAR WxNext2 AI convective hazard forecasts. | None |
| `/scp` | NIU/Gensini Supercell Composite Parameter maps. | `fresh` |
| `/wpc` | WPC Excessive Rainfall Outlooks. | None |
| `/download` | Request raw Level 2 Radar data from NOAA S3. | `site`, `time`, `count` |
| `/status` | Real-time operational dashboard with system health, network connectivity (NWWS/IEM pings in ms), alert delay tracking, and environment state. Auto-refreshes every 5 seconds for 5 minutes. | None |
| `/taskmgr` | Live-updating task manager (htop-style) showing background loop status and iteration timers. Auto-refreshes every 5 seconds for 10 minutes. Owner-only. | None |
| `/logs` | Virtual terminal viewer for live-streaming console output with ANSI color support. Auto-refreshes every 5 seconds for 5 minutes. Owner-only. | None |
| `/failover` | Manually trigger a node role swap (Owner-only). | `force_hostname` |
