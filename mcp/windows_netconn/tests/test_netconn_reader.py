import importlib
import netconn_reader as nr


ROWS = [
    {"proto": "TCP", "local": "0.0.0.0", "lport": 445, "remote": None, "rport": None,
     "state": "LISTEN", "pid": 4, "exe": "System", "services": None},
    {"proto": "TCP", "local": "192.168.1.5", "lport": 51000, "remote": "140.82.1.1", "rport": 443,
     "state": "ESTABLISHED", "pid": 5320, "exe": "chrome.exe", "services": None},
    {"proto": "TCP", "local": "192.168.1.5", "lport": 51001, "remote": "140.82.1.1", "rport": 443,
     "state": "TIME_WAIT", "pid": 5320, "exe": "chrome.exe", "services": None},
    {"proto": "UDP", "local": "0.0.0.0", "lport": 53, "remote": None, "rport": None,
     "state": "LISTEN", "pid": 2008, "exe": "svchost.exe", "services": ["Dnscache"]},
]


def _patch(monkeypatch):
    monkeypatch.setattr(nr, "_all_rows", lambda: list(ROWS))


def test_connections_filters(monkeypatch):
    _patch(monkeypatch)
    assert nr.connections(state="LISTEN")["count"] == 2
    assert nr.connections(proto="UDP")["count"] == 1
    assert nr.connections(pid=5320)["count"] == 2
    assert nr.connections(process="chrome")["count"] == 2
    assert nr.connections(port=443)["count"] == 2   # matches rport
    assert nr.connections(port=445)["count"] == 1   # matches lport


def test_connections_truncation(monkeypatch):
    _patch(monkeypatch)
    r = nr.connections(max=1)
    assert r["count"] == 1 and r["total_matching"] == 4 and r["truncated"] is True


def test_listeners(monkeypatch):
    _patch(monkeypatch)
    r = nr.listeners()
    assert r["count"] == 2
    assert all(x["state"] == "LISTEN" for x in r["listeners"])


def test_connection_stats(monkeypatch):
    _patch(monkeypatch)
    s = nr.connection_stats()
    assert s["total"] == 4
    assert s["by_state"]["LISTEN"] == 2 and s["by_state"]["TIME_WAIT"] == 1
    assert s["by_proto"]["TCP"] == 3 and s["by_proto"]["UDP"] == 1
    chrome = next(p for p in s["top_processes"] if p["exe"] == "chrome.exe")
    assert chrome["count"] == 2 and chrome["time_wait"] == 1
    assert s["ephemeral"]["distinct_ports_in_use"] == 2  # 51000 + 51001 in dynamic range


def test_time_wait_pid0_excluded_from_top_processes(monkeypatch):
    # Windows TIME_WAIT sockets report pid 0 -> must NOT form a phantom process, but still counted in by_state
    rows = list(ROWS) + [
        {"proto": "TCP", "local": "192.168.1.5", "lport": 52000 + i, "remote": "1.2.3.4", "rport": 80,
         "state": "TIME_WAIT", "pid": 0, "exe": None, "services": None} for i in range(50)]
    monkeypatch.setattr(nr, "_all_rows", lambda: rows)
    s = nr.connection_stats()
    assert s["by_state"]["TIME_WAIT"] == 51           # 50 pid-0 + the original chrome one
    assert all(p["pid"] not in (0, None) for p in s["top_processes"])  # no phantom pid-0 process
    assert not any(p["exe"] is None for p in s["top_processes"])


def test_negative_and_bad_max_clamped(monkeypatch):
    _patch(monkeypatch)
    r = nr.connections(max=-1)
    assert r["count"] == 0 and r["connections"] == []
    assert nr.connections(max="notanint")["count"] >= 0        # falls back to default, no raise
    assert nr.by_remote(max=-5)["count"] == 0


def test_connections_bad_filter_args_do_not_raise(monkeypatch):
    _patch(monkeypatch)
    # non-numeric pid/port must not raise (reader honors 'never raises')
    assert "error" not in nr.connections(pid="notapid")
    assert "error" not in nr.connections(port="http")


def test_by_remote(monkeypatch):
    _patch(monkeypatch)
    assert nr.by_remote()["count"] == 2                    # only rows with a remote
    assert nr.by_remote(ip="140.82")["count"] == 2
    assert nr.by_remote(ip="10.0.0.1")["count"] == 0


def test_signatures():
    sigs = nr._signatures(ROWS)
    assert "L|TCP|0.0.0.0:445|System" in sigs
    assert "R|chrome.exe|140.82.1.1:443" in sigs
    # LISTEN rows are L|, remote rows are R|; TIME_WAIT to same remote collapses with ESTABLISHED
    assert len([s for s in sigs if s.startswith("L|")]) == 2


def test_baseline_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("NETCONN_BASELINES", str(tmp_path / "bl.json"))
    importlib.reload(nr)
    monkeypatch.setattr(nr, "_all_rows", lambda: list(ROWS))
    saved = nr.baseline_save(name="t")
    assert saved["signature_count"] >= 3
    # no change -> empty diff
    d = nr.baseline_diff(name="t")
    assert d["summary"]["added"] == 0 and d["summary"]["removed"] == 0
    # add a new listener -> shows as added
    extra = ROWS + [{"proto": "TCP", "local": "0.0.0.0", "lport": 4444, "remote": None,
                     "rport": None, "state": "LISTEN", "pid": 999, "exe": "evil.exe", "services": None}]
    monkeypatch.setattr(nr, "_all_rows", lambda: list(extra))
    d2 = nr.baseline_diff(name="t")
    assert d2["summary"]["added"] == 1
    assert any("evil.exe" in s for s in d2["added"])


def test_baseline_diff_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("NETCONN_BASELINES", str(tmp_path / "bl2.json"))
    importlib.reload(nr)
    r = nr.baseline_diff(name="nope")
    assert "error" in r


def test_baseline_diff_corrupt_and_incomplete(tmp_path, monkeypatch):
    p = tmp_path / "bl3.json"
    monkeypatch.setenv("NETCONN_BASELINES", str(p))
    importlib.reload(nr)
    monkeypatch.setattr(nr, "_all_rows", lambda: list(ROWS))
    # non-dict top-level JSON -> _load_baselines coerces to {}, save/diff must not raise
    p.write_text("[]", encoding="utf-8")
    assert "error" in nr.baseline_diff(name="x")           # no baseline -> tidy error
    assert nr.baseline_save(name="x")["signature_count"] >= 1  # save recovers over the bad file
    # incomplete entry (missing 'signatures') -> tidy error, not KeyError
    p.write_text('{"y": {"ts": "2026-01-01T00:00:00+00:00"}}', encoding="utf-8")
    assert "error" in nr.baseline_diff(name="y")


def test_addr_and_proto_helpers():
    assert nr._addr(()) == (None, None)
    assert nr._addr(None) == (None, None)
