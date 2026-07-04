#!/usr/bin/env python3
"""Generate a ground-truth JSON fixture for the Rust thermo kernel.

Downloads a real observed sounding via SounderPy, runs
``sounding_params(clean_data).calc()``, and dumps both the raw profile
arrays (the exact inputs the Rust kernel receives) and the computed
parameters (CAPE/CIN/LCL/SRH/shear ground truth) to a JSON file that
Rust unit tests can load.

Usage:
    venv/bin/python scripts/gen_thermo_fixture.py [STATION] [YYYY MM DD HH]

Defaults: ILX (Lincoln, IL) at the most recent 00Z/12Z cycle that is at
least 3 hours old (RAOBs post ~1-2 h after launch). Falls back through
the previous few cycles if the newest one isn't available yet.

Output: tests/fixtures/thermo_kernel_<station>_<YYYYMMDD>_<HH>Z.json
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import numpy.ma as ma

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"

# Raw profile arrays from clean_data (pint Quantities) and their expected units.
RAW_KEYS = {
    "p": "hPa",  # pressure, surface -> top (decreasing)
    "z": "meter",  # geopotential height MSL (NOT AGL)
    "T": "degC",
    "Td": "degC",
    "u": "kt",  # NOTE: knots, not m/s
    "v": "kt",
}

# Ground-truth params to capture: (result dict, key) pairs.
THERMO_KEYS = [
    "sbcape",
    "sbcin",
    "mucape",
    "mucin",
    "mlcape",
    "mlcin",
    "sb3cape",
    "mu3cape",
    "sb_lcl_p",
    "sb_lcl_z",
    "sb_lfc_p",
    "sb_lfc_z",
    "mu_lcl_p",
    "ml_lcl_p",
    "lr_03km",
    "lr_36km",
    "dcape",
]
KINEM_KEYS = [
    "sm_u",
    "sm_v",  # Bunkers right-mover storm motion actually used for SRH (kts)
    "srh_0_to_500",
    "srh_0_to_1000",
    "srh_0_to_3000",
    "srh_0_to_6000",
    "shear_0_to_500",
    "shear_0_to_1000",
    "shear_0_to_3000",
    "shear_0_to_6000",
]


def _jsonable(val):
    """Convert SounderPy/SHARPpy output values to JSON-safe types (masked -> None)."""
    if val is None or val is ma.masked:
        return None
    if isinstance(val, ma.MaskedArray):
        return [None if m else float(x) for x, m in zip(val.data, ma.getmaskarray(val))]
    if hasattr(val, "magnitude"):  # pint Quantity
        return _jsonable(val.magnitude)
    if isinstance(val, np.ndarray):
        return [None if (isinstance(x, float) and np.isnan(x)) else float(x) for x in val.tolist()]
    if isinstance(val, (np.floating, np.integer)):
        val = float(val)
    if isinstance(val, float):
        return None if np.isnan(val) else val
    if isinstance(val, (int, str, bool)):
        return val
    try:
        return float(val)
    except (TypeError, ValueError):
        return str(val)


def candidate_cycles(now=None):
    """Yield (year, month, day, hour) for recent 00Z/12Z cycles, newest first."""
    now = now or datetime.now(timezone.utc)
    # RAOBs are usually posted 1-2 h after launch; require the cycle be >= 3 h old.
    t = now - timedelta(hours=3)
    cycle_hour = 12 if t.hour >= 12 else 0
    cycle = t.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)
    for _ in range(6):
        yield cycle.year, cycle.month, cycle.day, cycle.hour
        cycle -= timedelta(hours=12)


def fetch(station, cycles):
    import sounderpy as spy

    for year, month, day, hour in cycles:
        try:
            clean_data = spy.get_obs_data(
                station, str(year), f"{month:02d}", f"{day:02d}", f"{hour:02d}"
            )
            return clean_data, (year, month, day, hour), "raob"
        except Exception as e:  # noqa: BLE001 - sounderpy raises bare ValueError on missing data
            print(f"    ! no data for {station} {year}-{month:02d}-{day:02d} {hour:02d}Z: {e}")

    # Fallback: latest HRRR BUFKIT profile from PSU (observed archive unreachable).
    # Same clean_data dict format; still a real vertical profile for parity testing.
    print("    ! RAOB archive unavailable; falling back to latest HRRR BUFKIT profile")
    bufkit_station = station if station.startswith("K") else f"K{station}"
    try:
        clean_data = spy.get_bufkit_data("hrrr", bufkit_station, 1)
        now = datetime.now(timezone.utc)
        return clean_data, (now.year, now.month, now.day, now.hour), "bufkit-hrrr"
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"No sounding found for {station} (RAOB + BUFKIT both failed): {e}") from e


def main():
    args = sys.argv[1:]
    station = args[0] if args else "ILX"
    if len(args) == 5:
        cycles = [(int(args[1]), int(args[2]), int(args[3]), int(args[4]))]
    else:
        cycles = list(candidate_cycles())

    clean_data, (year, month, day, hour), source = fetch(station, cycles)

    # Raw arrays exactly as the Rust kernel would receive them (before unit conversion).
    raw = {}
    for key, expected_unit in RAW_KEYS.items():
        q = clean_data[key]
        actual_unit = str(getattr(q, "units", ""))
        raw[key] = {
            "units": actual_unit,
            "expected_units": expected_unit,
            "values": _jsonable(q),
        }

    # Ground truth from SounderPy (SHARPpy under the hood).
    import sounderpy as spy

    general, thermo, kinem, _intrp = spy.sounding_params(clean_data).calc()

    fixture = {
        "station": station,
        "valid_time": f"{year}-{month:02d}-{day:02d}T{hour:02d}:00:00Z",
        "source": f"SounderPy {source} -> sounding_params().calc() (SHARPpy backend)",
        "site_elevation_m": _jsonable(general.get("elevation")),
        "notes": {
            "sort_order": "surface -> top (pressure decreasing)",
            "z": "meters MSL; subtract z[0] (or site elevation) for AGL",
            "u_v": "KNOTS (divide by 1.94384 for m/s)",
            "cin_sign": "SHARPpy bminus is NEGATIVE J/kg",
            "srh": "computed with Bunkers right-mover motion (sm_u/sm_v, kts)",
            "shear": "magnitude in kts, layer bounds interpolated to exact AGL heights",
        },
        "raw_profile": raw,
        "expected": {
            "thermo": {k: _jsonable(thermo.get(k)) for k in THERMO_KEYS},
            "kinem": {k: _jsonable(kinem.get(k)) for k in KINEM_KEYS},
        },
    }

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    out = (
        FIXTURE_DIR
        / f"thermo_kernel_{station}_{source}_{year}{month:02d}{day:02d}_{hour:02d}Z.json"
    )
    out.write_text(json.dumps(fixture, indent=2))
    print(f"Wrote {out}")

    # Quick human-readable summary
    exp = fixture["expected"]
    print(json.dumps({"thermo": exp["thermo"], "kinem": exp["kinem"]}, indent=2))


if __name__ == "__main__":
    main()
