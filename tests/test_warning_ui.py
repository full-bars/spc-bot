import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import discord

from cogs.warning_ui import EnvironmentalView, TornadoPhotoView, TornadoDashboardView

@pytest.fixture
def mock_interaction():
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.client = MagicMock()
    return interaction

@pytest.mark.asyncio
async def test_environmental_view_no_embed(mock_interaction):
    view = EnvironmentalView()
    mock_interaction.message = MagicMock()
    mock_interaction.message.embeds = []
    
    # In discord.py, children[0] is the button
    await view.children[0].callback(mock_interaction)
    mock_interaction.response.defer.assert_called_once_with(ephemeral=True)

@pytest.mark.asyncio
async def test_environmental_view_no_data(mock_interaction):
    view = EnvironmentalView()
    embed = MagicMock()
    embed.footer.text = "VTEC ... | 12345"
    mock_interaction.message = MagicMock()
    mock_interaction.message.embeds = [embed]
    
    mock_db = MagicMock()
    mock_cur = AsyncMock()
    mock_cur.fetchone.return_value = None
    mock_cur.__aenter__.return_value = mock_cur
    mock_cur.__aexit__.return_value = False
    mock_db.execute.return_value = mock_cur

    with patch("utils.events_db.get_events_db", new=AsyncMock(return_value=mock_db)):
        mock_interaction.client.get_cog.return_value = None
        await view.children[0].callback(mock_interaction)
        mock_interaction.followup.send.assert_called_once()
        assert "No environmental data" in mock_interaction.followup.send.call_args.args[0]

@pytest.mark.asyncio
async def test_environmental_view_success(mock_interaction):
    view = EnvironmentalView()
    embed = MagicMock()
    embed.footer.text = "VTEC ... | 12345"
    mock_interaction.message = MagicMock()
    mock_interaction.message.embeds = [embed]
    
    mock_db = MagicMock()
    mock_cur = AsyncMock()
    mock_cur.fetchone.return_value = {"gif_path": "test.gif", "srh_0_1": 150.0, "location": "Test Loc"}
    mock_cur.__aenter__.return_value = mock_cur
    mock_cur.__aexit__.return_value = False
    mock_db.execute.return_value = mock_cur

    with patch("utils.events_db.get_events_db", new=AsyncMock(return_value=mock_db)):
        with patch("cogs.warning_ui.os.path.exists", return_value=True):
            with patch("cogs.warning_ui.discord.File") as MockFile:
                await view.children[0].callback(mock_interaction)
                mock_interaction.followup.send.assert_called_once()
                kwargs = mock_interaction.followup.send.call_args.kwargs
                assert kwargs["embed"].title == "🌪️ Environmental Evolution - Test Loc"

@pytest.mark.asyncio
async def test_tornado_photo_view_pagination(mock_interaction):
    urls = ["url1", "url2", "url3", "url4", "url5"]
    parent_view = MagicMock()
    view = TornadoPhotoView(urls, parent_view, "Test Loc")
    
    assert view.page == 0
    assert len(view.build_embeds()) == 4
    
    # Next page button is children[1]
    next_btn = [c for c in view.children if c.label == "Next Page ▶"][0]
    await next_btn.callback(mock_interaction)
    assert view.page == 1
    assert len(view.build_embeds()) == 1
    
    # Prev page button is children[0]
    prev_btn = [c for c in view.children if c.label == "◀ Prev Page"][0]
    await prev_btn.callback(mock_interaction)
    assert view.page == 0
    assert len(view.build_embeds()) == 4

@pytest.mark.asyncio
async def test_tornado_dashboard_view_summary(mock_interaction):
    events = [
        {"timestamp": 1600000000, "magnitude": "EF3", "location": "Loc1", "source": "NWS", "coords": [1,2]},
        {"timestamp": 1600000000, "magnitude": "EF0", "location": "Loc2", "source": "NWS", "coords": [1,2]}
    ]
    view = TornadoDashboardView(events, "Test Dash", mode="summary")
    
    embed = view.build_summary_embed()
    assert "🟠1" in embed.description
    assert "🔵1" in embed.description

@pytest.mark.asyncio
async def test_tornado_dashboard_view_card(mock_interaction):
    events = [
        {"timestamp": 1600000000, "magnitude": "EF3", "location": "Loc1", "source": "NWS", "coords": [1,2], "lead_time": 12.5},
        {"timestamp": 1600000000, "magnitude": "EF0", "location": "Loc2", "source": "NWS", "coords": [1,2]}
    ]
    view = TornadoDashboardView(events, "Test Dash", mode="card")
    
    embed = view.build_card_embed()
    assert "Tornado: Loc1" in embed.title
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Rating"] == "EF3"
    assert fields["Lead Time"] == "12.5 min"

    next_btn = [c for c in view.children if c.label == "Next ▶"][0]
    # To mock render_map_if_needed properly, let's just let it return None, None
    await next_btn.callback(mock_interaction)
    assert view.index == 1

