from __future__ import print_function

import logging
import os
import re
import struct
import zlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from lib.vad_plotter.wsr88d import build_has_name
from utils.http import circuit_breaker, http_get_bytes, http_get_text

logger = logging.getLogger("spc_bot.vad_reader")

# Rust core fallback
try:
    import spc_rust_core
    RUST_AVAILABLE = True
    logger.info("VAD engine initialized: using Rust hybrid core")
except ImportError:
    RUST_AVAILABLE = False
    logger.debug("Rust core not available, using pure-python fallback for VWP header search")

_base_url = "https://tgftp.nws.noaa.gov/SL.us008001/DF.of/DC.radar/DS.48vwp"
_S3_BUCKET = os.getenv("VAD_S3_BUCKET", "unidata-nexrad-level3")

def _normalize_nids_bytes(raw_bytes: bytes) -> bytes:
    """
    Ensure the incoming bytes are raw NIDS Product 48, unwrapped and decompressed.
    """
    # 1. Handle Gzip or Zlib compression (common on S3)
    if raw_bytes.startswith(b"\x1f\x8b"):
        try:
            import gzip
            raw_bytes = gzip.decompress(raw_bytes)
            logger.debug(f"Decompressed Gzip payload: {len(raw_bytes)} bytes")
        except Exception as e:
            logger.warning(f"Failed to decompress Gzip payload: {e}")
    elif any(h in raw_bytes[:100] for h in (b"\x78\xda", b"\x78\x9c")):
        try:
            # Find the first occurrence of either header
            idx_da = raw_bytes.find(b"\x78\xda")
            idx_9c = raw_bytes.find(b"\x78\x9c")
            
            # Determine which one comes first (if both present, very unlikely in first 100)
            if idx_da != -1 and idx_9c != -1:
                offset = min(idx_da, idx_9c)
            else:
                offset = idx_da if idx_da != -1 else idx_9c
            
            raw_bytes = zlib.decompress(raw_bytes[offset:])
            logger.debug(f"Decompressed Zlib payload: {len(raw_bytes)} bytes")
        except Exception as e:
            logger.warning(f"Failed to decompress Zlib payload: {e}")

    # 2. Locate the NIDS Message Header (Product Code 48 = 0x0030)
    # We look for 48 (h) at the start of the Product Description Block.
    # The standard structure we expect is:
    # [30 bytes WMO] [18 bytes Msg Header] [12 bytes PDB prefix] [Product Code 48]
    # Total offset to code = 30 + 18 + 12 = 60.

    # If the file is already raw from TGFTP, it should have 48 at offset 60.
    # If it's unwrapped, it might have 48 at offset 30 (18 Msg + 12 PDB).

    found_offset = -1

    # Try Rust optimized search first
    if RUST_AVAILABLE:
        try:
            res = spc_rust_core.find_vwp_header_offset(raw_bytes)
            if res is not None:
                found_offset = res
        except Exception as e:
            logger.debug(f"Rust find_vwp_header_offset failed: {e}. Falling back to Python.")

    if found_offset == -1:
        # Fallback to Python loop if Rust failed or is unavailable
        for i in range(200):
            if i + 2 <= len(raw_bytes):
                val = struct.unpack(">h", raw_bytes[i:i+2])[0]
                if val == 48:
                    # Potential match. Verify if the Message Code at -30 bytes is also 48.
                    if i >= 30:
                        msg_code = struct.unpack(">h", raw_bytes[i-30:i-30+2])[0]
                        if msg_code == 48:
                            found_offset = i
                            break

    if found_offset != -1:
        # We want the message to start 60 bytes BEFORE the PDB product code
        # (30 WMO + 18 Msg Header + 12 PDB prefix).
        # We will strip whatever is there and prepend exactly 30 dummy bytes
        # so the existing _read_headers (which skips 30) works perfectly.
        nids_start = found_offset - 30 # Start of Message Header
        payload = raw_bytes[nids_start:]
        return b"A" * 30 + payload

    return raw_bytes

from collections.abc import Mapping


class VADFile(Mapping):
    fields = ['wind_dir', 'wind_spd', 'rms_error', 'divergence', 'slant_range', 'elev_angle']

    def __init__(self, file_or_bytes: Union[bytes, bytearray, BytesIO, Any]) -> None:
        if isinstance(file_or_bytes, (bytes, bytearray)):
            data = file_or_bytes
        elif hasattr(file_or_bytes, 'read'):
            data = file_or_bytes.read()
        else:
            data = bytes(file_or_bytes)

        normalized = _normalize_nids_bytes(data)
        self._rpg = BytesIO(normalized)
        self._data: Optional[Dict[str, np.ndarray]] = None
        self.rid: str = "" # Should be set externally or parsed

        self._read_headers()
        has_symbology_block, has_graphic_block, has_tabular_block = self._read_product_description_block()

        if has_symbology_block:
            self._read_product_symbology_block()

        if has_graphic_block:
            pass

        if has_tabular_block:
            self._read_tabular_block()

        self._data = self._get_data()
        del self._rpg
        return

    def _read_headers(self) -> None:
        wmo_header = self._read('s30')

        message_code = self._read('h')
        message_date = self._read('h')
        message_time = self._read('i')
        message_length = self._read('i')
        source_id = self._read('h')
        dest_id = self._read('h')
        num_blocks = self._read('h')

        return

    def _read_product_description_block(self) -> Tuple[bool, bool, bool]:
        self._read('h')
        self._radar_latitude  = self._read('i') / 1000.
        self._radar_longitude = self._read('i') / 1000.
        self._radar_elevation = self._read('h')

        product_code = self._read('h')
        if product_code != 48:
            raise IOError("This isn't a VWP file.")

        operational_mode    = self._read('h')
        self._vcp           = self._read('h')
        req_sequence_number = self._read('h')
        vol_sequence_number = self._read('h')

        scan_date    = self._read('h')
        scan_time    = self._read('i')
        product_date = self._read('h')
        product_time = self._read('i')

        self._read('h')
        self._read('h')
        self._read('h')
        self._read('h')
        self._read('16h')
        self._read('7h')

        version    = self._read('b')
        spot_blank = self._read('b')

        offset_symbology = self._read('i')
        offset_graphic   = self._read('i')
        self._tabular_offset = self._read('i')

        self._time = datetime(1969, 12, 31, 0, 0, 0) + timedelta(days=scan_date, seconds=scan_time)

        return offset_symbology > 0, offset_graphic > 0, self._tabular_offset > 0

    def _read_product_symbology_block(self) -> None:
        self._read('h')
        block_id = self._read('h')

        if block_id != 1:
            raise IOError("This isn't the product symbology block.")

        block_length    = self._read('i')
        num_layers      = self._read('h')
        layer_separator = self._read('h')
        layer_num_bytes = self._read('i')
        block_data      = self._read('%dh' % int(layer_num_bytes / struct.calcsize('h')))

        packet_code = -1
        packet_size = -1
        packet_counter = -1
        packet_value = -1
        packet = []
        for item in block_data:
            if packet_code == -1:
                packet_code = item
            elif packet_size == -1:
                packet_size = item
                packet_counter = 0
            elif packet_value == -1:
                packet_value = item
                packet_counter += struct.calcsize('h')
            else:
                packet.append(item)
                packet_counter += struct.calcsize('h')

                if packet_counter == packet_size:
                    if packet_code == 8:
                        str_data = struct.pack('>%dh' % int(packet_size / struct.calcsize('h') - 3), *packet[2:])
                    elif packet_code == 4:
                        pass

                    packet = []
                    packet_code = -1
                    packet_size = -1
                    packet_counter = -1
                    packet_value = -1
        return

    def _read_tabular_block(self) -> None:
        self._read('h')
        block_id = self._read('h')
        if block_id != 3:
            raise IOError("This isn't the tabular block.")

        block_size = self._read('i')

        self._read('h')
        self._read('h')
        self._read('i')
        self._read('i')
        self._read('h')
        self._read('h')
        self._read('h')

        self._read('h')
        self._read('i')
        self._read('i')
        self._read('h')
        product_code = self._read('h')

        operational_mode    = self._read('h')
        vcp                 = self._read('h')
        req_sequence_number = self._read('h')
        vol_sequence_number = self._read('h')

        scan_date    = self._read('h')
        scan_time    = self._read('i')
        product_date = self._read('h')
        product_time = self._read('i')

        self._read('h')
        self._read('h')
        self._read('h')
        self._read('h')
        self._read('16h')
        self._read('7h')

        version    = self._read('b')
        spot_blank = self._read('b')

        offset_symbology = self._read('i')
        offset_graphic   = self._read('i')
        offset_tabular   = self._read('i')

        self._read('h')
        num_pages = self._read('h')
        self._text_message = []
        for idx in range(num_pages):
            num_chars = self._read('h')
            self._text_message.append([])
            while num_chars != -1:
                self._text_message[-1].append(self._read("s%d" % num_chars))
                num_chars = self._read('h')

        return

    def _read(self, type_string: str) -> Any:
        if type_string[0] != 's':
            size = struct.calcsize(type_string)
            data = struct.unpack(">%s" % type_string, self._rpg.read(size))
        else:
            size = int(type_string[1:])
            data = tuple([ self._rpg.read(size).strip(b"\0").decode('utf-8') ])

        if len(data) == 1:
            return data[0]
        else:
            return list(data)

    def _get_data(self) -> Dict[str, np.ndarray]:
        # Try Rust optimized tabular parser first
        if RUST_AVAILABLE:
            try:
                # Seek to tabular block offset in the original stream
                offset_tabular = self._tabular_offset if hasattr(self, '_tabular_offset') else 0

                # Get the raw bytes from the BytesIO object
                self._rpg.seek(0)
                raw_bytes = self._rpg.read()

                # Correct absolute offset (30 bytes WMO + NIDS relative offset)
                abs_offset = 30 + offset_tabular
                res = spc_rust_core.parse_vwp_tabular_data(raw_bytes, abs_offset)
                if res:
                    logger.info("VWP tabular data parsed successfully using Rust engine")
                    # Convert list values to numpy arrays for compatibility
                    return {k: np.array(v) for k, v in res.items()}
                else:
                    logger.warning(f"Rust parse_vwp_tabular_data returned None for offset {abs_offset}")
            except Exception as e:
                logger.warning(f"Rust parse_vwp_tabular_data failed: {e}. Falling back to Python.")

        # Fallback to pure Python parsing
        logger.info("Parsing VWP tabular data using Python engine (fallback)")
        vad_list = []
        for page in self._text_message:
            if (page[0].strip())[:20] == "VAD Algorithm Output":
                vad_list.extend(page[3:])

        data = dict((k, []) for k in VADFile.fields)

        for line in vad_list:
            values = line.strip().split()
            data['wind_dir'].append(float(values[4]))
            data['wind_spd'].append(float(values[5]))
            data['rms_error'].append(float(values[6]))
            data['divergence'].append(float(values[7]) if values[7] != 'NA' else np.nan)
            data['slant_range'].append(float(values[8]))
            data['elev_angle'].append(float(values[9]))

        for key, val in data.items():
            data[key] = np.array(val)

        data['slant_range'] *= 6067.1 / 3281.

        r_e = 4. / 3. * 6371
        data['altitude'] = np.sqrt(r_e ** 2 + data['slant_range'] ** 2 + 2 * r_e * data['slant_range'] * np.sin(np.radians(data['elev_angle']))) - r_e

        order = np.argsort(data['altitude'])
        for key, val in data.items():
            data[key] = val[order]
        return data

    def __getitem__(self, key: str) -> Any:
        if key == 'time':
            val = self._time
        elif key == 'rid':
            val = self.rid
        else:
            val = self._data[key] # type: ignore
        return val

    def __iter__(self):
        keys = ['time', 'rid']
        if self._data:
            keys.extend(self._data.keys())
        return iter(keys)

    def __len__(self):
        return (len(self._data) if self._data else 0) + 2

    def add_surface_wind(self, sfc_wind: Tuple[float, float]) -> None:
        sfc_dir, sfc_spd = sfc_wind

        keys = ['wind_dir', 'wind_spd', 'rms_error', 'altitude']
        vals = [float(sfc_dir), float(sfc_spd), 0., 0.01]

        for key, val in zip(keys, vals):
            self._data[key] = np.append(val, self._data[key]) # type: ignore

import aioboto3
import botocore
from botocore.config import Config


async def _list_s3_vad_times(rid: str) -> List[Tuple[str, datetime]]:
    """List recent VAD files from S3 for a site."""
    # Try both 3-letter and 4-letter codes.
    # unidata-nexrad-level3 primarily uses 3-letter ICAO codes (omits leading K).
    # e.g. KTLX -> TLX.
    site_candidates = [rid.upper()]
    if rid.upper().startswith('K') and len(rid) == 4:
        site_candidates.append(rid[1:].upper())

    now = datetime.now(timezone.utc)
    session = aioboto3.Session()
    
    try:
        async with session.client(
            "s3",
            config=Config(signature_version=botocore.UNSIGNED),
            region_name="us-east-1"
        ) as s3:
            results = []
            # Check last 3 days
            for i in range(3):
                dt = now - timedelta(days=i)
                date_str = dt.strftime('%Y_%m_%d')
                
                for site_id in site_candidates:
                    prefix = f"{site_id}_NVW_{date_str}"
                    response = await s3.list_objects_v2(Bucket=_S3_BUCKET, Prefix=prefix)
                    
                    if "Contents" in response:
                        for obj in response["Contents"]:
                            key = obj["Key"]
                            try:
                                # Key format: SSS_NVW_YYYY_MM_DD_HH_MM_SS
                                if not key.startswith(f"{site_id}_NVW"):
                                    continue
                                ts_str = "_".join(key.split("_")[2:])
                                ts = datetime.strptime(ts_str, "%Y_%m_%d_%H_%M_%S").replace(tzinfo=timezone.utc)
                                results.append((key, ts))
                            except (ValueError, IndexError):
                                continue
                
                # If we found data for this day, we can probably stop unless we need more
                if results and i >= 1: # Found data for at least yesterday
                    break

            # Sort newest first
            return sorted(results, key=lambda x: x[1], reverse=True)
    except Exception as e:
        logger.warning(f"[VAD] S3 listing failed for {rid}: {e}")
        return []

async def find_file_times(rid: str) -> List[Tuple[str, datetime]]:
    host = "tgftp.nws.noaa.gov"

    # Try TGFTP if circuit is closed
    if not circuit_breaker.is_open(host):
        url = "%s/SI.%s/" % (_base_url, rid.lower())
        try:
            file_text = await http_get_text(url, timeout=10)
            if file_text:
                file_list = re.findall(r"([\w]{3} [\d]{1,2} [\d]{2}:[\d]{2}) (sn.[\d]{4})", file_text)
                if file_list:
                    file_times_raw, file_names_raw = list(zip(*file_list))
                    file_names = list(file_names_raw)

                    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                    year = now_utc.year
                    file_dts = []
                    for ft in file_times_raw:
                        ft_dt = datetime.strptime("%d %s" % (year, ft), "%Y %b %d %H:%M")
                        if ft_dt > now_utc:
                            ft_dt = datetime.strptime("%d %s" % (year - 1, ft), "%Y %b %d %H:%M")

                        file_dts.append(ft_dt)

                    file_list_zipped = list(zip(file_names, file_dts))
                    file_list_zipped.sort(key=lambda fl: fl[1])

                    file_names_sorted, file_dts_sorted = list(zip(*file_list_zipped))
                    file_names = list(file_names_sorted)

                    file_names[:-1] = file_names[1:]
                    file_names[-1] = 'sn.last'

                    return list(zip(file_names, file_dts_sorted))[::-1]
        except Exception as e:
            logger.warning(f"[VAD] TGFTP listing failed for {rid}, recording failure: {e}")
            circuit_breaker.record_failure(host)

    # Fallback to S3
    logger.info(f"[VAD] Falling back to S3 listing for {rid}")
    return await _list_s3_vad_times(rid)

async def download_vad(
    rid: str,
    time: Optional[datetime] = None,
    file_id: Optional[int] = None,
    cache_path: Optional[str] = None,
) -> VADFile:
    host = "tgftp.nws.noaa.gov"
    content = None
    status = None

    # Attempt TGFTP if circuit is closed
    if not circuit_breaker.is_open(host):
        if time is None:
            if file_id is None:
                url = "%s/SI.%s/sn.last" % (_base_url, rid.lower())
            else:
                url = "%s/SI.%s/sn.%04d" % (_base_url, rid.lower(), file_id)
        else:
            file_name = ""
            times = await find_file_times(rid)
            # Filter for TGFTP filenames (sn.*)
            tgftp_times = [(fn, ft) for fn, ft in times if isinstance(fn, str) and fn.startswith("sn.")]
            for fn, ft in tgftp_times:
                # Ensure ft is naive for comparison if needed
                ft_naive = ft.replace(tzinfo=None) if ft.tzinfo else ft
                time_naive = time.replace(tzinfo=None) if time.tzinfo else time
                if ft_naive <= time_naive:
                    file_name = fn
                    break

            if file_name:
                url = "%s/SI.%s/%s" % (_base_url, rid.lower(), file_name)
            else:
                url = None

        if url:
            try:
                content, status = await http_get_bytes(url, retries=1, timeout=10)
                if status == 200 and content:
                    circuit_breaker.record_success(host)
                else:
                    logger.warning(f"[VAD] TGFTP fetch status {status} for {rid}")
                    circuit_breaker.record_failure(host)
            except Exception as e:
                logger.warning(f"[VAD] TGFTP fetch exception for {rid}: {e}")
                circuit_breaker.record_failure(host)

    # Fallback to S3 if TGFTP failed or circuit was open
    if not content:
        logger.info(f"[VAD] Fetching from S3 fallback for {rid}")
        s3_times = await _list_s3_vad_times(rid)
        if not s3_times:
            raise ValueError(f"Could not find VAD data for {rid} on TGFTP or S3")

        target_key = None
        if time:
            time_utc = time.replace(tzinfo=timezone.utc) if not time.tzinfo else time
            for key, ts in s3_times:
                if ts <= time_utc:
                    target_key = key
                    break
        else:
            target_key = s3_times[0][0] # Latest

        if not target_key:
             raise ValueError(f"No VAD files before {time} found on S3 for {rid}")

        session = aioboto3.Session()
        try:
            async with session.client(
                "s3",
                config=Config(signature_version=botocore.UNSIGNED),
                region_name="us-east-1"
            ) as s3:
                resp = await s3.get_object(Bucket=_S3_BUCKET, Key=target_key)
                content = await resp["Body"].read()
        except Exception as e:
            raise ValueError(f"Failed to fetch VAD from S3 ({target_key}): {e}")

    if content:
        vad = VADFile(content)
        vad.rid = rid
        if cache_path:
            iname = build_has_name(rid, vad['time'])
            with open("%s/%s" % (cache_path, iname), 'wb') as floc:
                floc.write(content)
        return vad

    raise ValueError(f"VAD data unavailable for {rid}")
