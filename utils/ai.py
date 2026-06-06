# utils/ai.py
import logging
from config import GEMINI_API_KEY
from utils.http import http_post_json

logger = logging.getLogger("spc_bot.ai")


async def call_gemini(prompt: str) -> str | None:
    """Calls Gemini 1.5 Flash via REST API to generate a text response."""
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is not set. Cannot call AI.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
        },
    }

    response = await http_post_json(url, json_data=payload, retries=2, timeout=20)

    if not response:
        return None

    try:
        candidates = response.get("candidates", [])
        if not candidates:
            return None

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return None

        return parts[0].get("text")
    except Exception as e:
        logger.error(f"Failed to parse Gemini response: {e}")
        return None


async def summarize_md(raw_text: str) -> str | None:
    prompt = (
        "You are a meteorologist translating technical weather data for the general public. "
        "Read the following NWS Mesoscale Discussion. Provide a 2-3 sentence 'Bottom Line' summary. "
        "Strip out complex jargon (e.g., lapse rates, vorticity), and focus exclusively on: "
        "What is the threat, where is it located, and when is it happening.\n\n"
        f"TEXT:\n{raw_text}"
    )
    return await call_gemini(prompt)


async def summarize_outlook(raw_text: str) -> str | None:
    prompt = (
        "You are an expert severe weather meteorologist. Analyze the following Storm Prediction Center "
        "(SPC) Convective Outlook text and provide a concise, readable summary. Break your analysis into "
        "exactly five bulleted sections:\n"
        "1. **Favorable Factors**: What dynamics and thermodynamics are supporting severe hazards (tornadoes, hail, wind)?\n"
        "2. **Fail Modes**: What are the limiting factors or uncertainties that could prevent these hazards from developing?\n"
        "3. **Primary Hazards & Storm Mode**: Are we expecting discrete supercells or a squall line (QLCS)? What is the peak hazard magnitude?\n"
        "4. **Timing**: When is initiation expected, and what is the window of peak activity?\n"
        "5. **Geographic Focus & Confidence**: What specific regions/cities are at greatest risk, and how confident is the SPC in this scenario?\n\n"
        f"TEXT:\n{raw_text}"
    )
    return await call_gemini(prompt)


async def generate_morning_briefing(outlook_text: str, active_watches_text: str) -> str | None:
    prompt = (
        "You are a friendly, professional severe weather briefer. Based on the provided SPC Day 1 Outlook "
        "text and the list of currently active watches, write a short and engaging morning severe weather briefing. "
        "Give an overarching view of today's highest threats.\n\n"
        f"ACTIVE WATCHES:\n{active_watches_text}\n\n"
        f"DAY 1 OUTLOOK TEXT:\n{outlook_text}"
    )
    return await call_gemini(prompt)
