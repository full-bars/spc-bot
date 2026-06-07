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


async def summarize_sounding_enhanced(
    raw_text: str,
    location_name: str = "Unknown",
    outlook_context: list[dict] = None,
    md_context: list[str] = None,
    watch_context: list[str] = None,
    sounding_context: str = None,
) -> str | None:
    """Generates a high-accuracy environmental analysis by cross-referencing
    computed parameters with SPC products and nearby sounding data."""

    context_str = ""
    if outlook_context:
        context_str += "\n### SPC DAY 1 OUTLOOK REGIONAL SUMMARIES:\n"
        for reg in outlook_context:
            context_str += f"- {reg.get('region')}: {reg.get('hazards_mode')}. {reg.get('favorable_factors')}\n"

    if md_context:
        context_str += "\n### ACTIVE NEARBY MESOSCALE DISCUSSIONS:\n"
        context_str += "\n".join(md_context) + "\n"

    if watch_context:
        context_str += "\n### ACTIVE NEARBY WATCHES:\n"
        context_str += "\n".join(watch_context) + "\n"

    if sounding_context:
        context_str += f"\n### NEARBY SOUNDING THERMODYNAMICS:\n{sounding_context}\n"

    prompt = (
        "You are a Senior Severe Storms Meteorologist. Analyze the provided local data "
        f"for {location_name} and cross-reference it with the latest SPC thinking.\n\n"
        "GOAL: Provide a highly accurate, 4-5 sentence plain-English environmental summary.\n"
        "PRIORITY: Identify the primary severe hazard and expected storm mode. "
        "DO NOT overstate hail risk if deep-layer shear is organized but storm mode is "
        "expected to be multicellular/MCS. Be skeptical of discrete supercell threats "
        "if SPC mentions linear transitions or cold-pool organization.\n\n"
        "STRUCTURE:\n"
        "1. Identify the primary hazard (Wind vs. Hail vs. Tornado) and peak magnitude.\n"
        "2. Identify the expected storm mode (Discrete Supercells, QLCS, Multicellular/MCS).\n"
        "3. Highlight limiting factors or 'fail modes' (e.g., capping, poor low-level spin).\n"
        "4. Note any discrepancies between local data and broad SPC products if relevant.\n\n"
        f"### LOCAL KINEMATIC/THERMODYNAMIC DATA:\n{raw_text}\n"
        f"{context_str}\n"
        "FINAL SUMMARY (Be concise, avoid raw numbers unless extreme):"
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
