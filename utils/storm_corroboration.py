"""Independent corroboration for the active tropical cyclone list.

The tracker's primary source is a hand-scraped HTML page
(``https://www.nhc.noaa.gov/cyclones``): the SVG map blocks and the storm-ID
comment table have to agree on an exact name, and a single transient mismatch
there once produced a public "Storm Dissipated" post for a hurricane that was
still running. Nothing may be declared gone on that page's word alone.

Each source below answers the same question — "which storm IDs are active?" —
over an unrelated pipeline:

``nhc-rss``
    NHC's per-basin RSS indexes (``index-at.xml`` / ``index-ep.xml`` /
    ``index-cp.xml``), structured XML generated from NHC's storm database
    rather than the cyclones page markup. Authoritative: only these feeds may
    vote a storm *gone*, and only when all three loaded.
``nesdis``
    NESDIS/STAR GOES storm floaters — a different NOAA office and product.
``tidbits``
    Tropical Tidbits' ``storminfo`` JSON — third party.

Verdict:

* any source lists the storm                          → active
* all three NHC RSS feeds loaded and nobody lists it  → gone
* otherwise                                           → inconclusive

Third-party sources may only ever rescue a storm (vote "active"); they are
never trusted to declare one gone, because their coverage is not guaranteed
for every basin.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from utils.http import http_get_text

logger = logging.getLogger("spc_bot")

SOURCE_NHC_RSS = "nhc-rss"
SOURCE_NESDIS = "nesdis"
SOURCE_TIDBITS = "tidbits"

NHC_RSS_URLS: dict[str, str] = {
    "AL": "https://www.nhc.noaa.gov/index-at.xml",
    "EP": "https://www.nhc.noaa.gov/index-ep.xml",
    "CP": "https://www.nhc.noaa.gov/index-cp.xml",
}
NESDIS_URL = "https://www.star.nesdis.noaa.gov/GOES/"
TIDBITS_URL = "https://tropicaltidbits.com/storminfo/stormhtml.json"

_FETCH_RETRIES = 2
_FETCH_TIMEOUT = 12

_STORM_ID_RE = re.compile(r"\b([A-Z]{2}\d{6})\b")
_TITLE_RE = re.compile(r"<title>([^<]*)</title>", re.IGNORECASE)
_FLOATER_RE = re.compile(r"stormid=([A-Z]{2}\d{6})", re.IGNORECASE)
_TIDBITS_KEY_RE = re.compile(r"(\d{2})([A-Z])")
# Tropical Tidbits keys storms by ATCF basin suffix: L=Atlantic, E=East
# Pacific, C=Central Pacific. Others (W, S, ...) are outside tracked basins.
_TIDBITS_BASIN = {"L": "AL", "E": "EP", "C": "CP"}


def ids_from_nhc_rss(xml: str) -> set[str]:
    """Storm IDs named in an NHC basin RSS feed.

    Titles carry the full ID, e.g. ``Summary for Hurricane Polo (EP2/EP172026)``.
    """
    return {
        storm_id for title in _TITLE_RE.findall(xml) for storm_id in _STORM_ID_RE.findall(title)
    }


def ids_from_nesdis(html: str) -> set[str]:
    """Storm IDs in the STAR GOES floater nav (``floater.php?stormid=EP172026``)."""
    return {m.upper() for m in _FLOATER_RE.findall(html)}


def ids_from_tidbits(payload: str, year: int | None = None) -> set[str]:
    """Storm IDs from Tropical Tidbits' ``storminfo`` JSON.

    Keys are ATCF basin numbers (``06L``, ``17E``, ``01C``) rather than full
    IDs, so they are expanded with the current year. Keys in the 90-99 range
    are invests, not named/numbered cyclones, and are skipped.
    """
    data = json.loads(payload)
    if not isinstance(data, dict):
        return set()
    if year is None:
        year = datetime.now(timezone.utc).year

    ids: set[str] = set()
    for key in data:
        m = _TIDBITS_KEY_RE.fullmatch(str(key))
        if not m:
            continue
        number, suffix = m.groups()
        basin = _TIDBITS_BASIN.get(suffix)
        if not basin or number.startswith("9"):
            continue
        ids.add(f"{basin}{number}{year}")
    return ids


@dataclass(frozen=True)
class Corroboration:
    """Verdict on one storm ID from the independent sources."""

    storm_id: str
    active: bool | None  # True = still listed, False = gone, None = inconclusive
    seen_in: tuple[str, ...] = ()
    detail: str = ""

    @property
    def sources(self) -> str:
        return ", ".join(self.seen_in)


@dataclass
class SourceSets:
    """Storm IDs per source; ``None`` means the source could not be fetched."""

    rss: dict[str, set[str] | None] = field(default_factory=dict)
    nesdis: set[str] | None = None
    tidbits: set[str] | None = None

    @property
    def nhc_rss_complete(self) -> bool:
        """True only when every basin feed was fetched and parsed."""
        return bool(self.rss) and all(ids is not None for ids in self.rss.values())

    @property
    def reachable(self) -> list[str]:
        out = [SOURCE_NHC_RSS] if self.nhc_rss_complete else []
        if self.nesdis is not None:
            out.append(SOURCE_NESDIS)
        if self.tidbits is not None:
            out.append(SOURCE_TIDBITS)
        return out

    def evaluate(self, storm_id: str) -> Corroboration:
        seen_in: list[str] = []
        if any(ids is not None and storm_id in ids for ids in self.rss.values()):
            seen_in.append(SOURCE_NHC_RSS)
        if self.nesdis is not None and storm_id in self.nesdis:
            seen_in.append(SOURCE_NESDIS)
        if self.tidbits is not None and storm_id in self.tidbits:
            seen_in.append(SOURCE_TIDBITS)

        if seen_in:
            return Corroboration(
                storm_id, True, tuple(seen_in), f"still listed by {', '.join(seen_in)}"
            )
        if self.nhc_rss_complete:
            return Corroboration(
                storm_id,
                False,
                (),
                "absent from all three NHC RSS basin feeds",
            )
        missing = [basin for basin, ids in sorted(self.rss.items()) if ids is None]
        return Corroboration(
            storm_id,
            None,
            (),
            "no source listed it and the NHC RSS feeds did not all load"
            + (f" (failed: {', '.join(missing)})" if missing else ""),
        )


async def _get(url: str) -> str | None:
    """Fetch a corroboration source; a source that is down proves nothing."""
    try:
        return await http_get_text(url, retries=_FETCH_RETRIES, timeout=_FETCH_TIMEOUT)
    except Exception as exc:  # CircuitOpenError, timeouts, client errors
        logger.debug(f"Corroboration source unreachable ({url}): {exc}")
        return None


async def fetch_sources(year: int | None = None) -> SourceSets:
    """Fetch every corroboration source concurrently.

    Returns a :class:`SourceSets` whose entries are ``None`` where the source
    was unreachable, so callers can tell "not listed" from "not asked".
    """
    labelled: list[tuple[str, str]] = [(f"rss:{basin}", url) for basin, url in NHC_RSS_URLS.items()]
    labelled += [("nesdis", NESDIS_URL), ("tidbits", TIDBITS_URL)]

    texts = await asyncio.gather(*(_get(url) for _, url in labelled))

    # Every basin starts as None (unanswered) so a failed feed can never be
    # mistaken for "fetched and empty".
    sources = SourceSets(rss={basin: None for basin in NHC_RSS_URLS})
    for (label, _url), text in zip(labelled, texts):
        if text is None:
            continue
        try:
            if label.startswith("rss:"):
                sources.rss[label.split(":", 1)[1]] = ids_from_nhc_rss(text)
            elif label == "nesdis":
                sources.nesdis = ids_from_nesdis(text)
            else:
                sources.tidbits = ids_from_tidbits(text, year=year)
        except Exception as exc:  # malformed body must not count as "empty"
            logger.warning(f"Corroboration source {label} failed to parse: {exc}")
    return sources


async def corroborate(storm_id: str, year: int | None = None) -> Corroboration:
    """Fetch all sources and return the verdict for one storm ID."""
    return (await fetch_sources(year)).evaluate(storm_id)
