# Warning Lifecycle & Updates 🌪️

SPCBot provides more than just initial alerts; it tracks the entire lifecycle of NWS warnings in real time, from issuance to cancellation.

## 🔬 VTEC Parsing

The bot's intelligence is rooted in **VTEC (Valid Time Event Code)** parsing. Every NWS product contains a VTEC string (e.g., `/O.NEW.KOUN.TO.W.0042.../`) that identifies:
- **Action:** `NEW` (Issuance), `CON` (Continue), `EXT` (Extend), `CAN` (Cancel), `EXP` (Expire).
- **Office:** Issuing WFO (e.g., `KOUN`).
- **Phenomenon & Significance:** `TO.W` (Tornado Warning), `SV.W` (Severe Thunderstorm Warning).
- **ETN:** Event Tracking Number—a unique, stable identifier for that specific storm's lifecycle.

## 🚀 The Low-Latency Pipeline

To ensure sub-10 second delivery, SPCBot uses a dual-path pipeline:
1. **Fast-Trigger (NWWS-OI/IEMBot):** As soon as a raw text product is received, the bot parses the VTEC and posts a "NEW" issuance immediately.
2. **Polling Fallback (NWS API):** The bot polls the NWS API every 30 seconds to catch any products missed by the push feeds and to rehydrate state after a restart.

## 🔄 Real-Time Lifecycle Updates

Unlike basic bots, SPCBot updates its posts as the storm evolves:
- **Continuing Updates (`CON`):** When a warning is continued, the bot posts a concise update note.
- **County-Level Precision:** The bot calculates which counties have been **cancelled** and which are **continuing** in each update (e.g., `(cancels Clarke, continues Jones [MS])`).
- **Severity Tags:** Automatically detects and highlights PDS (Particularly Dangerous Situation), Tornado Emergencies, and Destructive Severe Thunderstorm tags.

## ⏹️ Graceful Cancellations

When a warning expires or is cancelled:
- The bot posts a dedicated **Cancellation Notice**.
- The notice includes a relative timestamp (e.g., `(cancelled 2 minutes ago)`).
- The original issuance post remains as a historical record, but the new notice signals the end of the threat to users.

## 🗺️ Automated Mapping

Every warning post includes an **IEM Autoplot 208** map showing the warning polygon, affected counties, and a storm-relative radar snapshot.
