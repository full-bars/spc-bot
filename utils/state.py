# utils/state.py
"""
BotState — in-memory state for the bot, attached to the bot instance
as `bot.state` at startup.

Composition
-----------
BotState is a thin coordinator over three focused sub-stores:

  HashStore     — image-hash caches used for content-change detection.
  PostingLog    — which MDs / watches / CSU days we've already posted,
                  plus the currently-active set (as returned by the NWS
                  feed).
  TimingTracker — when each category was last posted, and the URLs we
                  published for each outlook day.

BotState still exposes the legacy attribute names (`posted_mds`,
`auto_cache`, `last_post_times`, …) as properties so every existing
call site keeps working unchanged. The sub-store references are also
exposed directly (`state.hashes`, `state.posting`, `state.timing`) so
new code can take an explicit dependency on the component it needs
rather than on the whole coordinator.
"""

import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set


class RecentLogHandler(logging.Handler):
    """Logging handler that keeps the last N lines in memory."""

    def __init__(self, max_lines: int = 20):
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=max_lines)

    def emit(self, record):
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            self.handleError(record)

    def get_logs(self) -> List[str]:
        return list(self.buffer)


class BoundedFIFOKeys:
    """Insertion-ordered FIFO set with O(1) ``in``, ``append``, ``remove``.

    Backed by ``dict`` (which preserves insertion order in CPython 3.7+).
    Drop-in for a ``deque(maxlen=N)`` of strings on the hot dedup path,
    where ``in`` was previously O(N) and dominated the cost during severe
    weather outbreaks with thousands of products/sec.

    Only the methods used by callers of ``posted_product_ids`` are
    implemented; this is not a general container.
    """

    __slots__ = ("_d", "_maxlen")

    def __init__(self, maxlen: int = 5000):
        self._d: Dict[str, None] = {}
        self._maxlen = maxlen

    def append(self, key: str) -> None:
        if key in self._d:
            return
        self._d[key] = None
        if len(self._d) > self._maxlen:
            # Evict oldest (dicts preserve insertion order; first key is oldest).
            # Only one entry can be added per call, so a single eviction suffices.
            try:
                oldest = next(iter(self._d))
                del self._d[oldest]
            except StopIteration:
                pass

    def extend(self, keys) -> None:
        for k in keys:
            self.append(k)

    def remove(self, key: str) -> None:
        # Match deque.remove semantics: ValueError if absent
        if key not in self._d:
            raise ValueError(key)
        del self._d[key]

    def __contains__(self, key) -> bool:
        return key in self._d

    def __iter__(self):
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)


class HashStore:
    """Image-hash caches and partial-update state."""

    __slots__ = ("auto_cache", "manual_cache", "partial_update_state", "_in_flight")

    def __init__(self):
        self.auto_cache: Dict[str, str] = {}
        self.manual_cache: Dict[str, str] = {}
        self.partial_update_state: Dict[str, Dict] = {}
        self._in_flight: Set[str] = set()


class PostingLog:
    """Deduplication log for SPC posts and the currently-active alerts."""

    __slots__ = (
        "posted_mds",
        "posted_watches",
        "posted_warnings",
        "csu_posted",
        "active_mds",
        "active_watches",
        "active_warnings",
        "posted_reports",
        "posted_product_ids",
        "posted_soundings",
        "sounding_handled_watches",
    )

    def __init__(self):
        self.posted_mds: Set[str] = set()
        self.posted_watches: Set[str] = set()
        # Posted NWS warnings keyed by VTEC ETN (e.g. "KOUN.TO.W.0042").
        # Maps ETN -> {'message_id': int, 'channel_id': int}
        self.posted_warnings: Dict[str, dict] = {}
        self.csu_posted: Set[str] = set()
        self.active_mds: Set[str] = set()
        self.active_watches: Dict[str, dict] = {}
        # Currently-active VTEC IDs mapping to their latest vtec metadata dict
        self.active_warnings: Dict[str, dict] = {}
        self.posted_reports: Set[str] = set()
        self.posted_product_ids: BoundedFIFOKeys = BoundedFIFOKeys(maxlen=5000)
        self.posted_soundings: Set[str] = set()
        self.sounding_handled_watches: Set[str] = set()


class TimingTracker:
    """Per-category last-posted timestamps and URL payloads."""

    __slots__ = ("last_post_times", "last_posted_urls")

    def __init__(self):
        self.last_post_times: Dict[str, Optional[datetime]] = {
            "day1": None,
            "day2": None,
            "day3": None,
            "day48": None,
            "scp": None,
            "md": None,
            "watch": None,
            "csu_day1": None,
            "csu_day2": None,
            "csu_day3": None,
            "csu_day4": None,
            "csu_day5": None,
            "csu_day6": None,
            "csu_day7": None,
            "csu_day8": None,
            "csu_panel12": None,
            "csu_panel38": None,
            "wxnext": None,
            "sounding": None,
        }
        self.last_posted_urls: Dict[str, List[str]] = {}


def _parse_vtec_end_ts(end: str) -> Optional[float]:
    """Parse a VTEC end timestamp (``YYMMDDTHHMMZ``) to a Unix timestamp.

    Returns ``None`` for null VTEC times (``000000T0000Z``) or malformed
    input so callers can distinguish "no expiry data" from "expired"."""
    if not end or len(end) < 11 or end.startswith("000000"):
        return None
    try:
        year = 2000 + int(end[:2])
        month = int(end[2:4])
        day = int(end[4:6])
        hour = int(end[7:9])
        minute = int(end[9:11])
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()
    except (ValueError, IndexError):
        return None


def _delegate(store_attr: str, field: str) -> property:
    """Build a read/write property that forwards to a sub-store field.

    Reading returns the underlying container by reference, so existing
    callers doing `state.posted_mds.add(x)` keep mutating the same set
    the sub-store owns. Writing replaces the field on the sub-store.
    """

    def _get(self):
        return getattr(getattr(self, store_attr), field)

    def _set(self, value):
        setattr(getattr(self, store_attr), field, value)

    return property(_get, _set)


class BotState:
    """Top-level mutable state for the bot.

    Scalar flags (`is_primary`, `iembot_last_seqnum`) live here directly
    because they don't belong to any sub-store. Everything else is
    delegated to `hashes`, `posting`, or `timing`.
    """

    def __init__(self):
        self.is_primary: bool = True  # overridden by IS_PRIMARY env var in main.py
        self.iembot_last_seqnum: int = 0
        # Separate seqnum tracker for the iembot ``botstalk`` national
        # room — this is the warning-product fast-path. Tracked
        # independently from the spcchat seqnum so a stall in one feed
        # can't make us replay the other on restart.
        self.iembot_botstalk_last_seqnum: int = 0
        self.last_fronts_hash: Optional[str] = None
        self.bot_start_time: Optional[datetime] = None

        # Failover & Sync Stats
        self.failover_count: int = 0
        self.lease_renewals: int = 0
        self.sync_failures: int = 0

        # Latency tracking (seconds)
        self.iembot_latency: Optional[float] = None
        self.http_latency: Optional[float] = None
        self.nwws_latency: Optional[float] = None

        # Network pings (milliseconds)
        self.nwws_ping: Optional[float] = None
        self.iembot_ping: Optional[float] = None

        # NWWS message throughput tracking (messages per second, rolling average)
        self.nwws_msg_count: int = 0
        self.nwws_last_window_time: Optional[datetime] = None
        self.nwws_throughput: Optional[float] = None

        # Discord gateway tracking
        self.discord_gateway_url: Optional[str] = None
        self.discord_gateway_ip: Optional[str] = None
        self.discord_gateway_location: Optional[str] = None

        # Per-host rolling-window HTTP latency samples (seconds).
        # Bounded so a long-lived process can't accumulate samples forever.
        self.http_latency_by_host: Dict[str, deque[float]] = {}

        self.hashes = HashStore()
        self.posting = PostingLog()
        self.timing = TimingTracker()

    HTTP_LATENCY_WINDOW = 100  # samples kept per host

    def update_http_latency(self, latency: float, host: Optional[str] = None) -> None:
        """Update the rolling average of HTTP latency.

        When ``host`` is provided, also append the sample to the per-host
        rolling window so ``/status`` can show P50/P95 broken out by
        endpoint (NWS API vs IEM Autoplot vs Discord etc.)."""
        if self.http_latency is not None:
            self.http_latency = (self.http_latency * 0.9) + (latency * 0.1)
        else:
            self.http_latency = latency

        if host:
            samples = self.http_latency_by_host.get(host)
            if samples is None:
                samples = deque(maxlen=self.HTTP_LATENCY_WINDOW)
                self.http_latency_by_host[host] = samples
            samples.append(latency)

    def http_latency_percentiles(self, host: str) -> Optional[tuple[float, float]]:
        """Return (p50, p95) seconds for ``host``, or None if no samples."""
        samples = self.http_latency_by_host.get(host)
        if not samples:
            return None
        ordered = sorted(samples)
        n = len(ordered)
        p50 = ordered[n // 2]
        # nearest-rank P95; for small n this is just the max.
        p95 = ordered[min(n - 1, max(0, int(round(0.95 * n)) - 1))]
        return (p50, p95)

    # ── State Update Methods (Encapsulation) ───────────────────────────────

    async def add_posted_md(self, md_number: str) -> None:
        from utils import state_store

        self.posted_mds.add(md_number)
        await state_store.add_posted_md(md_number)

    async def add_posted_watch(self, watch_number: str) -> None:
        from utils import state_store

        self.posted_watches.add(watch_number)
        await state_store.add_posted_watch(watch_number)

    async def add_posted_report(self, product_id: str) -> None:
        from utils import state_store

        self.posted_reports.add(product_id)
        await state_store.add_posted_report(product_id)

    async def add_posted_product_id(self, product_id: str) -> None:
        from utils import state_store

        # posted_product_ids is a deque(maxlen=1000)
        if product_id not in self.posted_product_ids:
            self.posted_product_ids.append(product_id)
            await state_store.add_posted_product_id(product_id)

    async def add_posted_warning(
        self,
        vtec_id: str,
        message_id: int,
        channel_id: int,
        posted_at: float = 0.0,
        area: str = "",
        tornado_confidence: Optional[str] = None,
        tornado_severity: Optional[str] = None,
        severity: Optional[str] = None,
        raw_text: Optional[str] = None,
    ) -> None:
        from utils import state_store

        self.posted_warnings[vtec_id] = {
            "message_id": message_id,
            "channel_id": channel_id,
            "area": area,
        }
        await state_store.add_posted_warning(
            vtec_id,
            message_id,
            channel_id,
            posted_at,
            area,
            tornado_confidence,
            tornado_severity,
            severity,
            raw_text,
        )

    async def add_posted_sounding(self, pkey: str) -> None:
        from utils import state_store

        self.posted_soundings.add(pkey)
        await state_store.add_posted_sounding(pkey)

    async def add_sounding_handled_watch(self, watch_number: str) -> None:
        from utils import state_store

        self.sounding_handled_watches.add(watch_number)
        await state_store.add_sounding_handled_watch(watch_number)

    async def prune_posted_warnings(self, max_size: int = 5000) -> int:
        """Trim posted_warnings to the most recent ``max_size`` entries across
        SQLite, Redis, and the in-memory dict.

        Returns the number of in-memory entries removed. Unconfirmed
        placeholders (empty-dict values) are always preserved — a concurrent
        post may be mid-flight and the persisted row has not been written yet.
        """
        from utils import state_store

        await state_store.prune_posted_warnings(max_size)
        surviving = await state_store.get_all_posted_warnings()
        before = len(self.posted_warnings)
        self.posted_warnings = {
            k: v for k, v in self.posted_warnings.items() if k in surviving or not v
        }
        return before - len(self.posted_warnings)

    def sweep_active(
        self, grace_minutes: int = 60, now: Optional[datetime] = None
    ) -> tuple[int, int]:
        """Drop entries from ``active_warnings`` and ``active_watches`` whose
        VTEC ``end`` timestamp or watch ``expires`` field is past now+grace.

        Safety net for the normal NWS expiry path — a single missed API poll
        used to strand entries forever. The grace window prevents racing with
        an in-flight cancellation handler.

        Returns ``(warnings_removed, watches_removed)``.
        """
        now = now or datetime.now(timezone.utc)
        cutoff = now.timestamp() - grace_minutes * 60

        warnings_before = len(self.active_warnings)
        kept_warnings = {}
        for vtec_id, vtec in self.active_warnings.items():
            end = vtec.get("end", "") if isinstance(vtec, dict) else ""
            end_ts = _parse_vtec_end_ts(end)
            # Keep entries we can't parse (null VTEC time, malformed) — only
            # drop ones we have positive evidence are expired.
            if end_ts is None or end_ts >= cutoff:
                kept_warnings[vtec_id] = vtec
        self.active_warnings = kept_warnings

        watches_before = len(self.active_watches)
        kept_watches = {}
        for watch_num, info in self.active_watches.items():
            expires = info.get("expires") if isinstance(info, dict) else None
            if expires is None:
                kept_watches[watch_num] = info
                continue
            if isinstance(expires, datetime):
                if expires.timestamp() >= cutoff:
                    kept_watches[watch_num] = info
            else:
                # Unrecognised type — keep, don't risk dropping a live watch
                kept_watches[watch_num] = info
        self.active_watches = kept_watches

        return (
            warnings_before - len(self.active_warnings),
            watches_before - len(self.active_watches),
        )

    async def remove_posted_warning(self, vtec_id: str) -> None:
        """Roll back a posted warning across memory + SQLite + Redis."""
        from utils import state_store

        self.posted_warnings.pop(vtec_id, None)
        await state_store.remove_posted_warning(vtec_id)

    async def remove_posted_product_id(self, product_id: str) -> None:
        """Roll back a posted product ID across memory + SQLite + Redis.
        Silently no-ops if the id is not currently in the in-memory deque."""
        from utils import state_store

        try:
            self.posted_product_ids.remove(product_id)
        except ValueError:
            pass
        await state_store.remove_posted_product_id(product_id)

    def claim_posted_warning(self, vtec_id: str) -> "PostedWarningClaim":
        """Open an async context that reserves ``vtec_id`` in
        ``posted_warnings`` as a synchronous dedup signal.

        Usage::

            async with state.claim_posted_warning(vtec_id) as claim:
                msg = await channel.send(...)
                await claim.confirm(msg.id, msg.channel.id, ...)

        If ``confirm`` is not called (caller aborts, exception leaks, or
        an early ``continue`` skips it), the placeholder is rolled back on
        exit. If the vtec_id was already claimed when the context opens —
        e.g. a concurrent NWWS and IEM trigger racing for the same warning
        — the new claim becomes a no-op and exiting will not touch the
        other task's entry."""
        return PostedWarningClaim(self, vtec_id)

    async def add_csu_posted(self, day: str) -> None:
        from utils import state_store

        self.csu_posted.add(str(day))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        value = json.dumps({"date": today, "days": sorted(list(self.csu_posted))})
        await state_store.set_state("csu_mlp_posted", value)

    # ── Legacy attribute surface (delegated) ────────────────────────────────
    auto_cache = _delegate("hashes", "auto_cache")
    manual_cache = _delegate("hashes", "manual_cache")
    partial_update_state = _delegate("hashes", "partial_update_state")

    posted_mds = _delegate("posting", "posted_mds")
    posted_watches = _delegate("posting", "posted_watches")
    posted_warnings = _delegate("posting", "posted_warnings")
    csu_posted = _delegate("posting", "csu_posted")
    active_mds = _delegate("posting", "active_mds")
    active_watches = _delegate("posting", "active_watches")
    active_warnings = _delegate("posting", "active_warnings")
    posted_reports = _delegate("posting", "posted_reports")
    posted_product_ids = _delegate("posting", "posted_product_ids")
    posted_soundings = _delegate("posting", "posted_soundings")
    sounding_handled_watches = _delegate("posting", "sounding_handled_watches")

    last_post_times = _delegate("timing", "last_post_times")
    last_posted_urls = _delegate("timing", "last_posted_urls")

    # ── Serialization ───────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """Serialize state to a JSON-safe dict (for the failover /state
        endpoint). Shape is stable — the failover protocol depends on it."""
        return {
            "iembot_last_seqnum": self.iembot_last_seqnum,
            "auto_cache": self.auto_cache.copy(),
            "manual_cache": self.manual_cache.copy(),
            "posted_mds": list(self.posted_mds),
            "posted_watches": list(self.posted_watches),
            "posted_warnings": self.posted_warnings.copy(),
            "posted_reports": list(self.posted_reports),
            "posted_product_ids": list(self.posted_product_ids),
            "posted_soundings": list(self.posted_soundings),
            "sounding_handled_watches": list(self.sounding_handled_watches),
            "csu_posted": list(self.csu_posted),
            "active_mds": list(self.active_mds),
            "active_warnings": list(self.active_warnings.keys()),
            "active_watches": {
                k: {
                    "type": v.get("type"),
                    "expires": exp.isoformat() if (exp := v.get("expires")) else None,
                    "affected_zones": v.get("affected_zones", []),
                }
                for k, v in self.active_watches.copy().items()
                if isinstance(v, dict)
            },
            "last_posted_urls": self.last_posted_urls.copy(),
            "last_post_times": {
                k: v.isoformat() if v else None for k, v in self.last_post_times.copy().items()
            },
        }


class PostedWarningClaim:
    """Async context manager guarding a single ``posted_warnings`` entry.

    On enter, an empty-dict placeholder is written to ``state.posted_warnings``
    so concurrent tasks that check ``vtec_id in state.posted_warnings`` before
    awaiting immediately see the reservation. The caller must then either:

      * call ``await claim.confirm(message_id, channel_id, …)`` after the
        Discord post succeeds — this persists the real entry across SQLite +
        Redis via ``BotState.add_posted_warning`` and leaves the placeholder
        promoted to a real row, or
      * let the context exit without confirming (explicit ``claim.abort()``,
        an early ``continue``, or a leaked exception) — the placeholder is
        rolled back from memory on exit. SQLite + Redis are not touched
        because the placeholder was never persisted.

    If the vtec_id was already claimed when this context opens (concurrent
    NWWS + IEM trigger racing on the same warning), the new claim becomes a
    no-op: ``__aexit__`` will not clobber the other task's entry, and
    ``confirm`` still persists the real row (last writer wins, matching
    pre-refactor behavior).
    """

    __slots__ = ("_state", "_vtec_id", "_confirmed", "_already_claimed")

    def __init__(self, state: "BotState", vtec_id: str):
        self._state = state
        self._vtec_id = vtec_id
        self._confirmed = False
        self._already_claimed = False

    async def __aenter__(self) -> "PostedWarningClaim":
        if self._vtec_id in self._state.posted_warnings:
            self._already_claimed = True
        else:
            self._state.posted_warnings[self._vtec_id] = {}
        return self

    def abort(self) -> None:
        """Mark the claim for rollback. The placeholder is dropped when
        the context exits. A no-op after ``confirm`` has been called."""
        self._confirmed = False

    async def confirm(
        self,
        message_id: int,
        channel_id: int,
        posted_at: float = 0.0,
        area: str = "",
        tornado_confidence: Optional[str] = None,
        tornado_severity: Optional[str] = None,
        severity: Optional[str] = None,
        raw_text: Optional[str] = None,
    ) -> None:
        """Persist the real entry across memory + SQLite + Redis. After
        this call the claim will not roll back on context exit."""
        await self._state.add_posted_warning(
            self._vtec_id,
            message_id,
            channel_id,
            posted_at,
            area,
            tornado_confidence,
            tornado_severity,
            severity,
            raw_text,
        )
        self._confirmed = True

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._confirmed or self._already_claimed:
            return
        # Only remove the entry if it's still the empty placeholder we
        # inserted on enter. A confirm() that wrote a real dict and then
        # raised — or an unrelated writer — must not be wiped.
        if self._state.posted_warnings.get(self._vtec_id) == {}:
            self._state.posted_warnings.pop(self._vtec_id, None)
