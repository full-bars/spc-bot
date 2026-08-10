"""Coverage round 4: WPC surface-fronts URL discovery (HTTP-mocked, no network)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from cogs import fronts


def _meta(last_modified):
    return {"last_modified": last_modified}


async def test_get_current_fronts_url_picks_latest_cycle():
    metas = {}
    for i, cycle in enumerate(fronts.FRONTS_CYCLES):
        metas[cycle] = _meta(f"Thu, 01 Jan 2026 {10 + i % 10:02d}:00:00 GMT")
    # Force cycle 12Z to be the newest.
    metas["12"] = _meta("Thu, 01 Jan 2026 23:00:00 GMT")

    async def fake_head(url, timeout=None):
        cycle = url.split("usfntsfc")[1][:2]
        return metas.get(cycle)

    with patch("cogs.fronts.http_head_meta", side_effect=fake_head) as mock_head:
        url, modified = await fronts.get_current_fronts_url()

    assert url == f"{fronts.FRONTS_SFC_DIR}usfntsfc12wbg.gif"
    assert modified == datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
    assert mock_head.await_count == len(fronts.FRONTS_CYCLES)


async def test_get_current_fronts_url_skips_cycles_without_meta():
    async def fake_head(url, timeout=None):
        return None

    with patch("cogs.fronts.http_head_meta", side_effect=fake_head):
        url, modified = await fronts.get_current_fronts_url()

    assert url is None
    assert modified is None


async def test_get_current_fronts_url_skips_bad_dates():
    async def fake_head(url, timeout=None):
        return {"last_modified": "not-a-date"}

    with patch("cogs.fronts.http_head_meta", side_effect=fake_head):
        url, modified = await fronts.get_current_fronts_url()

    assert url is None
    assert modified is None


def _fronts_cog():
    cog = fronts.FrontsCog.__new__(fronts.FrontsCog)
    cog.bot = MagicMock()
    return cog


async def test_fronts_command_happy_path():
    cog = _fronts_cog()
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    with patch("cogs.fronts.get_current_fronts_url", new_callable=AsyncMock) as mock_url, patch(
        "cogs.fronts.http_get_bytes", new_callable=AsyncMock
    ) as mock_bytes:
        mock_url.return_value = (
            "https://wpc/x.gif",
            datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        mock_bytes.return_value = (b"GIFDATA", 200)

        await cog.fronts.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert "WPC Surface Fronts" in embed.title
    assert embed.url == fronts.FRONTS_PAGE_URL
    assert "attachment://fronts.gif" in embed.image.url
    assert "Released 12:00 UTC" in embed.footer.text


async def test_fronts_command_no_url():
    cog = _fronts_cog()
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    with patch("cogs.fronts.get_current_fronts_url", new_callable=AsyncMock) as mock_url:
        mock_url.return_value = (None, None)

        await cog.fronts.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    assert "Failed to fetch" in interaction.followup.send.await_args.args[0]


async def test_fronts_command_download_failure():
    cog = _fronts_cog()
    interaction = MagicMock()
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    with patch("cogs.fronts.get_current_fronts_url", new_callable=AsyncMock) as mock_url, patch(
        "cogs.fronts.http_get_bytes", new_callable=AsyncMock
    ) as mock_bytes:
        mock_url.return_value = ("https://wpc/x.gif", None)
        mock_bytes.return_value = (None, 500)

        await cog.fronts.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    assert "Failed to download" in interaction.followup.send.await_args.args[0]
