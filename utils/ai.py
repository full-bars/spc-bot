# utils/ai.py
import json
import logging
from typing import Any
from config import AI_MODEL, GEMINI_API_KEY, OPENCODE_API_KEY
from utils.http import http_post_json

logger = logging.getLogger("spc_bot.ai")

_OPENCODE_BASE = "https://opencode.ai/zen/v1/chat/completions"


async def call_gemini(prompt: str, is_json: bool = False) -> Any | None:
    """Calls Gemini 3.1 Flash Lite via REST API to generate a text or JSON response."""
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is not set. Cannot call AI.")
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key={GEMINI_API_KEY}"

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


async def call_openai_compatible(
    prompt: str,
    is_json: bool = False,
    system_prompt: str = "You are an expert severe weather meteorologist.",
) -> Any | None:
    """Calls an OpenAI-compatible chat completions endpoint via OpenCode Zen."""
    if not OPENCODE_API_KEY:
        logger.warning("OPENCODE_API_KEY is not set. Cannot call AI.")
        return None

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    payload: dict[str, Any] = {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": 0.2,
    }

    if is_json:
        payload["response_format"] = {"type": "json_object"}

    headers = {"Authorization": f"Bearer {OPENCODE_API_KEY}"}
    response = await http_post_json(
        _OPENCODE_BASE,
        json_data=payload,
        retries=2,
        timeout=45,
        extra_headers=headers,
    )

    if not response:
        return None

    try:
        choices = response.get("choices", [])
        if not choices:
            logger.warning("OpenCode API returned no choices")
            return None

        content = choices[0].get("message", {}).get("content", "")
        if not content:
            return None

        if is_json:
            # Strip markdown code fences if present
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                if len(lines) >= 2:
                    content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse AI JSON: {e}\nTEXT: {content[:500]}")
                return None
        return content
    except Exception as e:
        logger.error(f"Failed to parse AI response: {e}")
        return None


async def call_ai(prompt: str, is_json: bool = False) -> Any | None:
    """Dispatches to OpenCode Zen (DeepSeek) first, falling back to Gemini."""
    if OPENCODE_API_KEY:
        result = await call_openai_compatible(prompt, is_json=is_json)
        if result is not None:
            return result
        logger.info("OpenCode AI call returned no result; falling back to Gemini")
    if GEMINI_API_KEY:
        return await call_gemini(prompt, is_json=is_json)
    logger.warning("No AI API key configured (set OPENCODE_API_KEY or GEMINI_API_KEY).")
    return None


async def summarize_md(raw_text: str) -> str | None:
    prompt = (
        "You are a meteorologist translating technical weather data for the general public. "
        "Read the following NWS Mesoscale Discussion. Provide a 2-3 sentence 'Bottom Line' summary. "
        "Strip out complex jargon (e.g., lapse rates, vorticity), and focus exclusively on: "
        "What is the threat, where is it located, and when is it happening.\n\n"
        f"TEXT:\n{raw_text}"
    )
    return await call_ai(prompt)


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
    return await call_ai(prompt, is_json=True)


async def summarize_sounding(raw_text: str) -> str | None:
    prompt = (
        "You are an expert severe weather meteorologist. I will provide you with computed "
        "thermodynamic and kinematic parameters from an atmospheric sounding (or hodograph). "
        "Provide a concise, 3-4 sentence plain-English summary of the environment. "
        "Focus on: 1) The primary severe hazard (hail, wind, tornadoes) supported by this environment, "
        "2) The expected storm mode (e.g., discrete supercells, squall line), and "
        "3) Any limiting factors (e.g., strong capping/CIN, poor low-level shear). "
        "Do not list out raw numbers unless they are exceptional. Make it readable for a general audience.\n\n"
        "PARAMETER SCALES FOR REFERENCE (WFO Louisville Severe Weather Forecasting):\n"
        "• CAPE (J/kg): <500 = weak, 500-1500 = moderate, 1500-3000 = strong, >3000 = extreme\n"
        "• 0-1km SRH (m²/s²): >150-300 = tornadic potential; >300-400 = strong rotating updrafts\n"
        "• 0-3km SRH (m²/s²): >150 = updraft rotation likely; >300-400 = supercell development likely\n"
        "• 0-6km Bulk Shear (kts): >40 = supercells; >52 = long-lived supercells\n"
        "• 0-3km CAPE (J/kg): >100-200 = low-level instability; critical for mini-supercells with weak deep shear\n"
        "• Lapse Rate (K/km): 700-500mb >7 = hail favorable; 0-3km >8 = steep boundary layer\n"
        "• LCL Pressure (hPa): >900 hPa (≈1000m AGL) = surface-based convection favorable\n"
        "• DCAPE (J/kg): >400 = strong downdraft; >800 = extreme/derecho-like wind potential\n\n"
        "CRITICAL FOR INCOMPLETE PROFILES (ACARS, limited-depth hodographs):\n"
        "• Even with shallow profile, assess ALL low-level indices together: 0-3km CAPE + 0-1km SRH + LCL pressure + lapse rates\n"
        "• WFO research shows mini-supercells with total CAPE ≤1000 J/kg and 0-3km CAPE of 100-200 J/kg can produce significant convection "
        "if 0-1km shear and low LCL compensate for weaker deep shear\n"
        "• High LCL pressure (>900 hPa) + steep lapse rate (>8 K/km) + any available SRH indicates surface-based tornado potential\n"
        "• Never conclude weak convection based solely on missing full-column CAPE; synthesize available layers\n\n"
        f"DATA:\n{raw_text}"
    )
    return await call_ai(prompt)


async def summarize_sounding_enhanced(
    raw_text: str,
    location_name: str = "Unknown",
    outlook_context: list[dict] | None = None,
    md_context: list[str] | None = None,
    watch_context: list[str] | None = None,
    sounding_context: str | None = None,
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
        "Carefully resolve any discrepancies between the local kinematic profile and "
        "the broader SPC discussion (e.g., if the local environment shows high shear "
        "supportive of supercells, but SPC discusses a transition to a linear/MCS mode "
        "due to cold-pool organization).\n\n"
        "PARAMETER SCALES FOR REFERENCE (WFO Louisville / SPC Operational Standards):\n"
        "• CAPE (J/kg): <500 = weak, 500-1500 = moderate, 1500-3000 = strong, >3000 = extreme\n"
        "• 0-1km SRH (m²/s²): >150-300 = tornadic potential; >300-400 = strong rotating updrafts\n"
        "• 0-3km SRH (m²/s²): >150 = updraft rotation likely; >300-400 = supercell development\n"
        "• 0-6km Bulk Shear (kts): 20-35 = organized multicells; >40 = supercells; >52 = long-lived supercells\n"
        "• 0-3km CAPE (J/kg): 100-200 = mini-supercells; >500 = strong low-level instability\n"
        "• Lapse Rates: 700-500mb >7 K/km = hail growth; 0-3km >8 K/km = steep boundary layer\n"
        "• LCL Pressure (hPa): >900 hPa (≈1000m AGL) = surface-based; <900 hPa = elevated/weak initiation\n"
        "• DCAPE (J/kg): 400-800 = strong downdraft; >800 = extreme (derecho-type wind)\n\n"
        "EVALUATING INCOMPLETE PROFILES (ACARS, limited-depth hodographs):\n"
        "• Assess ALL available signals: 0-3km CAPE + 0-1km SRH + lapse rate steepness (0-3km) + LCL pressure + CIN + 0-6km bulk shear\n"
        "• Mini-supercell research: even CAPE ≤1000 J/kg with 0-3km CAPE of 100-200 J/kg produces significant convection if "
        "0-1km SRH >150 m²/s² or LCL pressure >900 hPa (low LCL) + steep lapse rate (>8 K/km)\n"
        "• Do NOT rely on full-column CAPE being N/A to conclude weak environment: layer-by-layer assessment is critical\n"
        "• Tornado composite for shallow data: 0-1km bulk shear >20 kts + 0-1km SRH >150 m²/s² + LCL pressure >900 hPa + steep low-level lapse rate\n"
        "• Hail potential in shallow profile: steep mid-level lapse rate (700-500 >7 K/km) + 0-3km CAPE >200 J/kg + strong shear\n\n"
        "STRUCTURE:\n"
        "1. Identify primary hazard (Tornado vs. Hail vs. Wind) based on complete parameter synthesis.\n"
        "2. Identify storm mode (Discrete Supercell, Mini-supercell, QLCS, Multicellular).\n"
        "3. Highlight limiting factors or fail modes (CIN, poor shear distribution, weak instability layer).\n"
        "4. Note discrepancies between local sounding and SPC products if they exist.\n\n"
        f"### LOCAL KINEMATIC/THERMODYNAMIC DATA:\n{raw_text}\n"
        f"{context_str}\n"
        "FINAL SUMMARY (Be concise, avoid raw numbers unless extreme):"
    )
    return await call_ai(prompt)


async def generate_morning_briefing(outlook_text: str, active_watches_text: str) -> str | None:
    prompt = (
        "You are a friendly, professional severe weather briefer. Based on the provided SPC Day 1 Outlook "
        "text and the list of currently active watches, write a short and engaging morning severe weather briefing. "
        "Give an overarching view of today's highest threats.\n\n"
        f"ACTIVE WATCHES:\n{active_watches_text}\n\n"
        f"DAY 1 OUTLOOK TEXT:\n{outlook_text}"
    )
    return await call_ai(prompt)
