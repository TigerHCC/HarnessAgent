import threading
from datetime import datetime
import server
from store import Store

def mkstore(tmp_path):
    return Store(str(tmp_path / "schedules.json"), str(tmp_path / "runs"), history_limit=5)

def test_gate_previews_then_confirms():
    calls = []
    args = {"name": "x", "kind": "cron", "expr": "0 9 * * *"}
    preview = server.gate("sched_create", args, "", lambda: calls.append(1))
    assert preview["requires_confirmation"] is True and preview["confirm_token"]
    assert calls == []                                   # not executed yet
    tok = preview["confirm_token"]
    server.gate("sched_create", args, tok, lambda: calls.append(1))
    assert calls == [1]                                  # executed on confirm
    # token is single-use: a replay re-previews instead of executing again
    again = server.gate("sched_create", args, tok, lambda: calls.append(1))
    assert again["requires_confirmation"] is True and calls == [1]

def test_ticker_fires_due_job_and_records(tmp_path):
    s = mkstore(tmp_path)
    rec = s.create(dict(name="h", kind="cron", expr="0 9 * * *",
                        session="cron_h", prompt="hi", mode="auto"))
    s.update(rec["id"], {"next_run": "2020-01-01T00:00:00"})
    fired = []
    def fake_runner(store, cfg, sched):
        fired.append(sched["id"]); return 0
    t = server.Ticker(s, {"workspace": ".", "default_max_turns": 5, "goose_bin": "goose"},
                      runner=fake_runner)
    t.tick(datetime(2026, 7, 18, 10, 0))
    assert fired == [rec["id"]]
    assert s.get(rec["id"])["last_status"] == "ok"

def test_ticker_skips_running_job(tmp_path):
    s = mkstore(tmp_path)
    rec = s.create(dict(name="h", kind="cron", expr="0 9 * * *",
                        session="cron_h", prompt="hi", mode="auto"))
    s.update(rec["id"], {"next_run": "2020-01-01T00:00:00"})
    s.mark_running(rec["id"])
    t = server.Ticker(s, {"workspace": ".", "default_max_turns": 5, "goose_bin": "goose"},
                      runner=lambda *a: (_ for _ in ()).throw(AssertionError("should not fire")))
    t.tick(datetime(2026, 7, 18, 10, 0))                 # no exception => running job skipped

def test_run_now_skips_already_running(tmp_path):
    s = mkstore(tmp_path)
    rec = s.create(dict(name="h", kind="cron", expr="0 9 * * *",
                        session="cron_h", prompt="hi", mode="auto"))
    s.mark_running(rec["id"])                             # simulate an in-flight run
    fired = []
    def fake_runner(store, cfg, sched):
        fired.append(sched["id"]); return 0
    t = server.Ticker(s, {"workspace": ".", "default_max_turns": 5, "goose_bin": "goose"},
                      runner=fake_runner)
    result = server.run_now(s, t, rec["id"])
    assert result == {"error": "already running", "id": rec["id"]}
    assert fired == []                                   # runner NOT invoked on overlap

def test_run_now_returns_before_runner_completes(tmp_path):
    # run_now must not block on the goose subprocess: it hands the fire-off to a background
    # daemon thread (same pattern as Ticker.start()'s loop) and returns right away. The runner
    # here BLOCKS on `gate` until the test releases it; run_now must return *while the runner is
    # still blocked*. A synchronous run_now could never reach the `assert not gate.is_set()` line
    # without first setting `gate`, so this catches a regression back to synchronous firing.
    s = mkstore(tmp_path)
    rec = s.create(dict(name="h", kind="cron", expr="0 9 * * *",
                        session="cron_h", prompt="hi", mode="auto"))
    gate = threading.Event()        # test controls when the runner may finish
    started = threading.Event()     # runner signals it has begun
    recorded = threading.Event()    # fires once the background run is recorded
    def blocking_runner(store, cfg, sched):
        started.set()
        gate.wait(5)                # block until the test releases us
        return 0
    # settle signal without wall-clock sleeps: wrap the store's record_run so the test
    # can wait on the exact moment the background thread finishes recording the run.
    _orig_record = s.record_run
    def _record(*a, **k):
        r = _orig_record(*a, **k)
        recorded.set()
        return r
    s.record_run = _record
    t = server.Ticker(s, {"workspace": ".", "default_max_turns": 5, "goose_bin": "goose"},
                      runner=blocking_runner)
    result = server.run_now(s, t, rec["id"])
    assert result == {"started": rec["id"]}               # returned immediately...
    assert started.wait(2)                                # ...runner is running in background
    # KEY: while the runner is still blocked, run_now has ALREADY returned. A synchronous
    # run_now could never have reached this line without gate having been set.
    assert not gate.is_set()
    assert s.get(rec["id"])["last_status"] == "running"   # mark_running ran; run not yet recorded
    gate.set()                                            # release the runner
    assert recorded.wait(5)                               # background thread records the run
    assert s.get(rec["id"])["last_status"] == "ok"         # store recorded the completed run
