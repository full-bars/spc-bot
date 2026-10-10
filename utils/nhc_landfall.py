"""Detect official landfall statements in NHC tropical cyclone products.

NHC announces landfall in two places inside the same product: an all-caps
headline built from ``...TEXT...`` blocks and a prose sentence in the body
("NWS Doppler Radar data indicate that Hurricane Isaias made landfall near
Destin, Florida, around 830 PM CDT (0130 UTC) with maximum sustained winds
of 105 mph"). Both are captured so a caller can lead with the headline and
quote the sentence.

Only *completed* landfall language matches. Forecast wording uses the base
verb ("will make landfall", "to cross the coastline"), which the phrase list
deliberately omits; a second pass rejects anything still hedged by a future
modal ("is expected to have made landfall") that precedes the match.
"""

import re
from dataclasses import dataclass

# Completed-event phrasings. Inflected verbs only — the base form ("make",
# "cross", "come") is what NHC uses for forecasts, so leaving it out rejects
# future-tense text before any heuristic runs.
_LANDFALL_PHRASES = re.compile(
    r"(?:"
    r"made\s+(?:its\s+)?landfall"
    r"|makes\s+(?:its\s+)?landfall"
    r"|making\s+(?:its\s+)?landfall"
    r"|landfall\s+(?:has\s+)?occurred"
    r"|crossed\s+the\s+coastline"
    r"|crosses\s+the\s+coastline"
    r"|came\s+ashore"
    r"|comes\s+ashore"
    r")",
    re.IGNORECASE,
)

# Modal wording that can only precede a *future* event. If one shows up before
# the landfall phrase in the same sentence, the event has not happened yet.
_FUTURE_MODAL_RE = re.compile(
    r"(?:"
    r"\b(?:will|expected|expect|forecast|forecasts|should|could|would|might|may|"
    r"likely|probable|anticipat\w*)\b"
    r"|\bto\s+(?:make|cross|come|reach|hit)\b"
    r")",
    re.IGNORECASE,
)

# "made landfall near Destin, Florida, around 830 PM CDT" — place name, with an
# optional trailing state/country when NHC writes it as a comma pair.
_PLACE_RE = re.compile(
    r"\blandfall\s+near\s+"
    r"([A-Za-z][\w.'-]*(?:\s+[A-Za-z][\w.'-]*){0,4}(?:,\s*[A-Za-z][\w.'-]+)?)",
    re.IGNORECASE,
)


@dataclass
class LandfallMatch:
    """A confirmed landfall statement found in a product.

    ``headline`` is the ``...MAKES LANDFALL...`` banner when present;
    ``sentence`` is the most informative prose statement (the headline itself
    when the product carries no prose match).
    """

    sentence: str
    headline: str | None
    place: str | None

    @property
    def display(self) -> str:
        return self.sentence


def _flatten_blocks(text: str) -> list[str]:
    """Split a product into blocks and collapse NHC's ~72-column wrapping."""
    blocks = []
    for block in re.split(r"\n\s*\n", text):
        flat = re.sub(r"\s+", " ", block).strip()
        if flat:
            blocks.append(flat)
    return blocks


def _split_segments(block: str) -> list[str]:
    """Split a flattened block into sentences/headline fragments.

    Headlines are ellipsis-delimited (``...TEXT... ...TEXT...``); prose runs
    wrap to one logical line, so splitting on ``. `` recovers sentences.
    """
    segments: list[str] = []
    for piece in re.split(r"\.{2,}", block):
        piece = piece.strip()
        if not piece:
            continue
        segments.extend(s.strip() for s in re.split(r"(?<=\.)\s+", piece) if s.strip())
    return segments


def _is_headline(segment: str, block: str) -> bool:
    """True for NHC's all-caps ``...TEXT...`` banners rather than prose."""
    if block.startswith("..."):
        return True
    letters = [c for c in segment if c.isalpha()]
    if len(letters) < 6:
        return False
    return sum(c.islower() for c in letters) / len(letters) < 0.2


def _has_completed_landfall(segment: str) -> bool:
    """True when `segment` states a landfall that has already happened."""
    match = _LANDFALL_PHRASES.search(segment)
    if not match:
        return False
    return not _FUTURE_MODAL_RE.search(segment[: match.start()])


def _extract_place(sentence: str) -> str | None:
    m = _PLACE_RE.search(sentence)
    if not m:
        return None
    place = m.group(1).strip().strip(",.").strip()
    # Headline matches come back in NHC's all-caps banner style.
    if place.isupper():
        place = place.title()
    return place or None


def detect_landfall(text: str) -> LandfallMatch | None:
    """Return a :class:`LandfallMatch` if `text` officially reports landfall.

    Returns None for forecast language, watch/warning boilerplate, and plain
    geographic mentions of "the coastline".
    """
    if not text:
        return None

    headline: str | None = None
    sentence: str | None = None

    for block in _flatten_blocks(text):
        for segment in _split_segments(block):
            if not _has_completed_landfall(segment):
                continue
            if _is_headline(segment, block):
                headline = headline or segment
            elif sentence is None:
                sentence = segment

    if sentence is not None:
        best = sentence
    elif headline is not None:
        best = headline
    else:
        return None

    return LandfallMatch(
        sentence=best,
        headline=headline,
        place=_extract_place(best),
    )
