This release introduces powerful new on-demand AI analysis tools built natively into the bot, alongside an infrastructure update that separates Damage Assessment Tool (DAT) and Public Information Statements (PNS) into their own dedicated Discord channel for cleaner routing.

🤖 **On-Demand AI Weather Analysis**
*   **Outlook Analysis:** A new "🪄 AI Analysis" button has been added to Convective Outlook embeds (Days 1, 2, 3, and 4-8) and `/spc` slash commands. When clicked, the bot fetches the raw SPC text and generates a structured summary covering Favorable Factors, Fail Modes, Primary Hazards & Storm Mode, Timing, and Geographic Focus.
*   **Mesoscale Discussions:** MDs and `/md` paginators now feature a "🪄 TL;DR" button that provides an instant, jargon-free summary of the discussion's key points.
*   **Morning Briefing Command:** A new `/dailybriefing` slash command analyzes the latest Day 1 Outlook text alongside any currently active watches to generate a cohesive morning severe weather briefing.
*   **Stateful Resilience:** AI interaction buttons are fully persistent, ensuring analysis functions correctly across bot restarts.

📡 **Dedicated Survey Routing**
*   **PNS & DAT Channel:** Added a new `SURVEYS_CHANNEL_ID` configuration option. Public Information Statements and Damage Assessment Tool alerts are now automatically routed here, decluttering the main warning channels.

⚡ **Under the Hood**
*   **Zero-Bloat AI Client:** Built a custom asynchronous REST client for the Gemini API using the bot's existing `aiohttp` session, completely avoiding the need to install heavy external SDK dependencies.
*   **Token Efficiency & Recovery:** Implemented persistent Redis caching for all AI summaries. If multiple users request a summary, or if the bot restarts, the cached response is served instantly to minimize API calls.

📈 **Observability**
*   **AI Metrics Dashboard:** The `/status` command now displays Gemini endpoint connectivity, daily requests, and cache hit rates to monitor API usage.

--------
## What's Changed
• feat: Add dedicated SURVEYS_CHANNEL_ID for PNS and DAT routing by @full-bars in https://github.com/full-bars/spc-bot/pull/493
• feat: Gemini AI Summaries & Briefings by @full-bars in https://github.com/full-bars/spc-bot/pull/494

**Full Changelog**: https://github.com/full-bars/spc-bot/compare/v5.35.0...v5.36.2
