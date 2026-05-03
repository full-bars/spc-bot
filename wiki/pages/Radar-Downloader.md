# Radar Downloader 📡

SPCBot includes a high-performance downloader for raw **NEXRAD Level 2** radar data, leveraging the NOAA AWS S3 archive.

## 📥 Quick Start (`/download`)

The `/download` command allows users to retrieve raw radar data for post-event analysis.
- **Site Selection:** Supports all 160+ NEXRAD sites (e.g., `KTLX`, `KOKX`).
- **Time Filtering:** Retrieve data by specific datetime, time range, or the "N most recent" files.
- **Z-to-Z Range:** Supports shorthand for Z-time ranges (e.g., `start: 2200, end: 0030`).

## 📦 Multi-Site & Zipping

- **ZIP Output:** For multi-file requests, the bot automatically zips the data before uploading it to Discord, respecting file size limits.
- **Concurrent Downloads:** Utilizes `aioboto3` for non-blocking, parallel S3 downloads, significantly reducing wait times for large datasets.

## ☁️ S3 Integration

- **Backend:** Pulls directly from the `noaa-nexrad-level2` bucket on AWS S3.
- **Metadata Search:** The bot performs real-time S3 prefix listing to find the exact filenames and timestamps required, ensuring accuracy even across UTC day boundaries.
- **Efficiency:** Downloads are streamed directly to a temporary buffer and then zipped, minimizing disk I/O.

## 🔧 Operator Controls

- **Size Limits:** Operators can configure the maximum number of files per request in `.env` to prevent resource exhaustion.
- **Permissioning:** Access to the downloader can be restricted to specific roles or channels via Discord's native integration settings.
