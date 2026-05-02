"""
Tests for IEM RAOB data quality controls (issue #87).

Covers:
- Per-level QC rejects physically implausible winds / thermo
- _iem_to_clean_data dedups duplicate-pressure levels and sorts descending
- validate_sounding_data requires dense winds over a meaningful span
"""

import numpy as np
import pytest


def _good_level(pres, tmpc=10.0, dwpc=5.0, drct=270.0, sknt=20.0, hght=1000.0):
    return {
        "pres": pres, "hght": hght,
        "tmpc": tmpc, "dwpc": dwpc,
        "drct": drct, "sknt": sknt,
    }


def _good_profile(n=12):
    # Surface 1000 hPa → 200 hPa, roughly realistic.
    pressures = np.linspace(1000, 200, n)
    return [_good_level(float(p), tmpc=20 - i, dwpc=15 - i,
                         drct=200 + i * 5, sknt=10 + i * 3,
                         hght=i * 1000.0)
            for i, p in enumerate(pressures)]


class TestIemLevelQC:
    def test_rejects_none_winds(self):
        from cogs.sounding_utils import _iem_level_is_valid
        assert not _iem_level_is_valid(_good_level(500, drct=None))
        assert not _iem_level_is_valid(_good_level(500, sknt=None))

    def test_rejects_out_of_range_direction(self):
        from cogs.sounding_utils import _iem_level_is_valid
        assert not _iem_level_is_valid(_good_level(500, drct=-10))
        assert not _iem_level_is_valid(_good_level(500, drct=361))

    def test_rejects_absurd_speed(self):
        from cogs.sounding_utils import _iem_level_is_valid
        assert not _iem_level_is_valid(_good_level(500, sknt=-5))
        assert not _iem_level_is_valid(_good_level(500, sknt=500))

    def test_rejects_bogus_pressure(self):
        from cogs.sounding_utils import _iem_level_is_valid
        assert not _iem_level_is_valid(_good_level(0.5))
        assert not _iem_level_is_valid(_good_level(2000))

    def test_rejects_dewpoint_above_temp(self):
        from cogs.sounding_utils import _iem_level_is_valid
        assert not _iem_level_is_valid(_good_level(500, tmpc=-10, dwpc=5))

    def test_accepts_clean_level(self):
        from cogs.sounding_utils import _iem_level_is_valid
        assert _iem_level_is_valid(_good_level(500))


class TestIemToCleanData:
    def test_clean_profile_converts(self):
        from cogs.sounding_utils import _iem_to_clean_data
        out = _iem_to_clean_data(
            _good_profile(12), "KILX", "Lincoln", 40.15, -89.33, 180,
            valid="2026-04-18T00:00:00Z",
        )
        assert out is not None
        assert len(out["p"]) == 12

    def test_dedups_duplicate_pressures(self):
        from cogs.sounding_utils import _iem_to_clean_data
        profile = _good_profile(10)
        # Inject a duplicate of level 3 with wildly different winds — the
        # starburst hodograph failure mode from issue #87.
        dup = dict(profile[3])
        dup["drct"] = 90.0
        dup["sknt"] = 120.0
        profile.append(dup)
        out = _iem_to_clean_data(
            profile, "KILX", "Lincoln", 40.15, -89.33, 180,
            valid="2026-04-18T00:00:00Z",
        )
        assert out is not None
        # Duplicate should have been dropped.
        assert len(out["p"]) == 10

    def test_drops_corrupted_levels(self):
        from cogs.sounding_utils import _iem_to_clean_data
        profile = _good_profile(10)
        profile.append(_good_level(400, drct=720, sknt=50))  # bad dir
        profile.append(_good_level(300, drct=180, sknt=500))  # bad speed
        out = _iem_to_clean_data(
            profile, "KILX", "Lincoln", 40.15, -89.33, 180,
            valid="2026-04-18T00:00:00Z",
        )
        assert out is not None
        assert len(out["p"]) == 10

    def test_returns_none_when_all_bad(self):
        from cogs.sounding_utils import _iem_to_clean_data
        bad = [_good_level(500, drct=720) for _ in range(5)]
        out = _iem_to_clean_data(
            bad, "KILX", "Lincoln", 40.15, -89.33, 180,
            valid="2026-04-18T00:00:00Z",
        )
        assert out is None

    def test_pressures_sorted_descending(self):
        from cogs.sounding_utils import _iem_to_clean_data
        profile = _good_profile(10)
        profile.reverse()  # scrambled order
        out = _iem_to_clean_data(
            profile, "KILX", "Lincoln", 40.15, -89.33, 180,
            valid="2026-04-18T00:00:00Z",
        )
        p = np.asarray(out["p"].magnitude)
        assert np.all(np.diff(p) < 0)


class TestSoundingQualityWarning:
    def test_warns_on_sparse_winds(self):
        from cogs.sounding_utils import _iem_to_clean_data, sounding_quality_warning
        out = _iem_to_clean_data(
            _good_profile(5), "KILX", "Lincoln", 40.15, -89.33, 180,
            valid="2026-04-18T00:00:00Z",
        )
        assert out is not None
        warning = sounding_quality_warning(out)
        assert warning is not None
        assert "wind" in warning.lower()

    def test_warns_on_shallow_pressure_span(self):
        from cogs.sounding_utils import _iem_to_clean_data, sounding_quality_warning
        pressures = np.linspace(1000, 900, 10)
        profile = [_good_level(float(p), drct=200 + i, sknt=10)
                   for i, p in enumerate(pressures)]
        out = _iem_to_clean_data(
            profile, "KILX", "Lincoln", 40.15, -89.33, 180,
            valid="2026-04-18T00:00:00Z",
        )
        assert out is not None
        warning = sounding_quality_warning(out)
        assert warning is not None
        assert "span" in warning.lower() or "shallow" in warning.lower()

    def test_no_warning_for_good_profile(self):
        from cogs.sounding_utils import _iem_to_clean_data, sounding_quality_warning
        out = _iem_to_clean_data(
            _good_profile(12), "KILX", "Lincoln", 40.15, -89.33, 180,
            valid="2026-04-18T00:00:00Z",
        )
        assert sounding_quality_warning(out) is None

    def test_validator_still_accepts_good_profile(self):
        from cogs.sounding_utils import _iem_to_clean_data, validate_sounding_data
        out = _iem_to_clean_data(
            _good_profile(12), "KILX", "Lincoln", 40.15, -89.33, 180,
            valid="2026-04-18T00:00:00Z",
        )
        assert validate_sounding_data(out) is True

    def test_validator_accepts_sparse_profile(self):
        """Validator should not withhold plottable data — low quality is a warning, not a rejection."""
        from cogs.sounding_utils import _iem_to_clean_data, validate_sounding_data
        out = _iem_to_clean_data(
            _good_profile(5), "KILX", "Lincoln", 40.15, -89.33, 180,
            valid="2026-04-18T00:00:00Z",
        )
        assert out is not None
        assert validate_sounding_data(out) is True


class TestMultiSourceSounding:
    @pytest.mark.asyncio
    async def test_fsl_to_clean_data_parsing(self):
        from cogs.sounding_utils import _fsl_to_clean_data
        
        # Real sample of FSL data
        sample_fsl = """
    1      100  12345  KILX  72433  40.15N  89.33W    180  1200
    2     2026      5      1     12      0      0      0      0
    3      KILX   72433  99999
    4   10000     180    154     82    260     25
    5    9250     762    120     70    270     35
    6    8500    1450     80     40    280     40
    7    7000    3100     -2    -50    290     50
    8    5000    5800    -15    -250    300     60
    9    4000    7500    -25    -350    310     70
"""
        out = _fsl_to_clean_data(
            sample_fsl, "KILX", "Lincoln", 40.15, -89.33, 180,
            ["2026", "05", "01", "12:00"]
        )
        assert out is not None
        assert len(out["p"]) == 3 # Mandatory + Significant thermo
        assert out["site_info"]["source"] == "RAOB OBSERVED (GSL)"
        assert out["p"][0].magnitude == 1000.0
        assert out["T"][0].magnitude == 15.4

    @pytest.mark.asyncio
    async def test_fetch_gsl_sounding_failure(self, monkeypatch):
        from cogs.sounding_utils import fetch_gsl_sounding
        
        # Mock http_get_text to return "No data available"
        async def mock_get(*args, **kwargs):
            return "No data available"
            
        monkeypatch.setattr("utils.http.http_get_text", mock_get)
        
        out = await fetch_gsl_sounding("KILX", "2026", "05", "01", "12")
        assert out is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
