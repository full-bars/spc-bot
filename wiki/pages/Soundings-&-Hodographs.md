# Soundings & Hodographs 📈

SPCBot provides automated and on-demand tools for atmospheric analysis, focusing on observed data from the RAOB and VWP networks.

## 🎈 Observed Soundings (`/sounding`)

The `/sounding` command plots RAOB (weather balloon) and ACARS (aircraft) data using **SounderPy**.
- **Location Support:** Accepts 3-letter site IDs (e.g., `OUN`), 4-letter ICAO codes (`KOUN`), or city names (`Norman, OK`).
- **Data Sources:** Automatically tries **IEM**, **Wyoming**, and **GSL** (FSL) in a prioritized hierarchy with circuit-breaker logic.
- **Interactive UI:** Users can select specific times (e.g., `00z`, `12z`) or recent special releases (e.g., `18z`, `20z`) via a dropdown menu.
- **Performance:** The initial "Checking Station Availability..." step is optimized with concurrent request limiting, reducing lookup time from 10–30s to 1–3s.
- **International Stations:** For stations not listed in the IEM station inventory (e.g., `SBSM`/Santa Maria), availability is confirmed via a Wyoming archive fallback probe, ensuring non-CONUS stations show correctly.
- **Dark Mode:** Toggle between light and dark plot themes using the **Switch to Dark/Light Mode** button (preserves station data on mode change).

### Queue Management & Caching

Sounding renders run in a dedicated **Heavy Sounding** executor with 4 concurrent worker processes, isolated from hodograph requests so that a slow or queued sounding plot never delays a hodograph update.

- **Queue position feedback:** When all sounding workers are busy, the bot replies with "Plot Queued (Position X)…" and updates the message as the request advances through the queue. An `asyncio.Semaphore` controls concurrency.
- **Disk caching with hash dedup:** Rendered sounding images are cached to disk keyed by a hash of location + time + theme. Repeated requests for the same combination return the cached image immediately without re-rendering.

## 🌪️ Watch-Triggered Soundings

The bot proactively monitors severe weather and automatically posts soundings:
- **Issuance Trigger:** When an SPC Tornado or Severe Tstorm watch is issued, the bot immediately fetches and posts the nearest observed sounding.
- **Synoptic Cycles:** During active watches, the bot automatically posts the 00z and 12z soundings for all stations near the watch area.
- **Risk Sweep:** On **Moderate** or **High Risk** Day 1 outlooks, the bot sweeps RAOB stations and ACARS airports inside the active MDT/HIGH polygon as new data arrives.

## 🌀 VWP Hodographs (`/hodograph`)

The `/hodograph` command generates a Vertical Wind Profile (VWP) hodograph for any of 200+ NEXRAD or TDWR radar sites.
- **High-Availability:** Uses **AWS S3** (`unidata-nexrad-level3`) as a primary data source with automatic fallback to **TGFTP**, ensuring reliability during NWS server outages.
- **S3 Reliability:** The S3 engine supports Gzip and Zlib decompression and searches back 3 days to handle mirror data gaps.
- **Rust Acceleration:** Real-time S3 VAD fetching is offloaded to the Rust core using a pooled `reqwest` client, reducing S3 fetch latency from ~750ms to ~250ms.
- **Real-time Surface Wind:** Automatically fetches the latest ASOS surface observation near the radar to provide an accurate surface-to-1km profile.
- **Parameter Table:** Includes a comprehensive storm-parameter table (Bunkers motion, SRH, Shear) rendered alongside the plot.
- **Performance:** Hodograph storm-parameter calculations (Bunkers displacement, Storm-Relative Helicity, Critical Angle) are accelerated using Rust with Python fallback, reducing computation time for large profiles. Hodographs run in a dedicated **Fast Hodo** executor and are never blocked by concurrent sounding renders.

## 🎥 VAD Forensics

Introduced in **v5.14.0**, the bot automatically records the wind environment during confirmed tornado events.
- **Trigger:** Any Tornado Warning with an `OBSERVED` tag starts a **2.5-hour mission**.
- **Evolution GIFs:** Captures a 1-hour lookback and a 90-minute follow-up window, stitching them into an animated evolution of the vertical wind profile.
- **Permanent Record:** Calculates the **Peak 0-1km SRH** during the event and archives it in `events.db` along with the GIF.

## ⚙️ Rust-Accelerated VAD & S3 (Phase 9)

Key VAD math routines and data fetching are now backed by compiled Rust via try-Rust → fallback-to-Python wrappers:
- `fetch_s3_vad_fast` — High-speed pooled S3 listing and object retrieval (via `reqwest`)
- `compute_shear_mag` — bulk shear magnitude between two pressure levels
- `compute_sr_flow` — storm-relative flow vectors
- `clip_profile` — profile clipping to a depth layer

## 🔬 Scientific Stack

These tools rely on a Debian-based scientific Python stack included in the SPCBot Docker image:
- **MetPy:** Atmospheric calculations and skew-T rendering.
- **SounderPy:** Vertical profile data retrieval.
- **Matplotlib & Cartopy:** High-quality scientific visualization.
