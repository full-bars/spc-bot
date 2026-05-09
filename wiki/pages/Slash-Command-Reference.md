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
| `/ww` | Alias for `/watches`. | None |
| `/recenttornadoes` | List the most recent confirmed tornado events. | None |
| `/sigtor` | Show significant (EF2+) tornado events from the archive. | None |
| `/archive` | Search the tornado environmental forensics archive (GIFs + peak SRH). | `radar`, `date` |

## 📊 Analysis & Analytics
| Command | Description | Parameters |
|---|---|---|
| `/sounding` | Plot observed RAOB/ACARS soundings. | `location`, `time`, `dark` |
| `/hodograph` | Generate VWP hodographs for NEXRAD/TDWR sites. | `site` |
| `/verify` | View WFO warning verification metrics (IEM Cow). | `wfo`, `days`, `phenomena` |
| `/riskmap` | Generate a historical SPC Day 1 categorical risk-frequency map. | `threshold`, `state`, `years` |
| `/topstats` | View tornado warning/report leaderboards for WFOs or states. | `by`, `year`, `source` |
| `/dayssince` | IEM map showing days since the last Tornado Warning. | `wfo`, `state` accepted but not yet applied |
| `/dailyrecap` | Summary of all warning polygons for a specific day. | `date` |
| `/tornadoheatmap` | Density map of recent tornado reports. | `days`, `state` |

Some analytics commands expose experimental IEM Autoplot workflows and are still being hardened for production use.

## 🧪 Models & System
| Command | Description | Parameters |
|---|---|---|
| `/csu` | CSU-MLP Machine Learning forecasts. | `product` |
| `/wxnext` | NCAR WxNext2 AI convective hazard forecasts. | None |
| `/scp` | NIU/Gensini Supercell Composite Parameter maps. | `fresh` |
| `/wpc` | WPC Excessive Rainfall Outlooks. | None |
| `/download` | Request raw Level 2 Radar data from NOAA S3. | `sites`, `time`, `count` |
| `/downloaderstatus` | Check Discord gateway and AWS S3 downloader latency. | None |
| `/status` | Real-time operational dashboard with system health, network connectivity (NWWS/IEM pings in ms), alert delay tracking, NWWS throughput, Discord gateway location, and environment state. Auto-refreshes every 5 seconds for 5 minutes. | None |
| `/taskmgr` | Live-updating task manager (htop-style) showing background loop status and iteration timers. Auto-refreshes every 5 seconds for 10 minutes. Owner-only. | None |
| `/logs` | Virtual terminal viewer for live-streaming console output with ANSI color support. Auto-refreshes every 5 seconds for 5 minutes. Owner-only. | None |
| `/failover` | Open an interactive failover manager. Requires `ADMIN_USER_ID`; lets an operator designate a primary node or clear the manual override. | None |
