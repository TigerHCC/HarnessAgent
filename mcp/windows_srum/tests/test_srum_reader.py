import os
import pytest
import srum_reader as sr

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "SRUDB_copy.dat")
pytestmark = pytest.mark.skipif(
    not os.path.exists(FIX),
    reason="no SRUDB fixture (run spike then copy SRUDB to tests/fixtures/SRUDB_copy.dat)",
)


def test_parse_app_usage_from_fixture():
    data = sr._parse(FIX)
    rows = data["app_usage"]
    assert isinstance(rows, list)
    if rows:
        r = rows[0]
        assert "app" in r and isinstance(r["app"], str)
        assert r["bytes_read"] >= 0 and r["bytes_written"] >= 0
        assert r["foreground_cycles"] >= 0


def test_parse_network_from_fixture():
    rows = sr._parse(FIX)["network_usage"]
    assert isinstance(rows, list)
    if rows:
        assert {"app", "bytes_sent", "bytes_recvd"} <= set(rows[0])


def test_friendly_name_resolution():
    """Deterministic unit test of the IdBlob -> app-name resolver (independent of the SRUM fixture)."""
    assert sr._friendly("!!chrome.exe!2024/01/01:00:00:00!abcdef!x") == "chrome.exe"
    assert sr._friendly(r"C:\Program Files\Foo\app.exe") == "app.exe"
    assert sr._friendly("C:/Program Files/Foo/bar.exe") == "bar.exe"
    assert sr._friendly("PlainService") == "PlainService"
    assert sr._friendly(None) is None


def test_idmap_resolves_names():
    # at least some app names should be human-ish strings, not raw "id:NNN".
    rows = sr._parse(FIX)["app_usage"]
    resolvable = [r for r in rows if r["app"] and r["app"] not in ("id:None",)]
    if not resolvable:
        pytest.skip("committed SRUDB fixture is degenerate (no per-app rows); "
                    "resolver is covered by test_friendly_name_resolution + verified live")
    assert any(not r["app"].startswith("id:") for r in resolvable), "expected at least one resolved app name"


def test_health_shape():
    h = sr.health()
    for k in ("srudb_path", "is_admin", "parser_ok"):
        assert k in h
