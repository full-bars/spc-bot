import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone, timedelta
from lib.vad_plotter.vad_reader import download_vad


@pytest.mark.asyncio
async def test_download_vad_s3_fallback():
    """Verify that download_vad falls back to S3 when TGFTP fails."""
    rid = "KTLX"
    # Use a specific time to bypass the racing path (which uses asyncio.wait
    # that interacts poorly with pytest-asyncio mock patches).
    target_time = datetime.now(timezone.utc) - timedelta(minutes=10)

    with patch("lib.vad_plotter.vad_reader.http_get_bytes", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = (None, 403)

        with patch(
            "lib.vad_plotter.vad_reader._list_s3_vad_times", new_callable=AsyncMock
        ) as mock_list_s3:
            mock_list_s3.return_value = [("TLX_NVW_MOCK_KEY", target_time)]

            mock_s3_client = AsyncMock()
            mock_s3_client.get_object.return_value = {
                "Body": MagicMock(read=AsyncMock(return_value=b"MOCK VAD CONTENT"))
            }

            mock_session = MagicMock()
            mock_session.client.return_value.__aenter__.return_value = mock_s3_client

            with patch("aioboto3.Session", return_value=mock_session), patch(
                "lib.vad_plotter.vad_reader.RUST_AVAILABLE", False
            ), patch("lib.vad_plotter.vad_reader.VADFile") as mock_vad_file:
                mock_vad_instance = MagicMock()
                mock_vad_file.return_value = mock_vad_instance

                result = await download_vad(rid, time=target_time)

                assert result == mock_vad_instance
                assert mock_list_s3.call_count >= 1
                mock_s3_client.get_object.assert_called_once()
                args, kwargs = mock_s3_client.get_object.call_args
                assert kwargs["Key"] == "TLX_NVW_MOCK_KEY"


@pytest.mark.asyncio
async def test_download_vad_racing_cancelled_error():
    """Verify racing path handles CancelledError gracefully.

    The racing code in download_vad must catch BaseException (not just Exception)
    because asyncio.CancelledError is a BaseException since Python 3.9.
    """
    import inspect
    from lib.vad_plotter.vad_reader import download_vad as dv

    src = inspect.getsource(dv)

    # The racing block must catch BaseException (not just Exception) because
    # asyncio.CancelledError is a BaseException since Python 3.9.
    # Verify `except BaseException:` appears anywhere in download_vad.
    # If someone changes it back to `except Exception`, this test will fail.
    assert "except BaseException" in src, (
        "download_vad must use except BaseException, not except Exception, "
        "to catch asyncio.CancelledError"
    )


if __name__ == "__main__":
    asyncio.run(test_download_vad_s3_fallback())
