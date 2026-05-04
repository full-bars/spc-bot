# Soundings & Hodographs 📈

SPCBot provides automated and on-demand tools for atmospheric analysis, focusing on observed data from the RAOB and VWP networks.

## 🎈 Observed Soundings (`/sounding`)

The `/sounding` command plots RAOB (weather balloon) and ACARS (aircraft) data using **SounderPy**.
- **Location Support:** Accepts 3-letter site IDs (e.g., `OUN`), 4-letter ICAO codes (`KOUN`), or city names (`Norman, OK`).
- **Data Sources:** Automatically tries **IEM**, **Wyoming**, and **GSL** (FSL) in a prioritized hierarchy with circuit-breaker logic.
- **Interactive UI:** Users can select specific times (e.g., `00z`, `12z`) or recent special releases (e.g., `18z`, `20z`) via a dropdown menu.

## 🌪️ Watch-Triggered Soundings

The bot proactively monitors severe weather and automatically posts soundings:
- **Issuance Trigger:** When an SPC Tornado or Severe Tstorm watch is issued, the bot immediately fetches and posts the nearest observed sounding.
- **Synoptic Cycles:** During active watches, the bot automatically posts the 00z and 12z soundings for all stations near the watch area.
- **Risk Sweep:** On **Moderate** or **High Risk** days, the bot sweeps every RAOB and ACARS airport within 100km of the high-risk polygon as new data arrives.

## 🌀 VWP Hodographs (`/hodograph`)

The `/hodograph` command generates a Vertical Wind Profile (VWP) hodograph for any of 200+ NEXRAD or TDWR radar sites.
- **High-Availability:** Uses **AWS S3** as a primary data source with automatic fallback to **TGFTP**, ensuring reliability during NWS server outages.
- **Real-time Surface Wind:** Automatically fetches the latest ASOS surface observation near the radar to provide an accurate surface-to-1km profile.
- **Parameter Table:** Includes a comprehensive storm-parameter table (Bunkers motion, SRH, Shear) rendered alongside the plot.

## 🎥 VAD Forensics

Introduced in **v5.14.0**, the bot automatically records the wind environment during confirmed tornado events.
- **Trigger:** Any Tornado Warning with an `OBSERVED` tag starts a **2.5-hour mission**.
- **Evolution GIFs:** Captures a 1-hour lookback and a 90-minute follow-up window, stitching them into an animated evolution of the vertical wind profile.
- **Permanent Record:** Calculates the **Peak 0-1km SRH** during the event and archives it in `events.db` along with the GIF.

## 🔬 Scientific Stack

These tools rely on a Debian-based scientific Python stack included in the SPCBot Docker image:
- **MetPy:** Atmospheric calculations and skew-T rendering.
- **SounderPy:** Vertical profile data retrieval.
- **Matplotlib & Cartopy:** High-quality scientific visualization.
