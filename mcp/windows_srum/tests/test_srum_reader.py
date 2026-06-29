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


def test_idmap_resolves_names():
    # at least some app names should be human-ish strings, not raw "id:NNN"
    rows = sr._parse(FIX)["app_usage"]
    if rows:
        named = [r for r in rows if not r["app"].startswith("id:")]
        assert named, "expected at least one resolved app name"


def test_health_shape():
    h = sr.health()
    for k in ("srudb_path", "is_admin", "parser_ok"):
        assert k in h
