import live_metrics as lm


def test_snapshot_shape():
    s = lm.snapshot()
    for k in ("cpu", "memory", "disk_io", "network", "power", "uptime_seconds", "top_cpu", "top_mem"):
        assert k in s
    assert 0 <= s["cpu"]["percent_total"] <= 100
    assert isinstance(s["cpu"]["percent_per_core"], list) and s["cpu"]["percent_per_core"]
    assert 0 <= s["memory"]["percent"] <= 100
    assert s["memory"]["total"] > 0
    assert s["network"]["total_recv_per_s"] >= 0
    assert s["uptime_seconds"] > 0


def test_top_processes():
    procs = lm.top_processes(by="memory", n=5)
    assert 1 <= len(procs) <= 5
    assert {"pid", "name"} <= set(procs[0])
