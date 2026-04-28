# cogs/warnings.py
"""NWS warning posting (PR A — foundation).

Polls the NWS API for active TOR / SVR / FFW warnings every 30 seconds,
deduplicates by VTEC event-tracking number (ETN), and posts each new
issuance as a Discord embed in the warnings channel.

This is the v1 baseline. Subsequent PRs add:
  - PR B: iembot fast-trigger fallback (sub-15s latency).
  - PR C: nearest-NEXRAD radar loop GIF on each post.
  - PR D: lifecycle (cancellation/expiration → edit message).
  - PR E: PDS / Tornado Emergency styling.
  - PR F: SPS path with severe-only filter.
"""
from __future__ import annotations

import asyncio  # noqa: F401  # used by future PRs
import json as _json
import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import discord
from discord.ext import commands, tasks

from config import NWS_ALERTS_WARNINGS_URL, WARNINGS_CHANNEL_ID
from utils.backoff import TaskBackoff
from utils.http import http_get_bytes_conditional
from utils.nexrad import find_nearest_radar, polygon_centroid
from utils.state_store import (
    add_posted_warning,
    get_posted_warnings,
    prune_posted_warnings,
)

logger = logging.getLogger("spc_bot")


# ── VTEC parsing ─────────────────────────────────────────────────────────────

# VTEC string format (NWS Directive 10-1703):
#
#   /O.NEW.KOUN.TO.W.0042.260427T2018Z-260427T2100Z/
#    │ │   │    │  │ │    │
#    │ │   │    │  │ │    issuance / expiration timestamps
#    │ │   │    │  │ event tracking number (ETN, 4-digit, stable across the
#    │ │   │    │  │ warning's full lifecycle — our dedup key)
#    │ │   │    │  significance (W=warning, A=watch, Y=advisory, S=statement)
#    │ │   │    phenomenon (TO=tornado, SV=svr tstm, FF=flash flood, etc.)
#    │ │   issuing office (4-letter ICAO, e.g. KOUN = Norman)
#    │ action (NEW, CON, EXP, CAN, UPG, EXA, EXT)
#    fixed: O = operational
_VTEC_RE = re.compile(
    r"/O\.(NEW|CON|EXP|CAN|UPG|EXA|EXT|ROU)\."  # action
    r"([A-Z]{4})\."                              # office
    r"([A-Z]{2})\."                              # phenomenon
    r"([A-Z])\."                                 # significance
    r"(\d{4})\."                                 # ETN
    r"(\d{6}T\d{4}Z)-(\d{6}T\d{4}Z)/"            # start/end
)


def parse_vtec(text: str) -> Optional[dict]:
    """Parse the first VTEC string in ``text`` and return its components.

    Returns a dict with ``action``, ``office``, ``phenom``, ``sig``,
    ``etn``, plus the dedup key ``vtec_id`` (``OFFICE.PH.S.ETN``).
    Returns ``None`` if no VTEC is present.
    """
    if not text:
        return None
    m = _VTEC_RE.search(text)
    if not m:
        return None
    action, office, phenom, sig, etn, _start, _end = m.groups()
    return {
        "action": action,
        "office": office,
        "phenom": phenom,
        "sig": sig,
        "etn": etn,
        "vtec_id": f"{office}.{phenom}.{sig}.{etn}",
    }


# ── Polygon parsing (LAT...LON block) ────────────────────────────────────────

_LATLON_RE = re.compile(
    r"LAT\.\.\.LON\s+([\d\s]+?)(?=\n\s*[A-Z\$]|$)",
    re.IGNORECASE | re.DOTALL,
)


def parse_warning_polygon(
    text: str,
) -> Optional[List[Tuple[float, float]]]:
    """Parse the ``LAT...LON`` polygon block from a VTEC product.

    Format: pairs of integer values, lat then lon, in degrees * 100,
    space- or newline-delimited. Longitudes are reported as positive
    integers; for the US they convert to negative decimal degrees.

    Returns a list of (lat, lon) decimal-degree pairs, or ``None`` if
    the block is missing or unparseable. Used by PR B's iembot
    fallback to derive a polygon centroid when NWS API hasn't picked
    up the alert yet.
    """
    if not text:
        return None
    m = _LATLON_RE.search(text)
    if not m:
        return None
    nums = m.group(1).split()
    coords: List[Tuple[float, float]] = []
    for i in range(0, len(nums) - 1, 2):
        try:
            lat = int(nums[i]) / 100.0
            lon = -(int(nums[i + 1]) / 100.0)
        except ValueError:
            continue
        if not (15.0 <= lat <= 75.0 and -170.0 <= lon <= -60.0):
            # Sanity-clip — NWS warnings only fire over US territory.
            continue
        coords.append((lat, lon))
    return coords or None


# ── Cog ──────────────────────────────────────────────────────────────────────

# (emoji, color) for each event type. v1 covers TOR/SVR/FFW only;
# distinct PDS / Tornado Emergency styling lands in PR E.
_WARNING_STYLE = {
    "Tornado Warning":             ("🌪️", discord.Color.red()),
    "Severe Thunderstorm Warning": ("⛈️", discord.Color.gold()),
    "Flash Flood Warning":         ("🌊", discord.Color.dark_blue()),
}

# Hard-cap on the description block we render inside a code-block —
# Discord embed descriptions cap at 4096 chars total, fences add 8.
_DESCRIPTION_LIMIT = 4000


def _polygon_from_nws_feature(
    feature: dict,
) -> Optional[List[Tuple[float, float]]]:
    """Extract the warning polygon from a NWS API GeoJSON feature.

    NWS warning geometries are typically ``Polygon`` with a single
    outer ring. Coordinates arrive in ``[lon, lat]`` order; we flip to
    our internal ``(lat, lon)`` convention so the radar lookup, which
    expects lat-first, can consume the result directly.
    """
    geom = feature.get("geometry") or {}
    gtype = geom.get("type")
    if gtype == "Polygon":
        rings = geom.get("coordinates") or []
        if not rings:
            return None
        ring = rings[0]
    elif gtype == "MultiPolygon":
        polys = geom.get("coordinates") or []
        if not polys:
            return None
        ring = polys[0][0]
    else:
        return None
    out: List[Tuple[float, float]] = []
    for pair in ring:
        try:
            lon, lat = float(pair[0]), float(pair[1])
        except (ValueError, TypeError, IndexError):
            continue
        out.append((lat, lon))
    return out or None


def radar_loop_url(icao: str) -> str:
    """Public NWS Ridge2 reflectivity loop GIF URL for a radar site."""
    return f"https://radar.weather.gov/ridge/standard/{icao}_loop.gif"


async def resolve_radar_for_polygon(
    coords: Optional[List[Tuple[float, float]]],
) -> Tuple[Optional[str], Optional[float]]:
    """Pick the nearest WSR-88D site to a warning polygon's centroid.

    Returns ``(icao, distance_km)`` or ``(None, None)`` when the polygon
    is missing or the NEXRAD site list is unavailable.
    """
    if not coords:
        return None, None
    centroid = polygon_centroid(coords)
    if not centroid:
        return None, None
    result = await find_nearest_radar(*centroid)
    if not result:
        return None, None
    return result


def _extract_narrative(raw: str) -> Optional[str]:
    """Pull the human-readable narrative out of a raw VTEC product.

    The narrative is the section after the bulletin headers and before
    the boilerplate footer (LAT...LON, ATTN, $$). Used by the iembot
    fast-path when we don't yet have NWS API's pre-formatted description.
    """
    if not raw:
        return None

    text = raw
    # Drop the WMO header / AFOS header / VTEC line block at the top so
    # we lead with the substantive narrative rather than transmission
    # metadata. Heuristic: find the line that begins "BULLETIN -" or
    # the first line starting with "The National Weather Service in".
    nws_idx = re.search(
        r"(?m)^(?:BULLETIN.*|The National Weather Service\b.*)$", text
    )
    if nws_idx:
        text = text[nws_idx.start():]

    # Trim known footers — order matters because LAT...LON usually
    # precedes ATTN.
    for footer in ("LAT...LON", "ATTN...WFO", "TIME...MOT...LOC", "$$"):
        m = re.search(re.escape(footer), text, re.IGNORECASE)
        if m:
            text = text[: m.start()]
    text = text.strip()
    return text or None


class WarningsCog(commands.Cog):
    MANAGED_TASK_NAMES = [("auto_poll_warnings", "auto_poll_warnings")]

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._backoff = TaskBackoff("auto_poll_warnings")
        self._validators = {"etag": "", "last_modified": ""}

    async def cog_load(self):
        # Restore the dedup set so a restart during active wx doesn't
        # replay every warning the bot has already posted today.
        try:
            persisted = await get_posted_warnings()
            self.bot.state.posted_warnings.update(persisted)
            logger.info(
                f"[WARN] Restored {len(persisted)} posted warning(s) from store"
            )
        except Exception as e:
            logger.warning(f"[WARN] Could not restore posted_warnings: {e}")

        self.auto_poll_warnings.start()

    def cog_unload(self):
        self.auto_poll_warnings.cancel()

    # ── iembot fast-trigger path ───────────────────────────────────────────
    #
    # IEMBotCog calls this when a TOR/SVR/FFW product hits the botstalk
    # seqnum stream. Latency is typically 5-15s vs. the 30s NWS API
    # poll, so for severe wx the iembot path is the one that lands first.
    # We dedup against the same posted_warnings set as the NWS API
    # path; whichever fires first wins, the other is a no-op.

    async def post_warning_now(
        self, product_id: str, raw_text: str, event: str
    ):
        """Post a warning triggered by iembot. ``raw_text`` is the full
        VTEC product as plain text from the IEM nwstext API."""
        if not self.bot.state.is_primary:
            return
        channel = self.bot.get_channel(WARNINGS_CHANNEL_ID)
        if not channel:
            return

        vtec = parse_vtec(raw_text)
        if not vtec:
            logger.warning(
                f"[WARN] iembot trigger: no VTEC in {product_id} — skipping"
            )
            return
        # PR B only handles the initial NEW issuance. SVS/FFS updates and
        # cancellations land in PR D.
        if vtec["action"] != "NEW":
            return

        vtec_id = vtec["vtec_id"]
        if vtec_id in self.bot.state.posted_warnings:
            return
        # Claim the dedup key BEFORE the (possibly slow) Discord send so
        # a concurrent NWS API poll can't double-post.
        self.bot.state.posted_warnings.add(vtec_id)

        narrative = _extract_narrative(raw_text)
        emoji, color = _WARNING_STYLE.get(event, ("⚠️", discord.Color.orange()))

        body = narrative or ""
        if len(body) > _DESCRIPTION_LIMIT:
            body = body[:_DESCRIPTION_LIMIT].rstrip() + "\n..."

        embed = discord.Embed(
            title=f"{emoji} {event}",
            description=f"```\n{body}\n```" if body else None,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Office",
            value=vtec["office"],
            inline=True,
        )

        # Iembot path: pull the polygon out of the LAT...LON block
        # ourselves since NWS API may not have the alert yet. PR D will
        # backfill higher-fidelity NWS API data via an upgrade poll.
        coords = parse_warning_polygon(raw_text)
        await self._attach_radar(embed, coords)

        embed.set_footer(text=f"VTEC {vtec_id} (iembot fast-path)")

        try:
            await channel.send(embed=embed)
            logger.info(f"[WARN] Posted (iembot) {event} {vtec_id}")
        except discord.HTTPException as e:
            # Roll back the dedup claim on a hard send failure so the
            # NWS API path or the next iembot retrigger can try again.
            self.bot.state.posted_warnings.discard(vtec_id)
            logger.exception(
                f"[WARN] iembot send failed for {vtec_id}: {e}"
            )
            return

        try:
            await add_posted_warning(vtec_id, time.time())
            await prune_posted_warnings()
        except Exception as e:
            logger.warning(f"[WARN] Failed to persist {vtec_id}: {e}")

    @tasks.loop(seconds=30)
    async def auto_poll_warnings(self):
        # The body is wrapped so a single bad alert can't kill the loop
        # — same pattern we use for monitor_high_risk_soundings.
        try:
            await self._tick()
        except Exception as e:
            logger.exception(f"[WARN] Tick failed: {e}")
            await self._backoff.failure(self.bot)

    async def _tick(self):
        await self.bot.wait_until_ready()
        if not self.bot.state.is_primary:
            return

        channel = self.bot.get_channel(WARNINGS_CHANNEL_ID)
        if not channel:
            logger.warning("[WARN] Warnings channel not found — skipping poll")
            return

        content, status, validators = await http_get_bytes_conditional(
            NWS_ALERTS_WARNINGS_URL,
            etag=self._validators.get("etag") or None,
            last_modified=self._validators.get("last_modified") or None,
            retries=2,
            timeout=15,
        )
        if status == 304:
            self._backoff.success()
            return
        if not content or status != 200:
            logger.warning(
                f"[WARN] NWS API returned status {status} — will retry next cycle"
            )
            await self._backoff.failure(self.bot)
            return
        if validators and (validators.get("etag") or validators.get("last_modified")):
            self._validators["etag"] = validators.get("etag", "")
            self._validators["last_modified"] = validators.get("last_modified", "")

        try:
            data = _json.loads(content)
        except Exception as e:
            logger.warning(f"[WARN] JSON parse failed: {e}")
            return

        for feature in data.get("features", []) or []:
            props = feature.get("properties", {}) or {}
            event = props.get("event", "")
            if event not in _WARNING_STYLE:
                continue

            vtec_list = props.get("parameters", {}).get("VTEC", []) or []
            issuance_id: Optional[str] = None
            for v in vtec_list:
                parsed = parse_vtec(v)
                if parsed and parsed["action"] == "NEW":
                    issuance_id = parsed["vtec_id"]
                    break
            if not issuance_id:
                # Update / continuation / cancellation features arrive as
                # separate alerts; PR D handles those. v1 only posts on
                # the original NEW issuance.
                continue
            if issuance_id in self.bot.state.posted_warnings:
                continue

            try:
                await self._post_warning(feature, channel, issuance_id, event)
            except discord.HTTPException as e:
                logger.exception(f"[WARN] Send failed for {issuance_id}: {e}")
                continue

            self.bot.state.posted_warnings.add(issuance_id)
            try:
                await add_posted_warning(issuance_id, time.time())
                await prune_posted_warnings()
            except Exception as e:
                logger.warning(
                    f"[WARN] Failed to persist {issuance_id}: {e}"
                )

        self._backoff.success()

    async def _post_warning(
        self,
        feature: dict,
        channel: discord.abc.Messageable,
        vtec_id: str,
        event: str,
    ):
        props = feature.get("properties", {}) or {}
        emoji, color = _WARNING_STYLE.get(event, ("⚠️", discord.Color.orange()))

        description = props.get("description", "") or ""
        if len(description) > _DESCRIPTION_LIMIT:
            description = description[:_DESCRIPTION_LIMIT].rstrip() + "\n..."

        embed = discord.Embed(
            title=f"{emoji} {event}",
            description=f"```\n{description}\n```" if description else None,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

        sender = props.get("senderName", "")
        if sender:
            embed.add_field(name="Office", value=sender, inline=True)

        expires = props.get("expires") or props.get("ends")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(expires).astimezone(timezone.utc)
                embed.add_field(
                    name="Expires",
                    value=exp_dt.strftime("%H:%MZ %m/%d"),
                    inline=True,
                )
            except (ValueError, TypeError):
                pass

        area = props.get("areaDesc", "")
        if area:
            embed.add_field(name="Area", value=area[:1024], inline=False)

        # Pull the polygon from NWS API geometry (already structured)
        # and attach the nearest radar's loop GIF as the embed image.
        coords = _polygon_from_nws_feature(feature)
        await self._attach_radar(embed, coords)

        # Footer carries the VTEC ETN so we can correlate Discord posts
        # back to the source alert when reading logs or debugging.
        embed.set_footer(text=f"VTEC {vtec_id}")

        await channel.send(embed=embed)
        logger.info(f"[WARN] Posted {event} {vtec_id}")

    async def _attach_radar(
        self,
        embed: discord.Embed,
        coords: Optional[List[Tuple[float, float]]],
    ) -> None:
        """Resolve the nearest WSR-88D and set the embed image to its
        live reflectivity loop GIF. No-op if we can't resolve a site —
        we'd rather post the warning without radar than skip it."""
        icao, dist_km = await resolve_radar_for_polygon(coords)
        if not icao:
            return
        embed.set_image(url=radar_loop_url(icao))
        # Fold the radar attribution into the existing fields rather
        # than another row to keep the embed compact.
        embed.add_field(
            name="Radar",
            value=f"{icao} ({dist_km:.0f} km)" if dist_km else icao,
            inline=True,
        )

    @auto_poll_warnings.after_loop
    async def after_loop(self):
        if self.auto_poll_warnings.is_being_cancelled():
            return
        task = self.auto_poll_warnings.get_task()
        try:
            exc = task.exception() if task else None
        except Exception:
            exc = None
        if exc:
            logger.error(
                f"[TASK] auto_poll_warnings stopped: "
                f"{type(exc).__name__}: {exc}",
                exc_info=exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(WarningsCog(bot))
