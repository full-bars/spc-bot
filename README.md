# WxAlert / SPCBot

A Discord bot for severe weather enthusiasts. Auto-posts SPC convective outlooks, mesoscale discussions, and tornado/severe thunderstorm watches in real time. Includes a NEXRAD Level 2 radar downloader pulling from the NOAA AWS S3 archive with flexible time range selection.

## Features

- SPC convective outlooks (Day 1, 2, 3, Day 4-8) with dynamic URL resolution
- SPC mesoscale discussions with cancellation tracking
- Tornado and severe thunderstorm watch alerts via NWS API
- NIU/Gensini CFSv2/GEFS supercell composite parameter (SCP) graphics, twice daily
- NEXRAD Level 2 radar downloader from NOAA AWS S3
  - Single or multi-site downloads with per-site ZIP packaging
  - Z-to-Z range, start+duration, explicit datetime, or N most recent files

## Status

Work in progress. Actively developed in my free time, expect some bugs. 

## Built With

- [discord.py](https://github.com/Rapptz/discord.py)
- [aiohttp](https://github.com/aio-libs/aiohttp)
- [boto3](https://github.com/boto/boto3)
