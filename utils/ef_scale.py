"""Enhanced Fujita scale damage-indicator data access.

Exposes the 28 EF-scale damage indicators (DI) and their degrees of damage
(DoD), each with lower-bound (LB), expected (EXP), and upper-bound (UB)
3-second-gust wind speed estimates (mph) and the EF rating each bound maps
to.

Data snapshot: ``config/ef_scale.json`` (retrieved 2026-08-13) from
Wikipedia, "Enhanced Fujita scale"
(https://en.wikipedia.org/wiki/Enhanced_Fujita_scale), "Damage indicators
and degrees of damage" table, CC BY-SA 4.0 (attribution in CREDITS.md). The
table derives from Texas Tech University's "A recommendation for an Enhanced
Fujita scale" (McDonald & Mehta, 2006), the basis for NWS operational EF
rating.

Wind estimates are expressed as LB/EXP/UB because the EF scale is a damage
scale: EXP is the wind speed expected to produce the damage for typical
construction; LB/UB bracket worse/better-than-typical construction. A small
number of DoDs carry no wind estimate at all (``mph``/``ef`` are ``None``) —
those rows are rated EFU ("unknown") on Wikipedia.
"""

from __future__ import annotations

import json
import os
from typing import TypedDict


class BoundEstimate(TypedDict):
    mph: int | None
    ef: str | None


class DegreeOfDamage(TypedDict):
    dod: int
    desc: str
    lb: BoundEstimate
    exp: BoundEstimate
    ub: BoundEstimate


class DamageIndicator(TypedDict):
    di: int
    name: str
    dods: list[DegreeOfDamage]


# Wikipedia storm-colour hexes for the EF tornado palette (no leading #).
EF_COLORS: dict[str, str] = {
    "EFU": "CCCCCC",
    "EF0": "4DFFFF",
    "EF1": "FFFFD9",
    "EF2": "FFD98C",
    "EF3": "FF9E59",
    "EF4": "FF738A",
    "EF5": "A188FC",
}

_EF_SCALE_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "config", "ef_scale.json"
)

if not os.path.exists(_EF_SCALE_JSON):
    raise FileNotFoundError(
        f"EF scale data file not found at {_EF_SCALE_JSON}. "
        "This file is required — it carries the vendored Wikipedia table."
    )

with open(_EF_SCALE_JSON, encoding="utf-8") as _f:
    DAMAGE_INDICATORS: list[DamageIndicator] = json.load(_f)


def _extract_abbr(name: str) -> str | None:
    """Return the parenthesised abbreviation of a DI name, if present.

    e.g. "One- or two-family residences (FR12)" -> "FR12".
    """
    start = name.rfind("(")
    end = name.rfind(")")
    if start != -1 and end > start:
        return name[start + 1 : end]
    return None


def get_di(di_no: int) -> DamageIndicator | None:
    """Return the damage indicator with the given number, or None."""
    for di in DAMAGE_INDICATORS:
        if di["di"] == di_no:
            return di
    return None


def get_dod(di_no: int, dod_no: int) -> DegreeOfDamage | None:
    """Return a specific degree of damage, or None if it does not exist."""
    di = get_di(di_no)
    if di is None:
        return None
    for dod in di["dods"]:
        if dod["dod"] == dod_no:
            return dod
    return None


def max_dod(di: DamageIndicator) -> int:
    """Highest DoD number for a damage indicator."""
    return len(di["dods"])


def ef_color(ef: str | None) -> int:
    """Map an EF rating to its Wikipedia hex as a Discord embed color.

    Unknown/missing ratings (including None / EFU) fall back to grey.
    """
    hex_str = EF_COLORS.get(ef or "EFU", "CCCCCC")
    return int(hex_str, 16)


def search_damage_indicators(query: str) -> list[DamageIndicator]:
    """Ranked search over damage indicators.

    Matches by, in order: exact DI number, number prefix, abbreviation
    prefix (e.g. "FR12"), then name substring (case-insensitive). An empty
    query returns all 28 in number order.
    """
    q = query.strip().lower()
    if not q:
        return list(DAMAGE_INDICATORS)

    def rank(di: DamageIndicator) -> tuple[int, int]:
        name = di["name"].lower()
        no = str(di["di"])
        if no == q:
            return (0, di["di"])
        if no.startswith(q):
            return (1, di["di"])
        abbr = _extract_abbr(di["name"])
        if abbr and abbr.lower().startswith(q):
            return (2, di["di"])
        if q in name:
            return (3, di["di"])
        return (4, di["di"])

    matches = [di for di in DAMAGE_INDICATORS if rank(di)[0] < 4]
    matches.sort(key=rank)
    return matches
