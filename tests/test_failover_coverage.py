"""Coverage-focused tests for `cogs.failover`.

The cog handles HA between two hosts and has three I/O surfaces:
  1. An in-process aiohttp server serving /state and /sync.
  2. Calls out to Upstash Redis via `aiohttp.ClientSession`.
  3. A cloudflared subprocess for the tunnel.

This file covers #1 and #2. For (1) we spin up the real `web.Application`
the cog builds and hit it via an aiohttp client. For (2) we patch
`aiohttp.ClientSession` to hand back mock responses, which is the most
faithful way to test the paths short of running a real Redis.

The cloudflared subprocess lifecycle is out of scope — it requires a
binary that CI doesn't have.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import cogs.failover as failover_module
from cogs.failover import FailoverCog
from utils.state import BotState


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_bot(is_primary: bool = True):
    """Real BotState; MagicMock for discord I/O."""
    bot = MagicMock()
    bot.state = BotState()
    bot.state.is_primary = is_primary
    bot.load_extension = AsyncMock()
    bot.unload_extension = AsyncMock()
    bot.tree = MagicMock()
    bot.tree.sync = AsyncMock(return_value=[])
    return bot


class _FakeResponse:
    """Minimal aiohttp response stand-in."""

    def __init__(self, status: int, payload=None, text: str = ""):
        self.status = status
        self._payload = payload
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


def _fake_session_factory(responder):
    """Build a context-manager mock for `aiohttp.ClientSession`.

    `responder(method, url, kwargs) -> _FakeResponse` chooses the response
    per-call, letting a single fixture script out GET/POST sequences.
    """
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    def _make_call(method):
        def _call(url, **kwargs):
            return responder(method, url, kwargs)
        return _call

    session.get = MagicMock(side_effect=_make_call("GET"))
    session.post = MagicMock(side_effect=_make_call("POST"))
    return session


# ── _handle_get_state / _handle_post_sync — real HTTP roundtrip ─────────────

async def _spin_up_cog_server(cog):
    """Stand up the same aiohttp app the cog builds, via aiohttp's
    TestServer so we can fire real requests at real handlers without
    needing an unused port.
    """
    app = web.Application()
    app.router.add_get("/state", cog._handle_get_state)
    app.router.add_post("/sync", cog._handle_post_sync)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    return client


async def test_get_state_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")
    cog = FailoverCog(_make_bot())
    client = await _spin_up_cog_server(cog)
    try:
        resp = await client.get("/state")
        assert resp.status == 401
    finally:
        await client.close()


async def test_get_state_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")
    cog = FailoverCog(_make_bot())
    client = await _spin_up_cog_server(cog)
    try:
        resp = await client.get(
            "/state", headers={"Authorization": "Bearer nope"}
        )
        assert resp.status == 401
    finally:
        await client.close()


async def test_get_state_returns_serialized_state(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")
    cog = FailoverCog(_make_bot())
    cog.bot.state.posted_mds.add("0100")
    cog.bot.state.iembot_last_seqnum = 42
    client = await _spin_up_cog_server(cog)
    try:
        resp = await client.get(
            "/state", headers={"Authorization": "Bearer secret"}
        )
        assert resp.status == 200
        body = await resp.json()
        assert "0100" in body["posted_mds"]
        assert body["iembot_last_seqnum"] == 42
    finally:
        await client.close()


async def test_post_sync_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")
    cog = FailoverCog(_make_bot())
    client = await _spin_up_cog_server(cog)
    try:
        resp = await client.post(
            "/sync",
            headers={"Authorization": "Bearer nope"},
            json={"iembot_last_seqnum": 1},
        )
        assert resp.status == 401
    finally:
        await client.close()


async def test_post_sync_merges_state(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")
    # set_state is called inside /sync when seqnum advances; stub it.
    monkeypatch.setattr("utils.db.set_state", AsyncMock())

    cog = FailoverCog(_make_bot())
    cog.bot.state.iembot_last_seqnum = 100
    client = await _spin_up_cog_server(cog)
    try:
        resp = await client.post(
            "/sync",
            headers={"Authorization": "Bearer secret"},
            json={
                "posted_mds": ["0500"],
                "posted_watches": ["0042"],
                "auto_cache": {"u": "h"},
                "iembot_last_seqnum": 250,
                "last_posted_urls": {"day1": ["url1"]},
            },
        )
        assert resp.status == 200
    finally:
        await client.close()

    assert "0500" in cog.bot.state.posted_mds
    assert "0042" in cog.bot.state.posted_watches
    assert cog.bot.state.auto_cache == {"u": "h"}
    # seqnum takes max
    assert cog.bot.state.iembot_last_seqnum == 250
    assert cog.bot.state.last_posted_urls["day1"] == ["url1"]


async def test_post_sync_seqnum_does_not_regress(monkeypatch):
    """If the POSTed seqnum is lower than the primary's, keep the
    primary's. Tests the `new_seq > self.bot.state.iembot_last_seqnum`
    guard in the handler."""
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")
    monkeypatch.setattr("utils.db.set_state", AsyncMock())

    cog = FailoverCog(_make_bot())
    cog.bot.state.iembot_last_seqnum = 999
    client = await _spin_up_cog_server(cog)
    try:
        await client.post(
            "/sync",
            headers={"Authorization": "Bearer secret"},
            json={"iembot_last_seqnum": 5},
        )
    finally:
        await client.close()

    assert cog.bot.state.iembot_last_seqnum == 999


# ── Upstash interactions (mocked ClientSession) ─────────────────────────────

async def test_get_primary_url_success(monkeypatch):
    """Upstash GET returns the tunnel URL; method passes it through."""
    def _responder(method, url, kwargs):
        # Upstash REST API: POST {cmd, args} returns {"result": ...}
        return _FakeResponse(200, payload={"result": "https://tunnel.example"})

    fake_session = _fake_session_factory(_responder)
    monkeypatch.setattr(
        "cogs.failover.aiohttp.ClientSession", lambda: fake_session
    )

    cog = FailoverCog(_make_bot(is_primary=False))
    assert await cog._get_primary_url() == "https://tunnel.example"


async def test_get_primary_url_returns_none_on_error(monkeypatch):
    """Network exception → returns None (caller treats as missing)."""
    def _responder(method, url, kwargs):
        raise RuntimeError("network dead")

    fake_session = _fake_session_factory(_responder)
    monkeypatch.setattr(
        "cogs.failover.aiohttp.ClientSession", lambda: fake_session
    )

    cog = FailoverCog(_make_bot(is_primary=False))
    assert await cog._get_primary_url() is None


async def test_write_url_to_upstash_issues_set_with_ttl(monkeypatch):
    """Primary should publish its URL via `SET … EX HEARTBEAT_TTL`."""
    captured = {}

    def _responder(method, url, kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200)

    fake_session = _fake_session_factory(_responder)
    monkeypatch.setattr(
        "cogs.failover.aiohttp.ClientSession", lambda: fake_session
    )

    cog = FailoverCog(_make_bot())
    await cog._write_url_to_upstash("https://tunnel.example")

    assert captured["method"] == "POST"
    cmd = captured["json"]
    assert cmd[0] == "SET"
    assert cmd[1] == "spcbot:primary_url"
    assert cmd[2] == "https://tunnel.example"
    assert cmd[3] == "EX"
    # TTL must match the module-level HEARTBEAT_TTL.
    assert int(cmd[4]) == failover_module.HEARTBEAT_TTL


async def test_upstash_seqnum_roundtrip(monkeypatch):
    """`get_upstash_seqnum` parses int; `write_upstash_seqnum` sends SET."""
    call_log = []

    def _responder(method, url, kwargs):
        cmd = kwargs.get("json")
        call_log.append(cmd)
        if cmd[0] == "GET":
            return _FakeResponse(200, payload={"result": "123"})
        return _FakeResponse(200)

    fake_session = _fake_session_factory(_responder)
    monkeypatch.setattr(
        "cogs.failover.aiohttp.ClientSession", lambda: fake_session
    )

    cog = FailoverCog(_make_bot())
    assert await cog.get_upstash_seqnum() == 123
    await cog.write_upstash_seqnum(456)

    # SET has no EX clause (seqnum is permanent state).
    set_call = [c for c in call_log if c[0] == "SET"][0]
    assert set_call[1] == "spcbot:last_seqnum"
    assert set_call[2] == "456"
    assert "EX" not in set_call


async def test_upstash_seqnum_missing_returns_none(monkeypatch):
    """Missing key → Upstash returns {"result": null} → caller sees None."""
    def _responder(method, url, kwargs):
        return _FakeResponse(200, payload={"result": None})

    fake_session = _fake_session_factory(_responder)
    monkeypatch.setattr(
        "cogs.failover.aiohttp.ClientSession", lambda: fake_session
    )

    cog = FailoverCog(_make_bot())
    assert await cog.get_upstash_seqnum() is None


# ── _standby_cycle / _promote ───────────────────────────────────────────────

async def test_standby_promotes_after_max_failures_missing_primary(monkeypatch):
    """MAX_FAILURES consecutive cycles with no URL in Upstash → promote.

    The threshold is derived from HEARTBEAT_TTL so the test reads it back
    from the module rather than hardcoding a count.
    """
    monkeypatch.setattr(
        FailoverCog, "_get_primary_url", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(FailoverCog, "_start_http_server", AsyncMock())
    monkeypatch.setattr(FailoverCog, "_start_tunnel", AsyncMock())

    cog = FailoverCog(_make_bot(is_primary=False))
    for _ in range(failover_module.MAX_FAILURES):
        await cog._standby_cycle()

    assert cog.bot.state.is_primary is True
    assert cog.bot.load_extension.call_count == len(failover_module.ALL_EXTENSIONS)


async def test_standby_does_not_promote_before_max_failures(monkeypatch):
    """One short of the threshold: must stay in standby."""
    monkeypatch.setattr(
        FailoverCog, "_get_primary_url", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(FailoverCog, "_start_http_server", AsyncMock())
    monkeypatch.setattr(FailoverCog, "_start_tunnel", AsyncMock())

    cog = FailoverCog(_make_bot(is_primary=False))
    for _ in range(failover_module.MAX_FAILURES - 1):
        await cog._standby_cycle()

    assert cog.bot.state.is_primary is False
    cog.bot.load_extension.assert_not_called()


async def test_standby_startup_grace_does_not_count_failures(monkeypatch):
    """Failures during the startup grace window are logged but must NOT
    advance the counter. Covers the "standby deployed before primary"
    race that caused the near-miss in prod."""
    import time as _time

    monkeypatch.setattr(
        FailoverCog, "_get_primary_url", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(FailoverCog, "_start_http_server", AsyncMock())
    monkeypatch.setattr(FailoverCog, "_start_tunnel", AsyncMock())

    cog = FailoverCog(_make_bot(is_primary=False))
    # Simulate cog_load having just been called.
    cog._cog_load_monotonic = _time.monotonic()

    for _ in range(failover_module.MAX_FAILURES + 3):
        await cog._standby_cycle()

    assert cog._primary_failures == 0
    assert cog.bot.state.is_primary is False


async def test_standby_promotes_after_grace_expires(monkeypatch):
    """After the grace window closes, failures count normally again."""
    monkeypatch.setattr(
        FailoverCog, "_get_primary_url", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(FailoverCog, "_start_http_server", AsyncMock())
    monkeypatch.setattr(FailoverCog, "_start_tunnel", AsyncMock())

    cog = FailoverCog(_make_bot(is_primary=False))
    # Pretend cog_load ran well before the grace window — already expired.
    cog._cog_load_monotonic = 0.0

    for _ in range(failover_module.MAX_FAILURES):
        await cog._standby_cycle()

    assert cog.bot.state.is_primary is True


async def test_standby_tracks_last_seen_primary_url(monkeypatch):
    """Verifies `_last_seen_primary_url` is kept in sync with Upstash —
    the actual counter-reset behaviour on URL change is exercised by
    `test_standby_clears_prior_failures_when_heartbeat_returns` via
    the None → present transition.
    """
    urls = iter([
        "https://old.example",
        "https://new.example",
    ])

    async def _next_url(self_):
        return next(urls)

    monkeypatch.setattr(FailoverCog, "_get_primary_url", _next_url)
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")

    # Hydration is irrelevant to this test — make it a no-op 200.
    def _ok(method, url, kwargs):
        return _FakeResponse(200, payload={})

    monkeypatch.setattr(
        "cogs.failover.aiohttp.ClientSession",
        lambda: _fake_session_factory(_ok),
    )

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = 0.0

    await cog._standby_cycle()
    assert cog._last_seen_primary_url == "https://old.example"
    await cog._standby_cycle()
    assert cog._last_seen_primary_url == "https://new.example"


async def test_standby_hydrates_when_primary_reachable(monkeypatch):
    """Primary URL found, /state returns 200 with state payload → hydrate."""
    monkeypatch.setattr(
        FailoverCog,
        "_get_primary_url",
        AsyncMock(return_value="https://tunnel.example"),
    )
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")

    state_payload = {
        "iembot_last_seqnum": 10,
        "posted_mds": ["0001"],
        "posted_watches": [],
        "auto_cache": {},
        "last_posted_urls": {},
    }

    def _responder(method, url, kwargs):
        assert url == "https://tunnel.example/state"
        return _FakeResponse(200, payload=state_payload)

    fake_session = _fake_session_factory(_responder)
    monkeypatch.setattr(
        "cogs.failover.aiohttp.ClientSession", lambda: fake_session
    )

    # _persist_hydrated_state touches the real DB — stub it.
    monkeypatch.setattr(
        FailoverCog, "_persist_hydrated_state", AsyncMock()
    )

    cog = FailoverCog(_make_bot(is_primary=False))
    # Seed prior failures so we can verify reset to 0.
    cog._primary_failures = 2

    await cog._standby_cycle()

    assert cog.bot.state.iembot_last_seqnum == 10
    assert "0001" in cog.bot.state.posted_mds
    assert cog._primary_failures == 0


async def test_standby_hydration_failure_does_not_count_when_heartbeat_fresh(monkeypatch):
    """If Upstash still has the primary URL (primary heartbeat fresh)
    but /state is unreachable, this is a hydration problem, not a
    primary-death signal. Must NOT advance `_primary_failures`.

    This is the fix for the false-promotion race that fired in prod
    when the standby could not resolve the trycloudflare.com tunnel
    hostname while the primary was fully healthy.
    """
    monkeypatch.setattr(
        FailoverCog,
        "_get_primary_url",
        AsyncMock(return_value="https://tunnel.example"),
    )
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")

    def _500(method, url, kwargs):
        return _FakeResponse(500, text="boom")

    def _raise(method, url, kwargs):
        raise RuntimeError("Name or service not known")

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = 0.0  # past startup grace

    # 5xx response: heartbeat is fresh → no counter advance.
    monkeypatch.setattr(
        "cogs.failover.aiohttp.ClientSession",
        lambda: _fake_session_factory(_500),
    )
    for _ in range(failover_module.MAX_FAILURES + 3):
        await cog._standby_cycle()
    assert cog._primary_failures == 0
    assert cog.bot.state.is_primary is False

    # Connection exception: still fresh → still no counter advance.
    monkeypatch.setattr(
        "cogs.failover.aiohttp.ClientSession",
        lambda: _fake_session_factory(_raise),
    )
    for _ in range(failover_module.MAX_FAILURES + 3):
        await cog._standby_cycle()
    assert cog._primary_failures == 0
    assert cog.bot.state.is_primary is False


async def test_standby_clears_prior_failures_when_heartbeat_returns(monkeypatch):
    """After the key goes missing (counter climbs) and then returns
    (primary recovered, republished), the counter must reset even if
    hydration keeps failing afterwards."""
    states = iter([None, None, "https://tunnel.example", "https://tunnel.example"])

    async def _next_url(self_):
        return next(states)

    monkeypatch.setattr(FailoverCog, "_get_primary_url", _next_url)
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")

    # Hydration always errors — exercises the "heartbeat fresh but
    # hydration dead" path that used to mistakenly increment.
    monkeypatch.setattr(
        "cogs.failover.aiohttp.ClientSession",
        lambda: _fake_session_factory(
            lambda m, u, k: (_ for _ in ()).throw(RuntimeError("x"))
        ),
    )

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = 0.0

    await cog._standby_cycle()  # None: liveness fail #1
    await cog._standby_cycle()  # None: liveness fail #2
    assert cog._primary_failures == 2

    await cog._standby_cycle()  # URL returns → counter cleared
    assert cog._primary_failures == 0
    await cog._standby_cycle()  # URL present, hydration still failing → still 0
    assert cog._primary_failures == 0


# ── _check_for_demotion / _demote ───────────────────────────────────────────

async def test_check_for_demotion_triggers_when_new_primary_elsewhere(monkeypatch):
    """Acting-primary sees a different URL in Upstash → demote."""
    cog = FailoverCog(_make_bot(is_primary=True))
    cog._tunnel_url = "https://mine.example"

    monkeypatch.setattr(
        FailoverCog,
        "_get_primary_url",
        AsyncMock(return_value="https://someone-else.example"),
    )
    demoted = AsyncMock()
    monkeypatch.setattr(FailoverCog, "_demote", demoted)

    await cog._check_for_demotion()
    demoted.assert_awaited_once_with("https://someone-else.example")


async def test_check_for_demotion_no_op_when_url_matches(monkeypatch):
    cog = FailoverCog(_make_bot(is_primary=True))
    cog._tunnel_url = "https://mine.example"

    monkeypatch.setattr(
        FailoverCog,
        "_get_primary_url",
        AsyncMock(return_value="https://mine.example"),
    )
    demoted = AsyncMock()
    monkeypatch.setattr(FailoverCog, "_demote", demoted)

    await cog._check_for_demotion()
    demoted.assert_not_awaited()


async def test_demote_pushes_state_and_flips_flag(monkeypatch):
    """Demotion must POST /sync to the new primary and set
    is_primary=False, then unload every extension."""
    cog = FailoverCog(_make_bot(is_primary=True))
    cog.bot.state.posted_mds.add("0042")

    # Capture the POST payload sent to the new primary.
    captured = {}
    fake_http_session = MagicMock()

    async def _post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(200)

    fake_http_session.post = _post
    monkeypatch.setattr(
        "utils.http.ensure_session",
        AsyncMock(return_value=fake_http_session),
    )
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "secret")

    await cog._demote("https://new-primary.example")

    assert captured["url"] == "https://new-primary.example/sync"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert "0042" in captured["json"]["posted_mds"]

    assert cog.bot.state.is_primary is False
    assert cog.bot.unload_extension.call_count == len(failover_module.ALL_EXTENSIONS)


# ── Fail-fast token guard (PR #94) ──────────────────────────────────────────

def test_require_failover_token_rejects_empty(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "")
    with pytest.raises(RuntimeError, match="FAILOVER_TOKEN"):
        failover_module._require_failover_token()


def test_require_failover_token_rejects_default_placeholder(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "changeme")
    with pytest.raises(RuntimeError, match="FAILOVER_TOKEN"):
        failover_module._require_failover_token()


def test_require_failover_token_accepts_real_value(monkeypatch):
    monkeypatch.setattr(failover_module, "FAILOVER_TOKEN", "real-secret-xyz")
    assert failover_module._require_failover_token() == "real-secret-xyz"
