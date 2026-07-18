from datetime import datetime
import pytest
from store import Store

def mk(tmp_path):
    return Store(str(tmp_path / "schedules.json"), str(tmp_path / "runs"), history_limit=3)

def base_fields(**over):
    f = dict(name="健康檢查", kind="cron", expr="0 9 * * *",
             session="cron_health", prompt="run health", mode="auto")
    f.update(over); return f

def test_create_assigns_id_and_next_run(tmp_path):
    s = mk(tmp_path)
    rec = s.create(base_fields())
    assert rec["id"] and rec["enabled"] is True
    assert rec["next_run"] is not None and rec["last_status"] is None
    assert s.get(rec["id"])["name"] == "健康檢查"

def test_create_rejects_bad_cron(tmp_path):
    s = mk(tmp_path)
    with pytest.raises(ValueError):
        s.create(base_fields(expr="99 9 * * *"))

def test_due_selects_only_past_enabled_not_running(tmp_path):
    s = mk(tmp_path)
    r = s.create(base_fields())
    # force next_run into the past
    s.update(r["id"], {"next_run": "2020-01-01T00:00:00"})
    assert [d["id"] for d in s.due(datetime(2026, 7, 18, 10, 0))] == [r["id"]]
    s.mark_running(r["id"])
    assert s.due(datetime(2026, 7, 18, 10, 0)) == []      # running is excluded

def test_record_run_updates_status_and_recomputes_next(tmp_path):
    s = mk(tmp_path)
    r = s.create(base_fields())
    s.mark_running(r["id"])
    out = s.record_run(r["id"], 0, "runs/x/1.log", datetime(2026, 7, 18, 10, 0))
    assert out["last_status"] == "ok"
    assert out["next_run"] == "2026-07-19T09:00:00"       # rolled forward
    assert len(s.history(r["id"])) == 1

def test_at_job_auto_disables_after_run(tmp_path):
    s = mk(tmp_path)
    r = s.create(base_fields(kind="at", expr="2026-07-18T20:00"))
    s.mark_running(r["id"])
    out = s.record_run(r["id"], 0, "runs/x/1.log", datetime(2026, 7, 18, 20, 0))
    assert out["enabled"] is False and out["next_run"] is None

def test_history_retention_caps_at_limit(tmp_path):
    s = mk(tmp_path)
    r = s.create(base_fields())
    for i in range(5):
        s.record_run(r["id"], 0, "runs/x/%d.log" % i, datetime(2026, 7, 18, 10, i))
    assert len(s.history(r["id"])) == 3                   # history_limit
