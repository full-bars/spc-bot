"""Coverage round 6: status cog — MD paginator view, cluster status, status embeds.

Pure-logic + mocked-Redis/HTTP tests for cogs/status.py (42% covered):
the MD paginator build/buttons, the Redis cluster-status parser, and the
/status embed builder paths (role/color, warning counts, severity lines,
AI metrics, recent activity, task details).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from cogs import status


# ── MD paginator view ────────────────────────────────────────────────────────


def _md_data():
    return [
        {
            "num": "1234",
            "raw_text": "MESOSCALE DISCUSSION 1234\nSome body text",
            "from_cache": False,
            "cache_path": None,
        },
        {
            "num": "0567",
            "raw_text": "MESOSCALE DISCUSSION 567\nOther body",
            "from_cache": True,
            "cache_path": "/tmp/md_0567.png",
        },
    ]


def _paginator():
    return status.MDPaginatorView(MagicMock(), MagicMock(), _md_data())


def test_md_paginator_build_response_first_page():
    view = _paginator()
    content, embeds, files = view.build_response()

    assert content is None
    assert len(embeds) == 2
    assert "1234" in embeds[0].title
    assert "MESOSCALE DISCUSSION 1234" in embeds[1].description
    assert embeds[1].footer.text == "MD 1 of 2"
    assert files == []


def test_md_paginator_build_response_cache_path(tmp_path):
    img = tmp_path / "md_0567.png"
    img.write_bytes(b"PNGDATA")
    data = _md_data()
    data[1]["cache_path"] = str(img)
    view = status.MDPaginatorView(MagicMock(), MagicMock(), data)
    view.index = 1
    content, embeds, files = view.build_response()

    assert len(files) == 1
    assert embeds[0].image.url == "attachment://md_0567.png"
    assert "⚠️ SPC website unreachable" in embeds[1].footer.text
    assert embeds[1].footer.text.endswith("MD 2 of 2")


def test_md_paginator_update_buttons():
    view = _paginator()
    view.index = 0
    view._update_buttons()
    assert view.prev_btn.disabled is True
    assert view.next_btn.disabled is False

    view.index = 1
    view._update_buttons()
    assert view.prev_btn.disabled is False
    assert view.next_btn.disabled is True


async def test_md_paginator_next_prev_edit_message():
    view = _paginator()
    view.md_data[1]["cache_path"] = None  # avoid discord.File IO in this test
    interaction = MagicMock()
    interaction.response = AsyncMock()

    await view.next_btn.callback(interaction)
    assert view.index == 1
    interaction.response.edit_message.assert_awaited_once()

    interaction.response.reset_mock()
    await view.prev_btn.callback(interaction)
    assert view.index == 0
    interaction.response.edit_message.assert_awaited_once()


async def test_md_paginator_tldr_without_cog():
    view = _paginator()
    view.bot.get_cog.return_value = None
    interaction = MagicMock()
    interaction.response = AsyncMock()

    await view.tldr_btn.callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    assert (
        "AI features not currently available"
        in interaction.response.send_message.await_args.args[0]
    )


async def test_md_paginator_on_timeout_disables():
    view = _paginator()
    view.message = MagicMock()
    view.message.edit = AsyncMock()

    await view.on_timeout()

    assert all(item.disabled for item in view.children)
    view.message.edit.assert_awaited_once()


# ── Cluster status parsing ───────────────────────────────────────────────────


def _status_view(bot):
    view = status.StatusView.__new__(status.StatusView)
    view.bot = bot
    view.interaction = MagicMock()
    view.message = None
    view.detailed = False
    view.should_update = True
    return view


async def test_cluster_status_parses_nodes():
    bot = MagicMock()
    bot.state.failover_count = 2
    bot.state.lease_renewals = 0
    bot.state.sync_failures = 0
    view = _status_view(bot)
    redis = AsyncMock()
    now = int(datetime.now(timezone.utc).timestamp())
    redis.hgetall.return_value = {
        "P:box-a:uuid1": str(now),
        "S:box-b:uuid2": str(now - 10),
        "S:box-c:stale": str(now - 5000),  # stale -> skipped
        "broken": "not-a-number",  # parse error
    }
    redis.get.return_value = "P:box-a:uuid1"

    with patch("utils.state_store._get_redis_client", return_value=redis):
        text = await view._get_cluster_status()

    assert "🟢 PRIMARY" in text
    assert "box-a" in text
    assert "(holds lease)" in text
    assert "🟡 STANDBY" in text
    assert "box-b" in text
    assert "box-c" not in text
    assert "parse error" in text
    assert "Failovers: `2`" in text


async def test_cluster_status_no_nodes():
    view = _status_view(MagicMock())
    redis = AsyncMock()
    redis.hgetall.return_value = {}
    redis.get.return_value = None

    with patch("utils.state_store._get_redis_client", return_value=redis):
        text = await view._get_cluster_status()

    assert text == "*(No nodes registered)*"


async def test_cluster_status_failover_primary():
    bot = MagicMock()
    bot.state.failover_count = 0
    bot.state.lease_renewals = 0
    bot.state.sync_failures = 0
    view = _status_view(bot)
    redis = AsyncMock()
    now = int(datetime.now(timezone.utc).timestamp())
    # A standby-configured node holding the lease shows as failover primary.
    redis.hgetall.return_value = {"S:box-a:uuid1": str(now)}
    redis.get.return_value = "S:box-a:uuid1"

    with patch("utils.state_store._get_redis_client", return_value=redis):
        text = await view._get_cluster_status()

    assert "🟠 PRIMARY ⚠️ FAILOVER" in text


async def test_cluster_status_redis_unavailable():
    view = _status_view(MagicMock())

    async def _boom(*a, **kw):
        raise RuntimeError("redis down")

    with patch("utils.state_store._get_redis_client", side_effect=_boom):
        text = await view._get_cluster_status()

    assert text == "*(Redis unavailable)*"


# ── Status embed builder ─────────────────────────────────────────────────────


def _bot_with_state():
    from utils.state import BotState

    bot = MagicMock()
    bot.state = BotState()
    bot.state.bot_start_time = datetime.now(timezone.utc) - timedelta(hours=2)
    bot.state.active_warnings = {
        "KOUN.TO.W.0001": {"phenom": "TO"},
        "KOUN.SV.W.0002": {"phenom": "SV"},
        "KOUN.SV.W.0003": {"phenom": "SV"},
    }
    bot.state.active_mds = {"1234"}
    bot.state.active_watches = {"W0001": {"type": "SVR"}}
    bot.state.last_post_times = {"warnings": datetime.now(timezone.utc) - timedelta(minutes=5)}
    bot.cogs = {}
    bot.latency = 0.1
    return bot


async def test_build_embeds_primary_role():
    bot = _bot_with_state()
    view = _status_view(bot)
    session = MagicMock()
    resp = MagicMock()
    resp.status = 200
    resp.text = AsyncMock(return_value="1.2.3.4")
    session.get.return_value.__aenter__ = AsyncMock(return_value=resp)
    with patch(
        "cogs.status._http.ensure_session", new_callable=AsyncMock, return_value=session
    ), patch("cogs.status.get_high_risk_polygon", new_callable=AsyncMock), patch(
        "cogs.status.get_current_risk_display", return_value="SLGT"
    ), patch("cogs.status.peek_active_labels", return_value=None), patch(
        "cogs.status.get_write_failure_count", return_value=0
    ), patch("utils.db.get_warning_stats", new_callable=AsyncMock) as mock_stats, patch(
        "utils.state_store._get_redis_client", return_value=AsyncMock()
    ), patch("config.OPENCODE_API_KEY", "key"), patch("config.GEMINI_API_KEY", ""):
        mock_stats.return_value = {
            "tor": {"total": 1, "standard": 1, "pds": 0, "emergency": 0},
            "svr": {"total": 2, "standard": 2, "considerable": 0, "destructive": 0},
            "ffw": {"total": 0, "standard": 0, "considerable": 0, "emergency": 0},
        }

        embeds = await view.build_embeds()

    assert len(embeds) == 1
    embed = embeds[0]
    assert "PRIMARY" in embed.description
    assert embed.color == discord.Color.green()
    fields = {f.name: f.value for f in embed.fields}
    assert "🖥️ System" in fields
    assert "`1.2.3.4`" in fields["🖥️ System"]
    assert "📡 Connectivity" in fields
    assert "🌩️ Environment" in fields
    assert "Active Warnings" in fields["🌩️ Environment"]
    assert "⛈️ SVR `2`" in fields["🌩️ Environment"]
    assert "📋 Warning Labels (6h)" in fields
    assert "🌪️" in fields["📋 Warning Labels (6h)"]
    assert "🧠 AI Subsystem" in fields
    assert "🟢 ACTIVE" in fields["🧠 AI Subsystem"]
    assert "🔄 Recent Activity" in fields
    assert "Live Auto-refresh" in embed.footer.text


async def test_build_embeds_failover_role_and_circuits():
    bot = _bot_with_state()
    bot.state.is_primary = True
    view = _status_view(bot)
    session = MagicMock()
    session.get.return_value.__aenter__ = AsyncMock(return_value=MagicMock(status=500))
    with patch(
        "cogs.status._http.ensure_session", new_callable=AsyncMock, return_value=session
    ), patch("cogs.status.get_high_risk_polygon", new_callable=AsyncMock), patch(
        "cogs.status.get_current_risk_display", return_value="SLGT"
    ), patch("cogs.status.peek_active_labels", return_value=None), patch(
        "cogs.status.get_write_failure_count", return_value=0
    ), patch("utils.db.get_warning_stats", new_callable=AsyncMock) as mock_stats, patch(
        "utils.state_store._get_redis_client", return_value=AsyncMock()
    ), patch("config.OPENCODE_API_KEY", ""), patch("config.GEMINI_API_KEY", ""), patch(
        "cogs.status._http.circuit_breaker"
    ) as mock_cb:
        mock_stats.return_value = {}
        mock_cb.failures = ["https://host-a"]
        mock_cb.is_open.return_value = True

        with patch.dict("os.environ", {"IS_PRIMARY": "false"}):
            embeds = await view.build_embeds()

    embed = embeds[0]
    assert "PRIMARY ⚠️ FAILOVER" in embed.description
    # Open circuits override the role color to red.
    assert embed.color == discord.Color.red()
    fields = {f.name: f.value for f in embed.fields}
    assert "🔌 Open Circuits" in fields
    assert "`https://host-a`" in fields["🔌 Open Circuits"]
    assert "🔴 DISCONNECTED" in fields["🧠 AI Subsystem"]


async def test_build_embeds_detailed_task_view():
    import discord.ext.tasks as dl_tasks

    bot = _bot_with_state()
    cog = MagicMock()
    cog.auto_post_md = MagicMock(spec=dl_tasks.Loop)
    cog.auto_post_md.is_running.return_value = True
    bot.cogs = {"mesoscale": cog}
    view = _status_view(bot)
    view.detailed = True
    session = MagicMock()
    session.get.return_value.__aenter__ = AsyncMock(return_value=MagicMock(status=500))
    with patch(
        "cogs.status._http.ensure_session", new_callable=AsyncMock, return_value=session
    ), patch("cogs.status.get_high_risk_polygon", new_callable=AsyncMock), patch(
        "cogs.status.get_current_risk_display", return_value="SLGT"
    ), patch("cogs.status.peek_active_labels", return_value=None), patch(
        "cogs.status.get_write_failure_count", return_value=0
    ), patch("utils.db.get_warning_stats", new_callable=AsyncMock), patch(
        "utils.state_store._get_redis_client", return_value=AsyncMock()
    ), patch("config.OPENCODE_API_KEY", ""), patch("config.GEMINI_API_KEY", ""):
        embeds = await view.build_embeds()

    assert len(embeds) == 2
    assert embeds[1].title == "📋 Bot Task Details"
    assert "🟢" in embeds[1].description
    assert "NOAA-SPC mesoscale discussions" in embeds[1].description
