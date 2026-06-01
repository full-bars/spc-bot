
import asyncio
import aioboto3
from botocore.config import Config
import botocore

async def test_s3_download(bucket, key):
    print(f"Downloading key: {key} from bucket: {bucket}")
    session = aioboto3.Session()
    try:
        async with session.client(
            "s3",
            config=Config(signature_version=botocore.UNSIGNED),
            region_name="us-east-1"
        ) as s3:
            resp = await s3.get_object(Bucket=bucket, Key=key)
            content = await resp["Body"].read()
            print(f"  Downloaded {len(content)} bytes.")
            print(f"  First 20 bytes: {content[:20].hex()}")
            
            # Check for compression
            import zlib
            if content.startswith(b"\x1f\x8b"):
                print("  Gzip detected!")
                import gzip
                content = gzip.decompress(content)
                print(f"  Decompressed to {len(content)} bytes.")
            elif b"\x78\xda" in content[:100]:
                print("  Zlib detected!")
                offset = content.find(b"\x78\xda")
                content = zlib.decompress(content[offset:])
                print(f"  Decompressed to {len(content)} bytes.")
            
            print(f"  Header after potential decompression: {content[:20].hex()}")
            return content
    except Exception as e:
        print(f"  Error: {e}")
        return None

async def test_normalization():
    print("Testing normalization logic...")
    from lib.vad_plotter.vad_reader import _normalize_nids_bytes
    import gzip
    
    # Test Gzip decompression
    mock_data = b"MOCK DATA"
    gzipped = gzip.compress(mock_data)
    normalized = _normalize_nids_bytes(gzipped)
    print(f"  Gzip: {'SUCCESS' if normalized.startswith(mock_data) else 'FAILED'}")
    
    # Test Zlib decompression
    import zlib
    zlibbed = b"prefix" + zlib.compress(mock_data)
    normalized = _normalize_nids_bytes(zlibbed)
    print(f"  Zlib: {'SUCCESS' if normalized.startswith(mock_data) else 'FAILED'}")

async def test_listing_improvement():
    print("Testing listing improvement...")
    from lib.vad_plotter.vad_reader import _list_s3_vad_times
    
    # Check if it finds TLX with 4-letter or 3-letter logic
    results = await _list_s3_vad_times("KTLX")
    print(f"  KTLX results: {len(results)}")
    if results:
        print(f"  First key: {results[0][0]}")
    
    # Check if it finds AMA
    results = await _list_s3_vad_times("KAMA")
    print(f"  KAMA results: {len(results)}")

async def main():
    import logging
    logging.basicConfig(level=logging.INFO)
    await test_normalization()
    await test_listing_improvement()

if __name__ == "__main__":
    asyncio.run(main())
