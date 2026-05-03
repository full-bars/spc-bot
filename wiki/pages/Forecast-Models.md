# Forecast Models 🧪

In addition to observed data, SPCBot monitors several experimental and operational forecast models to provide a look-ahead at severe weather threats.

## 🤖 CSU-MLP (Machine Learning Probabilities)

The **Colorado State University Machine Learning Probabilities** model provides daily severe weather forecasts for Days 1–8.
- **Automated Posting:** The bot polls for new CSU-MLP runs daily and automatically posts the consolidated 6-panel summaries.
- **On-Demand (`/csu`):** Users can use the `/csu` command to retrieve specific products (Individual Hazards, Significant Severe, etc.) via an interactive dropdown.
- **Source:** Pulls from the official CSU MLP image archive.

## 🧠 NCAR WxNext2 (AI Convective Hazard)

**WxNext2** is an AI-driven convective hazard forecast developed by NCAR.
- **Automated Posting:** Posted daily for Days 1–8.
- **On-Demand (`/wxnext`):** Retrieves the latest AI hazard maps, which provide an alternative to traditional SPC categorical outlooks.

## 🗺️ NIU/Gensini SCP Graphics

The **Supercell Composite Parameter (SCP)** maps are generated twice daily based on CFSv2 and GEFS data.
- **Frequency:** Automated posts occur shortly after the 00z and 12z model cycles.
- **On-Demand (`/scp`):** Retrieves the latest NIU SCP graphics.
- **Context:** These maps are particularly useful for long-range (1–2 week) severe weather pattern recognition.

## 🌧️ WPC Rainfall Outlooks

The **Weather Prediction Center (WPC)** Excessive Rainfall Outlooks (ERO) for Days 1–3.
- **On-Demand (`/wpc`):** Retrieves the latest flash flood probability graphics.
- **Automated Monitoring:** The bot tracks ERO updates and can be configured to post High-Risk ERO events automatically.
