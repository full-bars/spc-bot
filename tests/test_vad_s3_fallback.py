import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timezone, timedelta
from lib.vad_plotter.vad_reader import download_vad


@pytest.mark.asyncio
async def test_download_vad_s3_fallback():
    """Verify that download_vad falls back to S3 when TGFTP fails."""
    rid = "KTLX"
    # Simulate TGFTP failure (403 Forbidden)
    with patch("lib.vad_plotter.vad_reader.http_get_bytes", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = (None, 403)

        # We need to mock _list_s3_vad_times to return something known
        mock_s3_time = datetime.now(timezone.utc) - timedelta(minutes=10)
        mock_s3_data = [("TLX_NVW_MOCK_KEY", mock_s3_time)]

        with patch(
            "lib.vad_plotter.vad_reader._list_s3_vad_times", new_callable=AsyncMock
        ) as mock_list_s3:
            mock_list_s3.return_value = mock_s3_data

            # Mock the S3 download itself
            mock_content = b"MOCK VAD CONTENT"
            # We need to mock aioboto3 session and client
            mock_s3_client = AsyncMock()
            mock_s3_client.get_object.return_value = {
                "Body": MagicMock(read=AsyncMock(return_value=mock_content))
            }

            mock_session = MagicMock()
            mock_session.client.return_value.__aenter__.return_value = mock_s3_client

            with patch("aioboto3.Session", return_value=mock_session), patch(
                "lib.vad_plotter.vad_reader.RUST_AVAILABLE", False
            ), patch("lib.vad_plotter.vad_reader.VADFile") as mock_vad_file:
                # Mock VADFile instance
                mock_vad_instance = MagicMock()
                mock_vad_file.return_value = mock_vad_instance

                # Call download_vad
                result = await download_vad(rid)

                # Assertions
                assert result == mock_vad_instance
                mock_list_s3.assert_called_once_with(rid)
                mock_s3_client.get_object.assert_called_once()
                # Verify it used the key from our mock_s3_data
                args, kwargs = mock_s3_client.get_object.call_args
                assert kwargs["Key"] == "TLX_NVW_MOCK_KEY"
                print("Fallback to S3 verified successfully.")


if __name__ == "__main__":
    asyncio.run(test_download_vad_s3_fallback())
