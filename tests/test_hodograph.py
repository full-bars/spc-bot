# tests/test_hodograph.py
"""
Unit tests for the hodograph cog — radar ID validation and error handling.
No Discord connection or network access required.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestRadarValidation:
    def test_valid_radars_loaded(self):
        from lib.vad_plotter.wsr88d import _radar_info
        from cogs.hodograph import VALID_RADARS

        assert len(VALID_RADARS) == len(_radar_info)
        assert "KTLX" in VALID_RADARS
        assert "KNKX" in VALID_RADARS
        assert "KHGX" in VALID_RADARS
        assert "TDFW" in VALID_RADARS

    def test_invalid_site_not_in_valid_radars(self):
        from cogs.hodograph import VALID_RADARS

        assert "XXXX" not in VALID_RADARS
        assert "KZZZ" not in VALID_RADARS

    def test_radar_id_uppercased(self):
        from cogs.hodograph import VALID_RADARS

        # All entries should be uppercase
        for site in VALID_RADARS:
            assert site == site.upper()

    def test_suggestion_returned_for_close_match(self):
        import difflib
        from cogs.hodograph import VALID_RADARS

        suggestions = difflib.get_close_matches("KTLZ", VALID_RADARS, n=3, cutoff=0.5)
        assert "KTLX" in suggestions

    def test_no_suggestion_for_garbage_input(self):
        import difflib
        from cogs.hodograph import VALID_RADARS

        suggestions = difflib.get_close_matches("ZZZZ", VALID_RADARS, n=3, cutoff=0.5)
        assert suggestions == []


class TestGenerateHodograph:
    @pytest.mark.asyncio
    async def test_timeout_sends_ephemeral_message(self):
        from cogs.hodograph import generate_hodograph

        interaction = MagicMock()
        interaction.followup = AsyncMock()

        mock_process = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        async def raise_timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        with patch("cogs.hodograph.asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("cogs.hodograph.asyncio.wait_for", side_effect=raise_timeout), \
             patch("cogs.hodograph.os.makedirs"):
            await generate_hodograph(interaction, "KTLX")

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert "ephemeral" in call_kwargs.kwargs
        assert call_kwargs.kwargs["ephemeral"] is True
        assert "Timed out" in call_kwargs.args[0]

    @pytest.mark.asyncio
    async def test_nonzero_returncode_sends_error(self):
        from cogs.hodograph import generate_hodograph

        interaction = MagicMock()
        interaction.followup = AsyncMock()

        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"some error"))

        with patch("cogs.hodograph.asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("cogs.hodograph.asyncio.wait_for", return_value=(b"", b"some error")), \
             patch("cogs.hodograph.os.makedirs"):
            await generate_hodograph(interaction, "KTLX")

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "Could not generate" in call_kwargs.args[0]

    @pytest.mark.asyncio
    async def test_missing_output_file_sends_error(self):
        from cogs.hodograph import generate_hodograph

        interaction = MagicMock()
        interaction.followup = AsyncMock()

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"", b""))

        with patch("cogs.hodograph.asyncio.create_subprocess_exec", return_value=mock_process), \
             patch("cogs.hodograph.asyncio.wait_for", return_value=(b"", b"")), \
             patch("cogs.hodograph.os.makedirs"), \
             patch("cogs.hodograph.os.path.exists", return_value=False):
            await generate_hodograph(interaction, "KTLX")

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args
        assert call_kwargs.kwargs.get("ephemeral") is True
        assert "not generated" in call_kwargs.args[0]
