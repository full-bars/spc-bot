# utils/ai.py
import json
import logging
from typing import Any
from config import GEMINI_API_KEY
from utils.http import http_post_json

logger = logging.getLogger("spc_bot.ai")


async def call_gemini(prompt: str, is_json: bool = False) -> Any | None:
    """Calls Gemini 1.5 Flash via REST API to generate a text or JSON response."""
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is not set. Cannot call AI.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"

    generation_config: dict[str, Any] = {
        "temperature": 0.2,
    }

    if is_json:
        generation_config["response_mime_type"] = "application/json"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }

    response = await http_post_json(url, json_data=payload, retries=2, timeout=25)

    if not response:
        return None

    try:
        candidates = response.get("candidates", [])
        if not candidates:
            return None

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            return None

        text = parts[0].get("text")
        if not text:
            return None

        if is_json:
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse Gemini JSON: {e}\nTEXT: {text[:500]}")
                return None
        return text
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


async def summarize_outlook(raw_text: str) -> list[dict] | None:
    prompt = (
        "You are an expert severe weather meteorologist. Analyze the following Storm Prediction Center "
        "(SPC) Convective Outlook text and identify distinct geographic risk areas mentioned. "
        "For EACH area, provide a concise, readable summary. "
        "Return the result ONLY as a JSON array of objects with these keys: "
        "'region', 'favorable_factors', 'fail_modes', 'hazards_mode', 'timing', 'confidence'.\n\n"
        "1. favorable_factors: What dynamics/thermodynamics support severe hazards?\n"
        "2. fail_modes: What limiting factors or uncertainties could prevent hazards?\n"
        "3. hazards_mode: Expected storm mode (discrete, QLCS) and peak hazard magnitude?\n"
        "4. timing: Initiation window and peak activity window?\n"
        "5. confidence: SPC confidence level and specific focus cities.\n\n"
        f"TEXT:\n{raw_text}"
    )
    return await call_gemini(prompt, is_json=True)


async def summarize_sounding(raw_text: str) -> str | None:
    prompt = (
        "You are an expert severe weather meteorologist. I will provide you with computed "
        "thermodynamic and kinematic parameters from an atmospheric sounding (or hodograph). "
        "Provide a concise, 3-4 sentence plain-English summary of the environment. "
        "Focus on: 1) The primary severe hazard (hail, wind, tornadoes) supported by this environment, "
        "2) The expected storm mode (e.g., discrete supercells, squall line), and "
        "3) Any limiting factors (e.g., strong capping/CIN, poor low-level shear). "
        "Do not list out raw numbers unless they are exceptional. Make it readable for a general audience.\n\n"
        f"DATA:\n{raw_text}"
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
