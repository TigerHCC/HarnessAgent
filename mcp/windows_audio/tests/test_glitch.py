import glitch as g

def test_sample_rate_mismatch():
    assert g.detect_sample_rate_mismatch(["48000", "44100"]) is True
    assert g.detect_sample_rate_mismatch(["48000", "48000"]) is False
    assert g.detect_sample_rate_mismatch([]) is False

def test_short_trace_clamped_and_guarded(monkeypatch):
    monkeypatch.setattr(g, "_run_trace", lambda secs, timeout: {"ran": True, "events": [], "secs": secs})
    r = g.short_trace(999, max_seconds=30, timeout=60)
    assert r["ran"] is True and r["secs"] == 30   # clamped to max

def test_short_trace_tool_missing(monkeypatch):
    monkeypatch.setattr(g, "_run_trace", lambda secs, timeout: (_ for _ in ()).throw(FileNotFoundError("wpr")))
    r = g.short_trace(5, max_seconds=30, timeout=60)
    assert r["ran"] is False and "wpr" in r["error"]
