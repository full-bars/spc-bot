# Privacy Policy for SPCBot

**Last Updated:** June 14, 2026

Thank you for using SPCBot (the "Bot"). This Privacy Policy explains how we collect, use, and protect information when you add SPCBot to your Discord server or interact with its features. 

SPCBot is a high-performance severe weather monitoring platform designed to provide near-zero latency weather alerts, scientific analyses, and weather product summaries directly to your Discord server.

## 1. Information We Collect

To function properly and provide its services, SPCBot may collect and store the following information:

*   **Server Information:** Discord Server (Guild) IDs.
*   **Channel Information:** Specific Discord Channel IDs configured by server administrators to receive weather updates (e.g., general SPC products, warning alerts, modeling data).
*   **User Information:** Discord User IDs for specific users designated as Bot administrators (e.g., `ADMIN_USER_ID`) for authorization and access control to sensitive commands (like `/failover` or `/taskmgr`).
*   **Command Usage Data:** Information regarding the usage of slash commands (e.g., `/historical`, `/compare`) for operational logging, troubleshooting, and performance improvements.
*   **Message Content (Limited):** The bot does *not* read or store general conversational messages from users. It only processes messages specifically directed at it via slash commands or configured operational channels when necessary for bot functionality.

## 2. How We Use Your Information

The collected data is strictly used to operate, maintain, and improve SPCBot:

*   **Routing Alerts:** Guild and Channel IDs are necessary to route NOAA, NWS, and SPC weather products to the correct destinations within your server.
*   **Access Control:** User IDs ensure that only authorized personnel can execute administrative commands.
*   **System Stability:** Command usage data helps diagnose issues, manage High-Availability failovers, and monitor system performance.
*   **AI Summarization:** SPCBot utilizes Google Gemini AI to provide context-aware environmental synthesis and summaries of weather products. When these features are triggered, relevant meteorological data (not personal user data) is sent to the Gemini API for processing.

## 3. Data Sharing and Third-Party Services

We value your privacy. **We do not sell, rent, or distribute your personal data to third parties for marketing or advertising purposes.** 

However, SPCBot interacts with the following external services to provide its functionality:
*   **Discord API:** To receive commands and send messages/alerts.
*   **NOAA / NWS APIs / NWWS-OI:** To fetch public meteorological data and alerts. 
*   **Google Gemini API:** To generate automated, AI-powered weather summaries. (Note: Only weather-related context is sent; no PII is transmitted).

## 4. Data Retention and Security

*   **Storage:** Configuration data (Guild IDs, Channel IDs, Admin User IDs) is stored locally on the host server where the bot is running (often in environment variables, configuration files, or local SQLite databases).
*   **Retention:** Operational data is kept as long as the bot remains active in your server. Log files (which may contain command usage traces) are periodically rotated and purged.
*   **Security:** Access to the bot's host server and databases is restricted to the bot operator. We employ local Redis, Tailscale (in high-availability setups), and file system permissions to protect operational state.

## 5. Your Rights and Choices

*   **Removal:** Server administrators can remove SPCBot from their Discord server at any time. Upon removal, the bot will cease sending alerts to that server.
*   **Data Deletion:** If you wish to have your server's configuration data explicitly removed from the host's configuration files and databases, you may contact the bot operator.

## 6. Disclaimer

SPCBot is designed for situational awareness and should **not** be used as a single point of failure for life-safety alerts. Please ensure you have multiple redundant methods for receiving severe weather warnings (e.g., NOAA Weather Radio, WEA alerts on mobile devices).

## 7. Contact Us

If you have any questions or concerns about this Privacy Policy or how SPCBot handles data, please open an issue on the [SPCBot GitHub Repository](https://github.com/full-bars/spc-bot).
