import os
import procdetail as pd


def test_process_detail_self():
    d = pd.process_detail(os.getpid())
    assert "error" not in d, d
    assert d["pid"] == os.getpid()
    assert d["name"] and "python" in d["name"].lower()
    assert isinstance(d["num_threads"], int) and d["num_threads"] >= 1
    assert "memory" in d


def test_process_detail_bad_pid():
    assert "error" in pd.process_detail(0x7FFFFFFF)


def test_loaded_modules_self():
    m = pd.loaded_modules(os.getpid(), filter=".dll", max=5)
    assert "error" not in m, m
    assert "modules" in m and m["total"] >= 1
    for mod in m["modules"]:
        assert mod["name"] and mod["path"]


def test_loaded_modules_bad_pid():
    assert "error" in pd.loaded_modules(0x7FFFFFFF)


def test_loaded_modules_signature_check_shape(monkeypatch):
    # when the Authenticode check fails wholesale, must NOT imply everything is trusted
    monkeypatch.setattr(pd, "_check_signatures", lambda paths: None)
    m = pd.loaded_modules(os.getpid(), filter=".dll", check_signatures=True, max=3)
    assert m.get("signature_check", "").startswith("unavailable")
    assert "untrusted_or_unknown" not in m
    # a module missing from the sig map is 'Unknown' and flagged, not silently trusted
    monkeypatch.setattr(pd, "_check_signatures", lambda paths: {})   # ran, but resolved nothing
    m2 = pd.loaded_modules(os.getpid(), filter=".dll", check_signatures=True, max=3)
    assert "untrusted_or_unknown" in m2
    assert all(mod.get("signature") == "Unknown" for mod in m2["modules"])
    assert set(m2["untrusted_or_unknown"]) == {mod["name"] for mod in m2["modules"]}


def test_top_handle_users():
    t = pd.top_handle_users(5)
    assert "top_by_handles" in t
    assert t["count"] <= 5
    # sorted descending
    hs = [r["handles"] for r in t["top_by_handles"]]
    assert hs == sorted(hs, reverse=True)


def test_find_process_self():
    r = pd.find_process("python", max=20)
    assert "processes" in r
    assert any(p["pid"] == os.getpid() for p in r["processes"]) or r["total_matching"] >= 1


def test_health():
    h = pd.health()
    assert "is_admin" in h and "process_count" in h and h["process_count"] >= 1
