"""Coverage round 5: NWWS product parsing helpers (pure logic + Rust fallback wrappers)."""

from cogs import nwws


# ── Product ID normalization ─────────────────────────────────────────────────


def test_normalize_product_id_py_iso8601():
    result = nwws.normalize_product_id_py("KOUN", "NPWSMCD", "MCD", "2026-05-03T06:50:00Z")
    assert result == "202605030650-KOUN-NPWSMCD-MCD"


def test_normalize_product_id_py_truncates_non_iso():
    result = nwws.normalize_product_id_py("KOUN", "NPWSMCD", "MCD", "20260503065012Z")
    assert result == "202605030650-KOUN-NPWSMCD-MCD"


def test_normalize_product_id_falls_back_to_python(monkeypatch):
    monkeypatch.setattr(nwws, "_normalize_product_id_rust", None)
    result = nwws.normalize_product_id("KOUN", "NPWSMCD", "MCD", "2026-05-03T06:50:00Z")
    assert result == "202605030650-KOUN-NPWSMCD-MCD"


def test_normalize_product_id_uses_rust_when_available(monkeypatch):
    monkeypatch.setattr(nwws, "_normalize_product_id_rust", lambda *a: "RUST-ID")
    assert nwws.normalize_product_id("a", "b", "c", "d") == "RUST-ID"


def test_normalize_product_id_rust_error_falls_back(monkeypatch):
    def _boom(*a):
        raise RuntimeError("rust error")

    monkeypatch.setattr(nwws, "_normalize_product_id_rust", _boom)
    result = nwws.normalize_product_id("KOUN", "NPWSMCD", "MCD", "2026-05-03T06:50:00Z")
    assert result.startswith("202605030650-")


# ── MD number parsing ────────────────────────────────────────────────────────


def test_parse_md_number_py_matches():
    assert nwws.parse_md_number_py("Mesoscale Discussion 1234 text") == "1234"


def test_parse_md_number_py_pads_short_numbers():
    assert nwws.parse_md_number_py("Mesoscale Discussion 7") == "0007"


def test_parse_md_number_py_no_match():
    assert nwws.parse_md_number_py("no discussion here") is None


def test_parse_md_number_falls_back_to_python(monkeypatch):
    monkeypatch.setattr(nwws, "_parse_md_number_rust", None)
    assert nwws.parse_md_number("MESOSCALE DISCUSSION 42") == "0042"


def test_parse_md_number_rust_wins(monkeypatch):
    monkeypatch.setattr(nwws, "_parse_md_number_rust", lambda text: "9999")
    assert nwws.parse_md_number("anything") == "9999"


# ── Watch number parsing ─────────────────────────────────────────────────────


def test_parse_watch_number_py_tornado():
    result = nwws.parse_watch_number_py("Tornado Watch Number 45 issued")
    assert result == ("0045", "TORNADO")


def test_parse_watch_number_py_severe():
    result = nwws.parse_watch_number_py("Severe Thunderstorm Watch Number 100")
    assert result == ("0100", "SVR")


def test_parse_watch_number_py_no_match():
    assert nwws.parse_watch_number_py("nothing here") is None


def test_parse_watch_number_converts_rust_tuple(monkeypatch):
    monkeypatch.setattr(nwws, "_parse_watch_number_rust", lambda text: ["0012", "TORNADO"])
    assert nwws.parse_watch_number("Tornado Watch Number 12") == ("0012", "TORNADO")


def test_parse_watch_number_rust_error_falls_back(monkeypatch):
    def _boom(text):
        raise RuntimeError("rust error")

    monkeypatch.setattr(nwws, "_parse_watch_number_rust", _boom)
    assert nwws.parse_watch_number("Severe Thunderstorm Watch Number 3") == ("0003", "SVR")


# ── Product log ──────────────────────────────────────────────────────────────


def test_ensure_product_log_creates_and_is_idempotent(monkeypatch, tmp_path):
    from logging.handlers import RotatingFileHandler

    monkeypatch.chdir(tmp_path)
    (tmp_path / "cache").mkdir()
    monkeypatch.setattr(nwws, "_NWWS_PRODUCT_LOG", None)

    first = nwws._ensure_product_log()
    second = nwws._ensure_product_log()

    assert first is second
    handler = next(h for h in first.handlers if isinstance(h, RotatingFileHandler))
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 2
    log_path = tmp_path / "cache" / "nwws_products.log"
    assert log_path.is_file()
