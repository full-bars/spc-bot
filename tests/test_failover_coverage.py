"""Coverage tests for the simplified failover cog.

Post-v5 architecture: the cog no longer ships state over HTTP. Its
whole job is leader election via an Upstash lease key. These tests
cover:

  1. Lease-writing / -reading / -releasing against a mock Upstash.
  2. Standby cycle: holder present → counter resets; holder missing →
     counter advances subject to the startup grace window and MAX_FAILURES.
  3. Primary cycle: still our lease → refresh; someone else's lease →
     demote.
  4. Promotion: is_primary flips, cache invalidates, resync runs, cogs
     load.
  5. Demotion: is_primary flips back, cogs unload, counter clears.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

import cogs.failover as failover_module
from cogs.failover import FailoverCog
from utils.state import BotState


@pytest.fixture(autouse=True)
def _isolate_hostname(monkeypatch):
    """Pin ``socket.gethostname`` so the bare-hostname fallback in
    ``_is_our_node`` cannot accidentally match a string used as the
    "other node" in a test scenario. Without this, tests that hardcode
    a node name (``"ubunt-server"``, ``"3cape"``, etc.) invert when run
    on a host whose actual hostname happens to be that string.
    """
    monkeypatch.setattr(
        failover_module.socket, "gethostname", lambda: "_pytest_no_match"
    )


def _make_bot(is_primary: bool = True):
    bot = MagicMock()
    bot.state = BotState()
    bot.state.is_primary = is_primary
    bot.load_extension = AsyncMock()
    bot.unload_extension = AsyncMock()
    bot.tree = MagicMock()
    bot.tree.sync = AsyncMock(return_value=[])
    return bot


def _stub_upstash(responses: dict | None = None, default=None):
    """Build an AsyncMock that answers `_upstash` by command keyword.

    `responses` is {command_string: value_to_return}. Unmatched commands
    return `default`.
    """
    responses = responses or {}
    calls: list = []

    async def _resp(*args):
        calls.append(args)
        if not args:
            return default
        return responses.get(args[0], default)

    mock = AsyncMock(side_effect=_resp)
    mock.calls = calls
    return mock


# ── Lease-writing / -reading / -releasing ───────────────────────────────────

async def test_write_lease_uses_set_with_ttl(monkeypatch):
    mock = _stub_upstash()
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    cog = FailoverCog(_make_bot())

    await cog._write_lease()

    assert mock.calls, "expected a call"
    args = mock.calls[0]
    assert args[0] == "SET"
    assert args[1] == failover_module.LEASE_KEY
    assert args[2] == cog._identity
    assert args[3] == "EX"
    assert int(args[4]) == failover_module.HEARTBEAT_TTL


async def test_read_lease_returns_holder(monkeypatch):
    mock = _stub_upstash({"GET": "some-host:abcd1234"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    cog = FailoverCog(_make_bot(is_primary=False))

    assert await cog._read_lease_holder() == "some-host:abcd1234"


async def test_release_lease_only_deletes_if_ours(monkeypatch):
    """Never DEL someone else's lease — that would strand the cluster."""
    cog = FailoverCog(_make_bot())
    cog._identity = "me"

    mock = _stub_upstash({"GET": "someone-else"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    await cog._release_lease()
    # No DEL should have been issued.
    del_calls = [c for c in mock.calls if c and c[0] == "DEL"]
    assert del_calls == []


async def test_release_lease_deletes_when_ours(monkeypatch):
    cog = FailoverCog(_make_bot())
    cog._identity = "me"

    mock = _stub_upstash({"GET": "me"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    await cog._release_lease()
    del_calls = [c for c in mock.calls if c and c[0] == "DEL"]
    assert del_calls and del_calls[0][1] == failover_module.LEASE_KEY


# ── Fail-fast token guard ───────────────────────────────────────────────────

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


# ── Standby cycle ───────────────────────────────────────────────────────────

async def test_standby_resets_failures_when_lease_present(monkeypatch):
    mock = _stub_upstash({"GET": "holder"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._primary_failures = 3

    await cog._standby_cycle()
    assert cog._primary_failures == 0
    assert cog.bot.state.is_primary is False


async def test_standby_increments_failures_when_lease_missing(monkeypatch):
    mock = _stub_upstash({"GET": None})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = __import__("time").monotonic() - failover_module.STARTUP_GRACE_SECONDS - 10  # past grace

    await cog._standby_cycle()
    assert cog._primary_failures == 1
    assert cog.bot.state.is_primary is False


async def test_standby_startup_grace_does_not_count(monkeypatch):
    import time as _time

    mock = _stub_upstash({"GET": None})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = _time.monotonic()  # grace window open

    for _ in range(failover_module.MAX_FAILURES + 3):
        await cog._standby_cycle()
    assert cog._primary_failures == 0


async def test_standby_promotes_after_max_failures(monkeypatch):
    """MAX_FAILURES consecutive missing-lease cycles → promote."""
    mock = _stub_upstash({"GET": None})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    monkeypatch.setattr(FailoverCog, "_promote", AsyncMock())

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = __import__("time").monotonic() - failover_module.STARTUP_GRACE_SECONDS - 10  # past grace

    for _ in range(failover_module.MAX_FAILURES):
        await cog._standby_cycle()

    FailoverCog._promote.assert_awaited_once()


async def test_standby_does_not_promote_one_short_of_threshold(monkeypatch):
    mock = _stub_upstash({"GET": None})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    monkeypatch.setattr(FailoverCog, "_promote", AsyncMock())

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._cog_load_monotonic = __import__("time").monotonic() - failover_module.STARTUP_GRACE_SECONDS - 10  # past grace

    for _ in range(failover_module.MAX_FAILURES - 1):
        await cog._standby_cycle()

    FailoverCog._promote.assert_not_awaited()


# ── Primary cycle ───────────────────────────────────────────────────────────

async def test_primary_refreshes_own_lease(monkeypatch):
    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "me"

    mock = _stub_upstash({"GET": "me"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    await cog._primary_cycle()
    # Saw GET, then SET (refresh).
    cmds = [c[0] for c in mock.calls]
    assert cmds[0] == "GET"
    assert "SET" in cmds


async def test_primary_demotes_when_other_holder_wins(monkeypatch):
    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "me"

    mock = _stub_upstash({"GET": "other-holder"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    monkeypatch.setattr(FailoverCog, "_demote", AsyncMock())

    await cog._primary_cycle()
    FailoverCog._demote.assert_awaited_once()


async def test_primary_reclaims_expired_lease_with_nx(monkeypatch):
    """When read returns None (key missing), primary uses SET NX to reclaim."""
    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "me"

    # First GET returns None (key expired), SET NX returns "OK", no re-read needed.
    responses = {"GET": None, "SET": "OK"}
    mock = _stub_upstash(responses)
    monkeypatch.setattr(FailoverCog, "_upstash", mock)
    monkeypatch.setattr(FailoverCog, "_demote", AsyncMock())

    await cog._primary_cycle()

    # Must NOT have demoted.
    FailoverCog._demote.assert_not_awaited()
    # SET call must include NX flag.
    set_calls = [c for c in mock.calls if c[0] == "SET"]
    assert set_calls, "expected a SET call"
    assert "NX" in set_calls[0], "SET must use NX when read returned None"


async def test_primary_demotes_when_nx_write_blocked_by_standby(monkeypatch):
    """When read returns None but NX write fails (standby holds key), primary demotes."""
    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "me"

    call_count = {"n": 0}

    async def _resp(*args):
        cmd = args[0] if args else ""
        if cmd == "GET":
            call_count["n"] += 1
            # First read (holder check): None — key looks missing.
            # Second read (post-NX re-check): standby is now visible.
            return None if call_count["n"] == 1 else "S:standby:abc"
        if cmd == "SET":
            return None  # NX write blocked — key exists
        return None

    monkeypatch.setattr(FailoverCog, "_upstash", AsyncMock(side_effect=_resp))
    monkeypatch.setattr(FailoverCog, "_demote", AsyncMock())

    await cog._primary_cycle()

    FailoverCog._demote.assert_awaited_once()


# ── Promotion / demotion ────────────────────────────────────────────────────

async def test_promote_sets_flag_invalidates_cache_loads_cogs(monkeypatch):
    from utils import state_store

    # Seed a cache entry so we can verify invalidation.
    state_store._cache["sentinel"] = state_store._CacheEntry("x", 1e9)

    monkeypatch.setattr(FailoverCog, "_upstash", _stub_upstash({"GET": None}))
    monkeypatch.setattr(
        state_store, "resync_to_upstash", AsyncMock(return_value={})
    )

    cog = FailoverCog(_make_bot(is_primary=False))
    await cog._promote()

    assert cog.bot.state.is_primary is True
    assert "sentinel" not in state_store._cache
    assert cog.bot.load_extension.call_count == len(failover_module.ALL_EXTENSIONS)


async def test_demote_flips_flag_unloads_cogs(monkeypatch):
    cog = FailoverCog(_make_bot(is_primary=True))
    cog._primary_failures = 4

    await cog._demote()

    assert cog.bot.state.is_primary is False
    assert cog.bot.unload_extension.call_count == len(failover_module.ALL_EXTENSIONS)
    assert cog._primary_failures == 0


# ── Startup lease check (boot-time race prevention) ─────────────────────────
#
# These tests cover the window that bit us in prod on 2026-04-23: the
# rebooting primary loaded cogs as primary based purely on the IS_PRIMARY
# env var, fired its first MD-scan task, and posted a duplicate ~13s
# before the failover sync_loop's first tick detected another node owned
# the lease and demoted. `startup_lease_check` is the synchronous probe
# that now runs inside setup_hook, before the other cogs are loaded.


async def test_startup_lease_free_primary_configured_claims_lease(monkeypatch):
    """Lease free + IS_PRIMARY=true → claim it and boot as primary."""
    mock = _stub_upstash({"GET": None})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "me"

    result = await cog.startup_lease_check()

    assert result is True
    assert cog.bot.state.is_primary is True
    sets = [c for c in mock.calls if c[0] == "SET" and c[1] == failover_module.LEASE_KEY]
    assert sets, "expected lease SET after claiming"
    assert sets[0][2] == "me"


async def test_startup_lease_held_by_us_keeps_primary(monkeypatch):
    """Same node rebooted quickly — TTL hasn't expired yet. Still ours."""
    mock = _stub_upstash({"GET": "me"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "me"

    result = await cog.startup_lease_check()

    assert result is True
    assert cog.bot.state.is_primary is True


async def test_startup_lease_held_by_other_forces_standby(monkeypatch):
    """The exact scenario from the 2026-04-23 incident: 3cape held the
    lease via manual override, ubunt-server came back with IS_PRIMARY=true
    and must NOT load cogs."""
    mock = _stub_upstash({"GET": "3cape"})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "ubunt-server"

    result = await cog.startup_lease_check()

    assert result is False
    assert cog.bot.state.is_primary is False
    # And critically, we did NOT overwrite the lease.
    set_lease = [
        c for c in mock.calls
        if c[0] == "SET" and c[1] == failover_module.LEASE_KEY
    ]
    assert set_lease == [], "must not clobber another node's lease on boot"


async def test_startup_manual_override_for_us_beats_lease(monkeypatch):
    """/failover set to us: claim the lease even if someone else holds it."""
    # This matches the sync_loop manual-override semantics so the boot path
    # doesn't have different rules than steady-state.
    _stub_upstash({
        "GET": None,  # catch-all; responses dict matches by command name
    })

    # Custom per-key response — the stub helper only keys by command, so
    # we roll our own for this case.
    calls = []

    async def upstash(*args):
        calls.append(args)
        if args[0] == "GET" and args[1] == "spcbot:manual_primary":
            return "me"
        if args[0] == "GET" and args[1] == failover_module.LEASE_KEY:
            return "someone-else"
        return None

    monkeypatch.setattr(FailoverCog, "_upstash", AsyncMock(side_effect=upstash))

    cog = FailoverCog(_make_bot(is_primary=False))  # env says standby
    cog._identity = "me"

    result = await cog.startup_lease_check()

    assert result is True
    assert cog.bot.state.is_primary is True
    set_lease = [
        c for c in calls
        if c[0] == "SET" and c[1] == failover_module.LEASE_KEY
    ]
    assert set_lease, "manual override for us should claim the lease"


async def test_startup_manual_override_for_other_forces_standby(monkeypatch):
    """/failover set to another node: stay standby even if we're the
    configured primary and the lease appears free."""
    calls = []

    async def upstash(*args):
        calls.append(args)
        if args[0] == "GET" and args[1] == "spcbot:manual_primary":
            return "3cape"
        if args[0] == "GET" and args[1] == failover_module.LEASE_KEY:
            return None  # lease even looks free
        return None

    monkeypatch.setattr(FailoverCog, "_upstash", AsyncMock(side_effect=upstash))

    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "ubunt-server"

    result = await cog.startup_lease_check()

    assert result is False
    assert cog.bot.state.is_primary is False
    # Must not have written the lease — would steal from the override target.
    set_lease = [
        c for c in calls
        if c[0] == "SET" and c[1] == failover_module.LEASE_KEY
    ]
    assert set_lease == []


async def test_startup_lease_free_standby_configured_stays_standby(monkeypatch):
    """Dedicated standby (IS_PRIMARY=false) must not grab a free lease on
    boot — the live primary might just be between heartbeats. The
    sync_loop will still promote after MAX_FAILURES if the primary is
    actually gone."""
    mock = _stub_upstash({"GET": None})
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=False))
    cog._identity = "3cape"

    result = await cog.startup_lease_check()

    assert result is False
    assert cog.bot.state.is_primary is False
    set_lease = [
        c for c in mock.calls
        if c[0] == "SET" and c[1] == failover_module.LEASE_KEY
    ]
    assert set_lease == []


async def test_startup_upstash_unreachable_falls_back_to_env(monkeypatch):
    """If Upstash is hosed, _upstash returns None for every call. Treat
    that as 'lease is free' would clobber whatever holder exists when
    Upstash recovers — so both nodes could end up claiming. The boot
    path instead trusts IS_PRIMARY=true and writes its own lease, which
    is the old (pre-fix) behavior and is acceptable because without
    Upstash coordination is impossible anyway. The test here just pins
    the current contract so changes to it are intentional."""
    mock = _stub_upstash({})  # everything returns None
    monkeypatch.setattr(FailoverCog, "_upstash", mock)

    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "me"

    result = await cog.startup_lease_check()

    # With IS_PRIMARY=true and nothing in Upstash (looks free), we claim.
    assert result is True
    assert cog.bot.state.is_primary is True


# ── Incident replay: the 2026-04-23 manual-override-clear sequence ─────────


async def test_incident_primary_reboot_during_manual_override_stays_standby(
    monkeypatch,
):
    """Reconstruct the exact failure mode:

      1. User ran /failover 3cape → spcbot:manual_primary = '3cape'.
      2. 3cape held the lease.
      3. ubunt-server (the "real" primary, IS_PRIMARY=true) rebooted.

    Pre-fix: ubunt-server loaded cogs immediately, posted dup MDs/watches,
    demoted ~30s later. Post-fix: ubunt-server must see the manual
    override and stay standby, no cogs loaded."""

    async def upstash(*args):
        if args[0] == "GET" and args[1] == "spcbot:manual_primary":
            return "3cape"
        if args[0] == "GET" and args[1] == failover_module.LEASE_KEY:
            return "3cape"
        return None

    monkeypatch.setattr(FailoverCog, "_upstash", AsyncMock(side_effect=upstash))

    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "ubunt-server"

    should_run_primary = await cog.startup_lease_check()

    assert should_run_primary is False
    assert cog.bot.state.is_primary is False


async def test_incident_after_override_cleared_standby_until_lease_released(
    monkeypatch,
):
    """Next beat of the incident: the user cleared the override while
    3cape still held the lease. A restart of ubunt-server at this moment
    must still stay standby — the lease holder is authoritative, env
    var is not."""

    async def upstash(*args):
        if args[0] == "GET" and args[1] == "spcbot:manual_primary":
            return None  # override cleared
        if args[0] == "GET" and args[1] == failover_module.LEASE_KEY:
            return "3cape"  # but 3cape still heartbeating
        return None

    monkeypatch.setattr(FailoverCog, "_upstash", AsyncMock(side_effect=upstash))

    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "ubunt-server"

    should_run_primary = await cog.startup_lease_check()

    assert should_run_primary is False
    assert cog.bot.state.is_primary is False


async def test_incident_after_3cape_releases_primary_boots_clean(monkeypatch):
    """Final beat: override cleared, 3cape shut down and released the
    lease. ubunt-server now boots → sees no manual, no lease holder,
    IS_PRIMARY=true → claims the lease and loads cogs. This is the
    only beat in the sequence where cogs should load."""
    calls = []

    async def upstash(*args):
        calls.append(args)
        if args[0] == "GET" and args[1] == "spcbot:manual_primary":
            return None
        if args[0] == "GET" and args[1] == failover_module.LEASE_KEY:
            return None
        return None

    monkeypatch.setattr(FailoverCog, "_upstash", AsyncMock(side_effect=upstash))

    cog = FailoverCog(_make_bot(is_primary=True))
    cog._identity = "ubunt-server"

    should_run_primary = await cog.startup_lease_check()

    assert should_run_primary is True
    assert cog.bot.state.is_primary is True
    assert any(
        c[0] == "SET" and c[1] == failover_module.LEASE_KEY and c[2] == "ubunt-server"
        for c in calls
    ), "primary should claim lease once it's free"


# ── Rehydrate pulls csu_posted from Upstash ─────────────────────────────────


async def test_rehydrate_pulls_csu_posted_for_today(monkeypatch):
    """Promotion must refresh csu_posted so we don't re-post CSU-MLP
    panels the outgoing primary already handled this UTC day."""
    from datetime import datetime, timezone
    from utils import state_store

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    csu_payload = f'{{"date": "{today}", "days": ["1", "2", "3", "hazard_1_2"]}}'

    async def fake_get_state(key):
        if key == "csu_mlp_posted":
            return csu_payload
        return None

    monkeypatch.setattr(state_store, "get_all_hashes", AsyncMock(return_value={}))
    monkeypatch.setattr(state_store, "get_posted_mds", AsyncMock(return_value=set()))
    monkeypatch.setattr(state_store, "get_posted_watches", AsyncMock(return_value=set()))
    monkeypatch.setattr(state_store, "get_state", AsyncMock(side_effect=fake_get_state))
    monkeypatch.setattr(state_store, "get_posted_urls", AsyncMock(return_value=[]))

    cog = FailoverCog(_make_bot(is_primary=False))
    await cog._rehydrate_bot_state()

    assert cog.bot.state.csu_posted == {"1", "2", "3", "hazard_1_2"}


async def test_rehydrate_ignores_stale_csu_posted_from_yesterday(monkeypatch):
    """If the Upstash blob is from a previous UTC day, don't load it —
    today's posts haven't happened yet and loading yesterday's set
    would suppress them."""
    from utils import state_store

    stale_payload = '{"date": "1999-01-01", "days": ["1", "2", "3"]}'

    async def fake_get_state(key):
        if key == "csu_mlp_posted":
            return stale_payload
        return None

    monkeypatch.setattr(state_store, "get_all_hashes", AsyncMock(return_value={}))
    monkeypatch.setattr(state_store, "get_posted_mds", AsyncMock(return_value=set()))
    monkeypatch.setattr(state_store, "get_posted_watches", AsyncMock(return_value=set()))
    monkeypatch.setattr(state_store, "get_state", AsyncMock(side_effect=fake_get_state))
    monkeypatch.setattr(state_store, "get_posted_urls", AsyncMock(return_value=[]))

    cog = FailoverCog(_make_bot(is_primary=False))
    cog.bot.state.csu_posted.clear()
    await cog._rehydrate_bot_state()

    assert cog.bot.state.csu_posted == set()


async def test_rehydrate_tolerates_malformed_csu_state(monkeypatch):
    """A garbled csu_mlp_posted blob must not break promotion."""
    from utils import state_store

    async def fake_get_state(key):
        if key == "csu_mlp_posted":
            return "{not valid json"
        return None

    monkeypatch.setattr(state_store, "get_all_hashes", AsyncMock(return_value={}))
    monkeypatch.setattr(state_store, "get_posted_mds", AsyncMock(return_value=set()))
    monkeypatch.setattr(state_store, "get_posted_watches", AsyncMock(return_value=set()))
    monkeypatch.setattr(state_store, "get_state", AsyncMock(side_effect=fake_get_state))
    monkeypatch.setattr(state_store, "get_posted_urls", AsyncMock(return_value=[]))

    cog = FailoverCog(_make_bot(is_primary=False))
    # Should not raise.
    await cog._rehydrate_bot_state()
