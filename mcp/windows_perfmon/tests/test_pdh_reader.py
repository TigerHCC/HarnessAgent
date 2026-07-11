import importlib
import pdh_reader as pdh


SYNTH = {
    "cpu_utility_pct": 92.0, "cpu_privileged_pct": 10.0, "cpu_queue_length": 1.0,
    "context_switches_sec": 50000.0,
    "disk_sec_per_read": 0.002, "disk_sec_per_write": 0.05, "disk_sec_per_transfer": 0.04,
    "disk_queue_length": 3.0, "disk_idle_pct": 20.0,
    "mem_available_mb": 300.0, "mem_committed_pct": 95.0,
    "mem_pool_nonpaged_mb": 1100.0, "mem_pool_paged_mb": 900.0,
    "mem_pages_sec": 1500.0, "mem_page_faults_sec": 8000.0,
}


def _patch(monkeypatch, values=None, status=None):
    monkeypatch.setattr(pdh, "read_counters",
                        lambda keys=None, paths=None, delay_ms=1000: (dict(values or SYNTH), status or {}))


def test_snapshot_groups(monkeypatch):
    _patch(monkeypatch)
    s = pdh.snapshot(delay_ms=0)
    assert set(s["counters"].keys()) == {"cpu", "disk", "memory", "system"}
    assert s["counters"]["cpu"]["cpu_utility_pct"] == 92.0
    assert s["counters"]["disk"]["disk_sec_per_transfer"] == 0.04
    assert s["counters"]["memory"]["mem_available_mb"] == 300.0
    assert s["counters"]["system"]["context_switches_sec"] == 50000.0


def test_bottleneck_flags(monkeypatch):
    _patch(monkeypatch)
    b = pdh.bottleneck(delay_ms=0)
    metrics = {f["metric"] for f in b["findings"]}
    assert "cpu_utility_pct" in metrics        # 92 > 85
    assert "disk_sec_per_transfer" in metrics  # 0.04 > 0.025
    assert "disk_queue_length" in metrics      # 3 > 2
    assert "mem_available_mb" in metrics       # 300 < 500 (below)
    assert "mem_committed_pct" in metrics      # 95 > 90
    assert "mem_pages_sec" in metrics          # 1500 > 1000
    assert b["verdict"] != "healthy"


def test_bottleneck_healthy(monkeypatch):
    healthy = dict(SYNTH)
    healthy.update(cpu_utility_pct=10.0, disk_sec_per_transfer=0.001, disk_queue_length=0.0,
                   mem_available_mb=8000.0, mem_committed_pct=40.0, mem_pages_sec=10.0)
    _patch(monkeypatch, values=healthy)
    b = pdh.bottleneck(delay_ms=0)
    assert b["verdict"] == "healthy" and b["findings"] == []


def test_read_counters_empty_returns_tuple():
    v, s = pdh.read_counters(keys=[], paths=None)
    assert v == {} and s == {}


def test_baseline_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("PERFMON_BASELINES", str(tmp_path / "bl.json"))
    importlib.reload(pdh)
    monkeypatch.setattr(pdh, "read_counters",
                        lambda keys=None, paths=None, delay_ms=1000: (dict(SYNTH), {}))
    saved = pdh.baseline_save(name="b", delay_ms=0)
    assert saved["counter_count"] == len(SYNTH)
    # bump nonpaged pool, diff should show the delta
    bumped = dict(SYNTH, mem_pool_nonpaged_mb=1300.0)
    monkeypatch.setattr(pdh, "read_counters",
                        lambda keys=None, paths=None, delay_ms=1000: (dict(bumped), {}))
    d = pdh.baseline_diff(name="b", delay_ms=0)
    assert d["deltas"]["mem_pool_nonpaged_mb"]["delta"] == 200.0
    assert d["deltas"]["cpu_utility_pct"]["delta"] == 0.0


def test_baseline_diff_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PERFMON_BASELINES", str(tmp_path / "bl2.json"))
    importlib.reload(pdh)
    assert "error" in pdh.baseline_diff(name="nope")


def test_baseline_corrupt_and_incomplete_do_not_raise(tmp_path, monkeypatch):
    p = tmp_path / "bl3.json"
    monkeypatch.setenv("PERFMON_BASELINES", str(p))
    importlib.reload(pdh)
    monkeypatch.setattr(pdh, "read_counters",
                        lambda keys=None, paths=None, delay_ms=1000: (dict(SYNTH), {}))
    # non-dict top-level JSON -> coerced to {}, save recovers, diff returns tidy error
    p.write_text("[1,2,3]", encoding="utf-8")
    assert "error" in pdh.baseline_diff(name="x")
    assert pdh.baseline_save(name="x", delay_ms=0)["counter_count"] == len(SYNTH)
    # incomplete entry (missing 'values') -> tidy error, not KeyError/TypeError
    p.write_text('{"y": {"ts": "2026-01-01T00:00:00+00:00"}}', encoding="utf-8")
    assert "error" in pdh.baseline_diff(name="y")
    # non-dict entry -> tidy error
    p.write_text('{"z": "oops"}', encoding="utf-8")
    assert "error" in pdh.baseline_diff(name="z")
