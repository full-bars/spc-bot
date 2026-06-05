# Alerting Authority & NWWS-OI 📡

SPCBot's primary mission is delivering severe weather information with the lowest possible latency. To achieve this, it utilizes a tiered hierarchy of data sources.

## 🥇 Gold Standard: NWWS-OI (XMPP)

The **National Weather Service Weather Wire Service Open Interface** is a satellite-sourced XMPP push feed.
- **Latency:** Near-zero. Products often arrive in Discord before they appear on the NWS API or IEM.
- **Reliability:** By connecting directly to `nwws-oi.weather.gov`, the bot receives raw text products as they are issued.
- **Usage:** Primarily used for `TOR`, `SVR`, and `FFW` warnings, as well as `SVS` updates and `PNS` damage surveys.

## 🥈 Silver Standard: IEMBot (XMPP/JSON)

The **Iowa Environmental Mesonet (IEM)** provides a robust real-time feed of NWS products via their `iembot` service.
- **Latency:** Low (1–5s).
- **Fallback:** Acts as a fast source for Mesoscale Discussions (MDs), watches, and warning products if NWWS-OI is unreachable.
- **Formatting:** SPCBot leverages IEM's pre-parsed product text and autoplot maps for warnings.

## 🥉 Bronze Standard: NWS API & SPC Polling

Traditional polling via the NWS API (`api.weather.gov`) and `spc.noaa.gov`.
- **Latency:** Moderate (30–60s+).
- **Role:** Used for "Aggressive Checking" during Moderate/High risk days and for rehydrating state at startup.
- **Persistence:** ETag and Last-Modified headers are used to minimize bandwidth and detect updates efficiently.

## 📢 Per-Type Warning Channels

Warning products can be routed to dedicated Discord channels based on event type, rather than a single shared channel.

### Channel Environment Variables

| Variable | Warning Type |
|---|---|
| `TOR_CHANNEL_ID` | Tornado Warnings |
| `SVR_CHANNEL_ID` | Severe Thunderstorm Warnings |
| `FFW_CHANNEL_ID` | Flash Flood Warnings |
| `SPS_CHANNEL_ID` | Special Weather Statements |
| `SURVEYS_CHANNEL_ID` | Damage Survey PNS and DAT toolkit plots |

### Routing Fallback Chain

For each warning type, the bot resolves the destination channel in this order:
1. Per-type channel (e.g., `TOR_CHANNEL_ID`) — if set and enabled
2. `WARNINGS_CHANNEL_ID` — general warnings channel
3. `SPC_CHANNEL_ID` — catch-all SPC channel

### Runtime Configuration

Per-type channel routing can be adjusted at runtime without a restart:

| Command | Description |
|---|---|
| `/enablewarnings` | Enable warning posting for the current channel (or a specified channel). Setting persists to the state store. |
| `/disablewarnings` | Disable warning posting for the configured channel. |
| `/displaysetup` | Show the current per-type channel routing configuration, including which env vars are active and which channels are enabled. |

## 🛡️ Circuit Breakers & Resilience

SPCBot implements **Circuit Breakers** for all upstream HTTP endpoints.
- If an endpoint (e.g., SPC website) fails multiple times, the bot "opens the circuit," stopping requests for a cooldown period to prevent loop starvation.
- **Graceful Degradation:** If the SPC website is down, the bot will attempt to serve cached MD images or fall back to text-only summaries from IEM.
- **Watchdog:** A background loop probes `api.weather.gov` and `mesonet.agron.iastate.edu`; it alerts administrators and recreates the aiohttp session only when both independent HTTP paths fail repeatedly.
