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
- **Fallback:** Acts as the primary source for Mesoscale Discussions (MDs) and a redundant path for warnings if NWWS-OI is unreachable.
- **Formatting:** SPCBot leverages IEM's pre-parsed product text and autoplot maps for warnings.

## 🥉 Bronze Standard: NWS API & SPC Polling

Traditional polling via the NWS API (`api.weather.gov`) and `spc.noaa.gov`.
- **Latency:** Moderate (30–60s+).
- **Role:** Used for "Aggressive Checking" during Moderate/High risk days and for rehydrating state at startup.
- **Persistence:** ETag and Last-Modified headers are used to minimize bandwidth and detect updates efficiently.

## 🛡️ Circuit Breakers & Resilience

SPCBot implements **Circuit Breakers** for all upstream HTTP endpoints.
- If an endpoint (e.g., SPC website) fails multiple times, the bot "opens the circuit," stopping requests for a cooldown period to prevent loop starvation.
- **Graceful Degradation:** If the SPC website is down, the bot will attempt to serve cached MD images or fall back to text-only summaries from IEM.
- **Watchdog:** A background loop monitors feed health and proactively alerts administrators if both NWWS and IEM paths are down.
