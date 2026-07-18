# Scheduler Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone scheduler MCP (17th, port 8793) that fires headless `goose run` agent tasks on cron/one-shot schedules independently of goose_web, and give goose_web a UI + API to create, view, and control those schedules.

**Architecture:** A new `mcp/scheduler/` module holds pure `cron.py` math, a JSON-backed `store.py`, a `policy.py` confirm-token gate, and `scheduler_mcp_server.py` (FastMCP tools + a background ticker thread that spawns `goose run` for due jobs). goose_web reaches the scheduler over the existing `Invoke-McpHttp` MCP `tools/call` handshake and renders a sidebar summary plus an in-app Schedules view.

**Tech Stack:** Python 3 (stdlib + `mcp` FastMCP, `anyio`), PowerShell 5.1 (goose_web `server.ps1`, install scripts), vanilla JS (`index.html`), pytest.

## Global Constraints

- Scheduler MCP binds `127.0.0.1:8793` only; transport `streamable-http`; goose URI `http://127.0.0.1:8793/mcp`.
- Runs UNELEVATED — Scheduled Task `RunLevel Limited`, task name `Scheduler-MCP`, AtLogOn, via `scripts\start_mcp_hidden.ps1`.
- Python module uses stdlib only plus `mcp>=1.2` and `anyio>=4.5` (no third-party cron library — implement cron in `cron.py`).
- Cron is standard 5-field `minute hour day-of-month month day-of-week`; resolution is 1 minute. One-shot is `at` = ISO-8601 local datetime.
- Mutating tools (`sched_create`, `sched_update`, `sched_delete`, `sched_pause`, `sched_resume`, `sched_run_now`) are confirm-token gated exactly like `dtm_sdk` (argv/args-bound, single-use, TTL). Read tools (`sched_list`, `sched_get`, `sched_history`, `scheduler_health`) run directly.
- goose_web never weakens the gate: its `/api/schedules` routes perform the preview→confirm two-step automatically because a UI button click is the human confirmation.
- Every commit message body ends with the two trailer lines used across this repo:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013Wm8BeurMKFZK6TgvFhLjE`. (Omitted from the sample commits below for brevity — add them.)
- Branch is `main`; do not push or open a PR unless asked.

---

## File Structure

- Create `mcp/scheduler/cron.py` — pure cron/at parsing, `next_run`, `describe`, `validate`. No I/O.
- Create `mcp/scheduler/store.py` — `Store` class: `schedules.json` CRUD, `due`, `mark_running`, `record_run`, run-history retention.
- Create `mcp/scheduler/config.py` + `config.json` — resolved config with `SCHEDULER_MCP_<KEY>` env overrides.
- Create `mcp/scheduler/policy.py` — confirm-token gating for mutating tools.
- Create `mcp/scheduler/scheduler_mcp_server.py` — FastMCP tools + `Ticker` background thread.
- Create `mcp/scheduler/start_scheduler_mcp.ps1`, `install_task.ps1`, `uninstall_task.ps1`, `requirements.txt`, `README.md`, `conftest.py`, `tests/`.
- Modify `config/mcp_servers.json` — add the 17th entry.
- Modify `setup_mcp_servers.ps1` — expected count 16→17, port range `8777..8793`, banner.
- Modify `goose_web/server.ps1` — `/api/schedules` routes + `Invoke-SchedulerTool` helper.
- Modify `goose_web/index.html` — sidebar summary, Schedules view, create/edit + history drawers.

---

## Task 1: cron.py — schedule math (pure)

**Files:**
- Create: `mcp/scheduler/cron.py`
- Test: `mcp/scheduler/tests/test_cron.py`

**Interfaces:**
- Produces:
  - `validate(kind: str, expr: str) -> None` — raises `ValueError` on a malformed cron/at.
  - `next_run(kind: str, expr: str, now: datetime) -> datetime | None` — cron: the next minute strictly after `now` matching `expr`; at: the parsed datetime if it is `> now`, else `None` (already past).
  - `describe(kind: str, expr: str) -> str` — a short human label, e.g. `"每日 09:00"`, `"每小時"`, `"一次性 2026-07-18 20:00"`.

- [ ] **Step 1: Write the failing test**

```python
# mcp/scheduler/tests/test_cron.py
from datetime import datetime
import pytest
import cron

def test_daily_next_run_rolls_to_tomorrow():
    now = datetime(2026, 7, 18, 10, 0)
    nxt = cron.next_run("cron", "0 9 * * *", now)
    assert nxt == datetime(2026, 7, 19, 9, 0)

def test_hourly_next_run_is_next_top_of_hour():
    now = datetime(2026, 7, 18, 10, 30)
    assert cron.next_run("cron", "0 * * * *", now) == datetime(2026, 7, 18, 11, 0)

def test_step_field_every_15_min():
    now = datetime(2026, 7, 18, 10, 7)
    assert cron.next_run("cron", "*/15 * * * *", now) == datetime(2026, 7, 18, 10, 15)

def test_day_of_week_monday_only():
    now = datetime(2026, 7, 18, 12, 0)   # 2026-07-18 is a Saturday
    assert cron.next_run("cron", "0 9 * * 1", now) == datetime(2026, 7, 20, 9, 0)

def test_at_future_returns_datetime_past_returns_none():
    now = datetime(2026, 7, 18, 10, 0)
    assert cron.next_run("at", "2026-07-18T20:00", now) == datetime(2026, 7, 18, 20, 0)
    assert cron.next_run("at", "2026-07-18T09:00", now) is None

def test_validate_rejects_bad_cron_and_bad_at():
    with pytest.raises(ValueError):
        cron.validate("cron", "0 9 * *")        # only 4 fields
    with pytest.raises(ValueError):
        cron.validate("cron", "99 9 * * *")     # minute out of range
    with pytest.raises(ValueError):
        cron.validate("at", "not-a-date")

def test_describe_labels():
    assert cron.describe("cron", "0 9 * * *") == "每日 09:00"
    assert cron.describe("cron", "0 * * * *") == "每小時"
    assert cron.describe("at", "2026-07-18T20:00").startswith("一次性 2026-07-18 20:00")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp/scheduler && python -m pytest tests/test_cron.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cron'`.

- [ ] **Step 3: Write minimal implementation**

```python
# mcp/scheduler/cron.py
"""Pure schedule math: 5-field cron + one-shot `at`. No I/O, no third-party deps.

Cron resolution is one minute. `next_run` steps minute-by-minute from `now` (capacped) until a match,
which is simple and correct for a minute-resolution scheduler.
"""
from datetime import datetime, timedelta

_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]   # min hour dom month dow (dow: 0=Mon..6=Sun)
_MAX_STEPS = 366 * 24 * 60   # a year of minutes -- guard against an unsatisfiable spec


def _parse_field(field, lo, hi):
    """Return the set of allowed ints for one cron field (`*`, `a`, `a-b`, `*/n`, `a-b/n`, `a,b,c`)."""
    allowed = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError("cron step must be positive: %r" % field)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        if start < lo or end > hi or start > end:
            raise ValueError("cron field out of range: %r" % field)
        allowed.update(range(start, end + 1, step))
    return allowed


def _parse_cron(expr):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron must have 5 fields, got %d: %r" % (len(fields), expr))
    return [_parse_field(f, lo, hi) for f, (lo, hi) in zip(fields, _RANGES)]


def _matches(sets, dt):
    minute, hour, dom, month, dow = sets
    # Python weekday(): Mon=0..Sun=6, matching our dow convention.
    return (dt.minute in minute and dt.hour in hour and dt.day in dom
            and dt.month in month and dt.weekday() in dow)


def _parse_at(expr):
    return datetime.fromisoformat(expr)   # raises ValueError on a malformed datetime


def validate(kind, expr):
    if kind == "cron":
        _parse_cron(expr)
    elif kind == "at":
        _parse_at(expr)
    else:
        raise ValueError("unknown schedule kind: %r" % kind)


def next_run(kind, expr, now):
    if kind == "at":
        when = _parse_at(expr)
        return when if when > now else None
    sets = _parse_cron(expr)
    # start at the next whole minute after `now`
    dt = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for _ in range(_MAX_STEPS):
        if _matches(sets, dt):
            return dt
        dt += timedelta(minutes=1)
    raise ValueError("cron never matches within a year: %r" % expr)


def describe(kind, expr):
    if kind == "at":
        return "一次性 " + _parse_at(expr).strftime("%Y-%m-%d %H:%M")
    m, h, dom, mon, dow = expr.split()
    if expr == "0 * * * *":
        return "每小時"
    if dom == "*" and mon == "*" and dow == "*" and m.isdigit() and h.isdigit():
        return "每日 %02d:%02d" % (int(h), int(m))
    if dom == "*" and mon == "*" and dow != "*" and m.isdigit() and h.isdigit():
        return "每週(%s) %02d:%02d" % (dow, int(h), int(m))
    return "cron " + expr
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp/scheduler && python -m pytest tests/test_cron.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp/scheduler/cron.py mcp/scheduler/tests/test_cron.py
git commit -m "feat(scheduler): pure cron/at schedule math"
```

---

## Task 2: store.py — schedule persistence

**Files:**
- Create: `mcp/scheduler/store.py`
- Test: `mcp/scheduler/tests/test_store.py`

**Interfaces:**
- Consumes: `cron.next_run`, `cron.validate`, `cron.describe` from Task 1.
- Produces a `Store` class (paths injected for testability):
  - `Store(json_path: str, runs_dir: str, history_limit: int = 20)`
  - `list() -> list[dict]` / `get(sid) -> dict | None`
  - `create(fields: dict) -> dict` — validates cadence, assigns `id`, computes `next_run`, persists. Required `fields`: `name, kind, expr, session, prompt, mode`. `expr` is the cron string or the `at` datetime.
  - `update(sid, fields) -> dict` / `delete(sid) -> bool` / `set_enabled(sid, enabled) -> dict`
  - `due(now: datetime) -> list[dict]` — enabled, `next_run <= now`, `last_status != "running"`.
  - `mark_running(sid) -> None` and `record_run(sid, exit_code: int, log_path: str, now: datetime) -> dict` — writes status, appends a run summary (rolling `history_limit`), recomputes `next_run`, and auto-disables a fired `at` job.
  - `history(sid) -> list[dict]`

- [ ] **Step 1: Write the failing test**

```python
# mcp/scheduler/tests/test_store.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp/scheduler && python -m pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'store'`.

- [ ] **Step 3: Write minimal implementation**

```python
# mcp/scheduler/store.py
"""Persistence for schedules + run history. One JSON file, one lock. Paths are injected so tests can
point at a tmp dir. next_run math is delegated to cron.py.
"""
import json
import os
import threading
import uuid
from datetime import datetime

import cron

_ISO = "%Y-%m-%dT%H:%M:%S"


def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def _parse_iso(s):
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, json_path, runs_dir, history_limit=20):
        self.json_path = json_path
        self.runs_dir = runs_dir
        self.history_limit = history_limit
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
        os.makedirs(runs_dir, exist_ok=True)

    # ---- raw file I/O (call under _lock) ----
    def _read(self):
        if not os.path.exists(self.json_path):
            return {}
        with open(self.json_path, "r", encoding="utf-8") as f:
            return {r["id"]: r for r in json.load(f)}

    def _write(self, by_id):
        tmp = self.json_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(by_id.values()), f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.json_path)

    # ---- public API ----
    def list(self):
        with self._lock:
            recs = list(self._read().values())
        for r in recs:
            r["label"] = cron.describe(r["kind"], r["expr"])
        return recs

    def get(self, sid):
        with self._lock:
            return self._read().get(sid)

    def create(self, fields):
        cron.validate(fields["kind"], fields["expr"])
        now = datetime.now()
        nxt = cron.next_run(fields["kind"], fields["expr"], now)
        rec = {
            "id": uuid.uuid4().hex[:12],
            "name": fields["name"], "kind": fields["kind"], "expr": fields["expr"],
            "session": fields["session"], "prompt": fields["prompt"], "mode": fields.get("mode", "auto"),
            "max_turns": fields.get("max_turns"),
            "enabled": True, "created": _iso(now),
            "next_run": _iso(nxt) if nxt else None,
            "last_run": None, "last_status": None, "history": [],
        }
        with self._lock:
            by_id = self._read()
            by_id[rec["id"]] = rec
            self._write(by_id)
        return rec

    def update(self, sid, fields):
        with self._lock:
            by_id = self._read()
            rec = by_id.get(sid)
            if not rec:
                raise KeyError(sid)
            if "kind" in fields or "expr" in fields:
                kind = fields.get("kind", rec["kind"]); expr = fields.get("expr", rec["expr"])
                cron.validate(kind, expr)
                rec["kind"], rec["expr"] = kind, expr
                if "next_run" not in fields:
                    nxt = cron.next_run(kind, expr, datetime.now())
                    rec["next_run"] = _iso(nxt) if nxt else None
            for k in ("name", "session", "prompt", "mode", "max_turns", "next_run", "enabled"):
                if k in fields:
                    rec[k] = fields[k]
            self._write(by_id)
            return rec

    def delete(self, sid):
        with self._lock:
            by_id = self._read()
            if sid not in by_id:
                return False
            del by_id[sid]
            self._write(by_id)
            return True

    def set_enabled(self, sid, enabled):
        return self.update(sid, {"enabled": bool(enabled)})

    def due(self, now):
        out = []
        for r in self.list():
            if not r["enabled"] or r["last_status"] == "running" or not r["next_run"]:
                continue
            if _parse_iso(r["next_run"]) <= now:
                out.append(r)
        return out

    def mark_running(self, sid):
        self.update(sid, {"last_status": "running"})

    def record_run(self, sid, exit_code, log_path, now):
        with self._lock:
            by_id = self._read()
            rec = by_id.get(sid)
            if not rec:
                raise KeyError(sid)
            status = "ok" if exit_code == 0 else "error"
            rec["last_run"] = _iso(now)
            rec["last_status"] = status
            rec.setdefault("history", []).insert(0, {
                "time": _iso(now), "exit_code": exit_code, "status": status, "log": log_path})
            del rec["history"][self.history_limit:]
            if rec["kind"] == "at":
                rec["enabled"] = False
                rec["next_run"] = None
            else:
                nxt = cron.next_run(rec["kind"], rec["expr"], now)
                rec["next_run"] = _iso(nxt) if nxt else None
            self._write(by_id)
            return rec

    def history(self, sid):
        rec = self.get(sid)
        return list(rec.get("history", [])) if rec else []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp/scheduler && python -m pytest tests/test_store.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp/scheduler/store.py mcp/scheduler/tests/test_store.py
git commit -m "feat(scheduler): JSON-backed schedule + run-history store"
```

---

## Task 3: config.py + config.json

**Files:**
- Create: `mcp/scheduler/config.py`
- Create: `mcp/scheduler/config.json`
- Test: `mcp/scheduler/tests/test_config.py`

**Interfaces:**
- Produces `config.load(path=None) -> dict` with resolved, absolute keys: `workspace` (abs), `schedules_path` (abs), `runs_dir` (abs), `tick_seconds` (int), `default_max_turns` (int), `history_limit` (int), `goose_bin` (str, may be ""). Env overrides use `SCHEDULER_MCP_<KEY>`.

- [ ] **Step 1: Write the failing test**

```python
# mcp/scheduler/tests/test_config.py
import os
import config

def test_defaults_resolve_to_absolute_paths():
    c = config.load()
    assert os.path.isabs(c["workspace"])
    assert os.path.isabs(c["schedules_path"]) and c["schedules_path"].endswith("schedules.json")
    assert c["tick_seconds"] == 30 and c["default_max_turns"] == 50

def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MCP_TICK_SECONDS", "5")
    assert config.load()["tick_seconds"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp/scheduler && python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`.

- [ ] **Step 3: Write minimal implementation**

```json
{
  "workspace": "${repo_root}/workspace",
  "schedules_path": "${repo_root}/mcp/scheduler/state/schedules.json",
  "runs_dir": "${repo_root}/mcp/scheduler/state/runs",
  "tick_seconds": 30,
  "default_max_turns": 50,
  "history_limit": 20,
  "goose_bin": ""
}
```

```python
# mcp/scheduler/config.py
"""Config loading for the scheduler MCP: ${var} expansion against sibling string keys + repo_root,
then SCHEDULER_MCP_<KEY> env override. Mirrors mcp/dtm_download/config.py.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
_VAR_RE = re.compile(r"\$\{([a-z_]+)\}")


def env_key(name):
    return "SCHEDULER_MCP_" + name.upper()


def _expand(value, scope):
    if not isinstance(value, str):
        return value
    prev, out = None, value
    while out != prev:
        prev = out
        out = _VAR_RE.sub(lambda m: str(scope.get(m.group(1), m.group(0))), out)
    return out


def load(path=None):
    path = path or os.environ.get("SCHEDULER_MCP_CONFIG") or os.path.join(HERE, "config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    scope = {"repo_root": REPO_ROOT.replace("\\", "/")}
    for k, v in cfg.items():
        if isinstance(v, str):
            scope[k] = v
    for k in list(scope):
        if env_key(k) in os.environ:
            scope[k] = os.environ[env_key(k)]
    for k in list(scope):
        scope[k] = _expand(scope[k], scope)

    for k in ("workspace", "schedules_path", "runs_dir"):
        cfg[k] = os.path.normpath(scope.get(k, cfg.get(k, "")))
    for k, default in (("tick_seconds", 30), ("default_max_turns", 50), ("history_limit", 20)):
        val = scope.get(k, cfg.get(k, default))
        if env_key(k) in os.environ:
            val = os.environ[env_key(k)]
        cfg[k] = int(val)
    cfg["goose_bin"] = scope.get("goose_bin", cfg.get("goose_bin", "")) or ""
    return cfg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp/scheduler && python -m pytest tests/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp/scheduler/config.py mcp/scheduler/config.json mcp/scheduler/tests/test_config.py
git commit -m "feat(scheduler): config loading with env overrides"
```

---

## Task 4: policy.py — confirm-token gate

**Files:**
- Create: `mcp/scheduler/policy.py`
- Test: `mcp/scheduler/tests/test_policy.py`

**Interfaces:**
- Produces:
  - `MUTATING: set[str]` — `{"sched_create","sched_update","sched_delete","sched_pause","sched_resume","sched_run_now"}`
  - `TOKEN_TTL_SECONDS: int`
  - `make_token(action: str, args: dict) -> str`
  - `verify_token(action: str, args: dict, token: str, *, now: float, issued_at: float) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# mcp/scheduler/tests/test_policy.py
import policy

def test_token_roundtrips_for_same_action_and_args():
    args = {"name": "x", "kind": "cron", "expr": "0 9 * * *"}
    tok = policy.make_token("sched_create", args)
    assert policy.verify_token("sched_create", args, tok, now=100.0, issued_at=100.0)

def test_token_rejected_when_args_differ():
    tok = policy.make_token("sched_create", {"name": "x"})
    assert not policy.verify_token("sched_create", {"name": "y"}, tok, now=100.0, issued_at=100.0)

def test_token_expires_after_ttl():
    args = {"name": "x"}
    tok = policy.make_token("sched_delete", args)
    late = 100.0 + policy.TOKEN_TTL_SECONDS + 1
    assert not policy.verify_token("sched_delete", args, tok, now=late, issued_at=100.0)

def test_mutating_set_matches_spec():
    assert policy.MUTATING == {"sched_create", "sched_update", "sched_delete",
                               "sched_pause", "sched_resume", "sched_run_now"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp/scheduler && python -m pytest tests/test_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'policy'`.

- [ ] **Step 3: Write minimal implementation**

```python
# mcp/scheduler/policy.py
"""Confirm-token gating for the scheduler's mutating tools. A token is a digest bound to the action name
and its argument dict; single-use enforcement + TTL live in the server. Mirrors dtm_sdk/policy.py.
"""
import hashlib
import json

TOKEN_TTL_SECONDS = 120

MUTATING = {"sched_create", "sched_update", "sched_delete",
            "sched_pause", "sched_resume", "sched_run_now"}


def _digest(action, args):
    payload = "%s|%s" % (action, json.dumps(args, sort_keys=True, separators=(",", ":"),
                                            ensure_ascii=False))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def make_token(action, args):
    return _digest(action, args)


def verify_token(action, args, token, *, now, issued_at):
    if not token or token != _digest(action, args):
        return False
    return (now - issued_at) <= TOKEN_TTL_SECONDS
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp/scheduler && python -m pytest tests/test_policy.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp/scheduler/policy.py mcp/scheduler/tests/test_policy.py
git commit -m "feat(scheduler): confirm-token gate for mutating tools"
```

---

## Task 5: scheduler_mcp_server.py — FastMCP tools + ticker

**Files:**
- Create: `mcp/scheduler/scheduler_mcp_server.py`
- Create: `mcp/scheduler/conftest.py`
- Test: `mcp/scheduler/tests/test_server.py`

**Interfaces:**
- Consumes: `cron`, `store.Store`, `config.load`, `policy` from Tasks 1–4.
- Produces (module-level, importable without starting the server):
  - `gate(action, args, confirm_token, do)` — returns a preview dict `{requires_confirmation, confirm_token, action, expires_in_seconds}` when `confirm_token` is empty/invalid, else runs `do()` and returns its result. Single-use tokens held in a module dict with TTL pruning.
  - `class Ticker` — `Ticker(store, cfg, runner=fire_goose)`; `.tick(now)` fires all due jobs once (used by tests); `.start()` launches the daemon loop.
  - `fire_goose(store, cfg, sched) -> int` — spawns `goose run`, writes the run log, returns exit code. Injectable so tests never spawn a real agent.
- The FastMCP tool functions (`sched_list`, `sched_create`, ...) wrap the above.

- [ ] **Step 1: Write the failing test**

```python
# mcp/scheduler/tests/test_server.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp/scheduler && python -m pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'server'`.

- [ ] **Step 3: Write minimal implementation**

Note: the test imports `server`; the runtime entry file is `scheduler_mcp_server.py`. Create the logic in `scheduler_mcp_server.py` and add a one-line shim `server.py` that re-exports it, so both the tests (`import server`) and the launcher (`scheduler_mcp_server.py`) work. Add `conftest.py` so tests import sibling modules.

```python
# mcp/scheduler/conftest.py
import os, sys
sys.path.insert(0, os.path.dirname(__file__))          # make cron/store/config/policy/server importable
```

```python
# mcp/scheduler/server.py
from scheduler_mcp_server import *          # noqa: F401,F403  (test/alias shim)
from scheduler_mcp_server import gate, Ticker, fire_goose   # explicit re-exports
```

```python
# mcp/scheduler/scheduler_mcp_server.py
"""Scheduler MCP (FastMCP, streamable HTTP, 127.0.0.1:8793).

Fires headless `goose run` agent tasks on cron/one-shot schedules, independently of goose_web. Mutating
tools are confirm-token gated (protects the chat-agent path); goose_web auto-confirms because a UI button
click is the human confirmation. A background Ticker thread spawns goose for due jobs. Runs UNELEVATED.
Goose connects via type: streamable_http, uri: http://127.0.0.1:8793/mcp.
"""
import os
import subprocess
import threading
import time
from datetime import datetime

from mcp.server.fastmcp import FastMCP

import config
import cron
import policy
from store import Store

mcp = FastMCP("scheduler", host="127.0.0.1", port=8793)

_CFG = config.load()
_STORE = Store(_CFG["schedules_path"], _CFG["runs_dir"], history_limit=_CFG["history_limit"])
_TOKENS = {}                                    # confirm_token -> (action, args, issued_at)
_TOKENS_LOCK = threading.Lock()


# ---- confirm-token gate ---------------------------------------------------
def gate(action, args, confirm_token, do):
    """Preview→confirm gate. Empty/invalid token -> return a preview; valid token -> run do() and return
    its result. Tokens are single-use with a TTL."""
    now = time.time()
    with _TOKENS_LOCK:
        if confirm_token:
            rec = _TOKENS.get(confirm_token)
            if rec and rec[0] == action and rec[1] == args \
                    and policy.verify_token(action, args, confirm_token, now=now, issued_at=rec[2]):
                del _TOKENS[confirm_token]
                return do()
            confirm_token = ""                  # fall through to re-preview
        # prune expired previews
        for t in [t for t, r in _TOKENS.items() if now - r[2] > policy.TOKEN_TTL_SECONDS]:
            _TOKENS.pop(t, None)
        token = policy.make_token(action, args)
        _TOKENS[token] = (action, args, now)
    return {"requires_confirmation": True, "confirm_token": token, "action": action,
            "expires_in_seconds": policy.TOKEN_TTL_SECONDS}


# ---- goose firing + ticker ------------------------------------------------
def fire_goose(store, cfg, sched):
    """Spawn `goose run` for one schedule, tee output to a run log, return the exit code."""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_dir = os.path.join(cfg.get("runs_dir", store.runs_dir), sched["id"])
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, ts + ".log")
    goose = cfg.get("goose_bin") or "goose"
    max_turns = str(sched.get("max_turns") or cfg.get("default_max_turns", 50))
    argv = [goose, "run", "-n", sched["session"], "--max-turns", max_turns, "-i", "-"]
    env = dict(os.environ, GOOSE_MODE=sched.get("mode", "auto"), GOOSE_TELEMETRY_ENABLED="false")
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.run(argv, input=sched["prompt"], text=True, encoding="utf-8",
                              stdout=log, stderr=subprocess.STDOUT, cwd=cfg.get("workspace", "."),
                              env=env)
    return proc.returncode, log_path


class Ticker:
    def __init__(self, store, cfg, runner=fire_goose):
        self.store = store
        self.cfg = cfg
        self.runner = runner
        self._stop = threading.Event()

    def _fire_one(self, sched, now):
        self.store.mark_running(sched["id"])
        try:
            result = self.runner(self.store, self.cfg, sched)
            code, log_path = result if isinstance(result, tuple) else (result, "")
        except Exception:
            code, log_path = 1, ""
        self.store.record_run(sched["id"], code, log_path, now)

    def tick(self, now):
        for sched in self.store.due(now):
            self._fire_one(sched, now)              # sequential in tests; threaded in start()

    def start(self):
        def loop():
            while not self._stop.wait(self.cfg.get("tick_seconds", 30)):
                now = datetime.now()
                for sched in self.store.due(now):
                    threading.Thread(target=self._fire_one, args=(sched, now), daemon=True).start()
        threading.Thread(target=loop, daemon=True).start()


# ---- MCP tools ------------------------------------------------------------
@mcp.tool()
def sched_list() -> dict:
    """List all schedules with their cadence label, next run, and last status. Read-only."""
    return {"schedules": _STORE.list()}


@mcp.tool()
def sched_get(id: str) -> dict:
    """Get one schedule by id. Read-only."""
    rec = _STORE.get(id)
    return rec or {"error": "unknown schedule id: %s" % id}


@mcp.tool()
def sched_history(id: str) -> dict:
    """Recent run history (time, exit code, status, log path) for a schedule. Read-only."""
    return {"id": id, "runs": _STORE.history(id)}


@mcp.tool()
def sched_create(name: str, kind: str, expr: str, session: str, prompt: str,
                 mode: str = "auto", confirm_token: str = "") -> dict:
    """Create a schedule. kind='cron' with a 5-field expr, or kind='at' with an ISO datetime. mode='auto'
    runs tools unattended; mode='chat' is model-only. Returns a confirm_token to pass back on first call."""
    args = {"name": name, "kind": kind, "expr": expr, "session": session, "prompt": prompt, "mode": mode}
    return gate("sched_create", args, confirm_token,
                lambda: _STORE.create(args))


@mcp.tool()
def sched_update(id: str, fields: dict, confirm_token: str = "") -> dict:
    """Update a schedule's fields (name/kind/expr/session/prompt/mode/enabled). Confirm-gated."""
    args = {"id": id, "fields": fields}
    return gate("sched_update", args, confirm_token, lambda: _STORE.update(id, fields))


@mcp.tool()
def sched_delete(id: str, confirm_token: str = "") -> dict:
    """Delete a schedule. Confirm-gated."""
    return gate("sched_delete", {"id": id}, confirm_token,
                lambda: {"deleted": _STORE.delete(id), "id": id})


@mcp.tool()
def sched_pause(id: str, confirm_token: str = "") -> dict:
    """Pause (disable) a schedule. Confirm-gated."""
    return gate("sched_pause", {"id": id}, confirm_token, lambda: _STORE.set_enabled(id, False))


@mcp.tool()
def sched_resume(id: str, confirm_token: str = "") -> dict:
    """Resume (enable) a schedule. Confirm-gated."""
    return gate("sched_resume", {"id": id}, confirm_token, lambda: _STORE.set_enabled(id, True))


@mcp.tool()
def sched_run_now(id: str, confirm_token: str = "") -> dict:
    """Fire a schedule immediately, out of band. Confirm-gated."""
    def _do():
        rec = _STORE.get(id)
        if not rec:
            return {"error": "unknown schedule id: %s" % id}
        _TICKER._fire_one(rec, datetime.now())
        return {"ran": id, "last_status": _STORE.get(id)["last_status"]}
    return gate("sched_run_now", {"id": id}, confirm_token, _do)


@mcp.tool()
def scheduler_health() -> dict:
    """Server health: schedule count, next upcoming run, store path, tick interval. Check first on issues."""
    recs = _STORE.list()
    upcoming = sorted([r["next_run"] for r in recs if r["enabled"] and r["next_run"]])
    return {"ok": True, "count": len(recs), "enabled": sum(1 for r in recs if r["enabled"]),
            "next_run": upcoming[0] if upcoming else None,
            "schedules_path": _CFG["schedules_path"], "tick_seconds": _CFG["tick_seconds"]}


_TICKER = Ticker(_STORE, _CFG)

if __name__ == "__main__":
    _TICKER.start()
    mcp.run(transport="streamable-http")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp/scheduler && python -m pytest tests/test_server.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full module suite**

Run: `cd mcp/scheduler && python -m pytest -v`
Expected: PASS (all Task 1–5 tests, ~22).

- [ ] **Step 6: Commit**

```bash
git add mcp/scheduler/scheduler_mcp_server.py mcp/scheduler/server.py mcp/scheduler/conftest.py mcp/scheduler/tests/test_server.py
git commit -m "feat(scheduler): FastMCP tools + background ticker"
```

---

## Task 6: Module scaffolding (start/install/uninstall, requirements, README)

**Files:**
- Create: `mcp/scheduler/requirements.txt`
- Create: `mcp/scheduler/start_scheduler_mcp.ps1`
- Create: `mcp/scheduler/install_task.ps1`
- Create: `mcp/scheduler/uninstall_task.ps1`
- Create: `mcp/scheduler/README.md`

**Interfaces:** None (scaffolding). Deliverable: the server starts under the shared hidden launcher and answers an MCP handshake on 8793.

- [ ] **Step 1: Write `requirements.txt`**

```
mcp>=1.2
anyio>=4.5
pytest>=8.0
```

- [ ] **Step 2: Write `start_scheduler_mcp.ps1`** (mirror `mcp/dtm_download/start_dtm_download_mcp.ps1`)

```powershell
# Starts the Scheduler MCP server. Does NOT need Administrator (it only drives goose + writes its own state).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Scheduler MCP on http://127.0.0.1:8793/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "scheduler_mcp_server.py")
```

- [ ] **Step 3: Write `install_task.ps1`** (mirror dtm_download's, UNELEVATED/Limited, task `Scheduler-MCP`)

```powershell
# Registers a Scheduled Task that runs the Scheduler MCP at logon, UNELEVATED (RunLevel Limited).
# Registering a Scheduled Task itself requires Administrator (a Windows requirement).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $here)
$launcher = Join-Path $repoRoot "scripts\start_mcp_hidden.ps1"
. (Join-Path $repoRoot "scripts\mcp_task_helpers.ps1")
$powershell = (Get-Command powershell).Source
$logRoot = Join-Path $repoRoot "logs\mcp"
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated to REGISTER the task (the server itself runs unelevated)." -ForegroundColor Red; exit 1 }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$server = Join-Path $here "scheduler_mcp_server.py"
$action = New-McpScheduledTaskAction -PowerShellPath $powershell -LauncherPath $launcher `
    -PythonPath $py -ServerPath $server -WorkingDirectory $here -Name "scheduler" -LogDirectory $logRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Limited -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "Scheduler-MCP" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered scheduled task 'Scheduler-MCP' (UNELEVATED, at logon)." -ForegroundColor Green
Write-Host "     Start now: Start-ScheduledTask -TaskName Scheduler-MCP" -ForegroundColor Cyan
Write-Host "     Remove:    .\uninstall_task.ps1" -ForegroundColor Cyan
```

- [ ] **Step 4: Write `uninstall_task.ps1`**

```powershell
# Removes the Scheduler MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "Scheduler-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'Scheduler-MCP' (if it existed)." -ForegroundColor Green
```

- [ ] **Step 5: Write `README.md`** — document tools, the cron+at model, trigger/overlap/catch-up policy, the `mode="auto"` unattended-tools warning, and that goose_web controls it via MCP `tools/call`. (Prose; follow the structure of `mcp/dtm_download/README.md`.)

- [ ] **Step 6: Manual verification — start and handshake**

Run:
```powershell
Start-Process -NoNewWindow python (Join-Path (Resolve-Path mcp/scheduler) 'scheduler_mcp_server.py')
Start-Sleep 3
try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8793/mcp -TimeoutSec 4 } catch { $_.Exception.Response.StatusCode.value__ }
```
Expected: a `406` or `400` status (FastMCP is alive and rejecting a bare GET). Stop the test process afterward.

- [ ] **Step 7: Commit**

```bash
git add mcp/scheduler/requirements.txt mcp/scheduler/start_scheduler_mcp.ps1 mcp/scheduler/install_task.ps1 mcp/scheduler/uninstall_task.ps1 mcp/scheduler/README.md
git commit -m "feat(scheduler): install scaffolding + docs"
```

---

## Task 7: Register the 17th MCP

**Files:**
- Modify: `config/mcp_servers.json` (append entry)
- Modify: `setup_mcp_servers.ps1:83` (count 16→17), `:91` (port range), `:131` (banner)

**Interfaces:** None. Deliverable: `setup_mcp_servers.ps1` validation accepts the 17-entry manifest.

- [ ] **Step 1: Append the manifest entry** to `config/mcp_servers.json` (after the `dtm_deploy` object, before the closing `]`):

```json
  ,
  {
    "name": "scheduler",
    "directory": "scheduler",
    "port": 8793,
    "task": "Scheduler-MCP",
    "run_level": "Limited",
    "description": "Fires headless `goose run` agent tasks on cron/one-shot schedules via local MCP server (127.0.0.1:8793). Runs UNELEVATED; create/update/delete/pause/resume/run_now are confirm-token gated, list/get/history/health run directly. goose_web controls it over MCP tools/call.",
    "health_tool": "scheduler_health"
  }
```

- [ ] **Step 2: Update the count guard** — `setup_mcp_servers.ps1:83`:

```powershell
if ($manifestEntries.Count -ne 17) { Die "MCP manifest must contain exactly 17 entries on canonical ports 8777-8793; found $($manifestEntries.Count) entries." }
```

- [ ] **Step 3: Update the port range** — `setup_mcp_servers.ps1:91`:

```powershell
$expectedPorts = @(8777..8793)
```

Also update the two `Die` messages at `:108` and `:122` to read `8777-8793`.

- [ ] **Step 4: Update the banner** — `setup_mcp_servers.ps1:131`:

```powershell
Write-Host "=== HarnessAgent MCP servers $mode (17: 12 diagnostic + dtmsdk + obsidian + dtm_download + dtm_deploy + scheduler) ===" -ForegroundColor Magenta
```

- [ ] **Step 5: Verify the manifest parses and the validation passes** (dry validation without installing):

Run:
```powershell
python -c "import json;d=json.load(open('config/mcp_servers.json',encoding='utf-8'));print(len(d),sorted(x['port'] for x in d))"
```
Expected: `17 [8777, 8778, ..., 8793]` — 17 entries, contiguous ports.

- [ ] **Step 6: Commit**

```bash
git add config/mcp_servers.json setup_mcp_servers.ps1
git commit -m "feat(scheduler): register scheduler as the 17th MCP"
```

---

## Task 8: goose_web `/api/schedules` routes

**Files:**
- Modify: `goose_web/server.ps1` — add `Invoke-SchedulerTool` to `$DiscoveryFns`; add `Handle-Schedules`; route `GET`/`POST /api/schedules` in `Handle-Request`.
- Test: `goose_web/tests/test_schedules.ps1`

**Interfaces:**
- Consumes: existing `Invoke-McpHttp $uri $bodyObj $sessionId $timeoutMs` (returns `@{sid; text}`), `ConvertFrom-McpBody`, `Parse-GooseExtensions`, `Send-Json`, `Read-Utf8Body`, `Get-QueryValue`.
- Produces: `Invoke-SchedulerTool $uri $name $arguments $timeoutMs` — does initialize → `tools/call`, parses the JSON-RPC result payload, and auto-confirms: if the parsed result has `requires_confirmation` + `confirm_token`, it re-calls once with `confirm_token` merged into `$arguments`. Returns the final result object (PSCustomObject).

- [ ] **Step 1: Write the failing test** (parsing + auto-confirm two-step against a stub)

```powershell
# goose_web/tests/test_schedules.ps1  -- run with:  powershell -File goose_web/tests/test_schedules.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# Load just the discovery function block from server.ps1 by extracting the $DiscoveryFns here-string is
# brittle; instead dot-source a tiny extraction: we test Merge-ConfirmArgs + result parsing in isolation.
. (Join-Path $here 'schedules_helpers_under_test.ps1')

# 1) Merge-ConfirmArgs adds the token without mutating the caller's hashtable identity semantics
$a = @{ id = 'x' }
$merged = Merge-ConfirmArgs $a 'tok123'
if ($merged.confirm_token -ne 'tok123' -or $merged.id -ne 'x') { throw 'Merge-ConfirmArgs failed' }

# 2) Parse-McpResult reads structuredContent first, then content[0].text JSON
$structured = '{"result":{"structuredContent":{"ok":true,"count":2}}}' | ConvertFrom-Json
if ((Parse-McpResult $structured).count -ne 2) { throw 'structuredContent parse failed' }
$textual = '{"result":{"content":[{"type":"text","text":"{\"requires_confirmation\":true,\"confirm_token\":\"t9\"}"}]}}' | ConvertFrom-Json
$p = Parse-McpResult $textual
if (-not $p.requires_confirmation -or $p.confirm_token -ne 't9') { throw 'content text parse failed' }

Write-Host '[OK] schedules helpers pass' -ForegroundColor Green
```

- [ ] **Step 2: Extract the two pure helpers into a dot-sourceable file** so the test can load them (they are also folded into `$DiscoveryFns`). Create `goose_web/schedules_helpers_under_test.ps1`:

```powershell
function Merge-ConfirmArgs($arguments, $token) {
    $out = @{}; foreach ($k in $arguments.Keys) { $out[$k] = $arguments[$k] }
    $out['confirm_token'] = $token
    return $out
}
function Parse-McpResult($obj) {
    if ($null -eq $obj -or $null -eq $obj.result) { return $null }
    if ($obj.result.PSObject.Properties['structuredContent'] -and $obj.result.structuredContent) {
        return $obj.result.structuredContent
    }
    if ($obj.result.content) {
        foreach ($c in $obj.result.content) {
            if ($c.type -eq 'text' -and $c.text) { try { return ($c.text | ConvertFrom-Json) } catch {} }
        }
    }
    return $obj.result
}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `powershell -NoProfile -File goose_web/tests/test_schedules.ps1`
Expected: FAIL — `schedules_helpers_under_test.ps1` not found (before Step 2) or throw (before helpers correct).

- [ ] **Step 4: Add `Invoke-SchedulerTool` + `Handle-Schedules` to `server.ps1`**

Inside the `$DiscoveryFns` here-string (so both the seed scope and the worker runspaces get them), append the two pure helpers from Step 2 verbatim, then add:

```powershell
function Resolve-SchedulerUri($configPath) {
    foreach ($e in (Parse-GooseExtensions $configPath)) {
        if ($e.id -eq 'scheduler' -and $e.uri) { return $e.uri }
    }
    return 'http://127.0.0.1:8793/mcp'          # fallback: canonical loopback endpoint
}

function Invoke-SchedulerTool($uri, $name, $arguments, $timeoutMs = 8000) {
    # initialize -> tools/call, then auto-confirm the preview->confirm two-step (the UI click IS the
    # human confirmation, so goose_web completes it without weakening the agent-path gate).
    $init = @{ jsonrpc='2.0'; id=1; method='initialize'; params=@{ protocolVersion='2025-06-18'; capabilities=@{}; clientInfo=@{ name='goose_web'; version='1' } } }
    $r1 = Invoke-McpHttp $uri $init $null $timeoutMs
    $sid = $r1.sid
    try { [void](Invoke-McpHttp $uri @{ jsonrpc='2.0'; method='notifications/initialized' } $sid $timeoutMs) } catch {}
    $callBody = @{ jsonrpc='2.0'; id=2; method='tools/call'; params=@{ name=$name; arguments=$arguments } }
    $r2 = Invoke-McpHttp $uri $callBody $sid $timeoutMs
    $res = Parse-McpResult (ConvertFrom-McpBody $r2.text)
    if ($res -and $res.PSObject.Properties['requires_confirmation'] -and $res.requires_confirmation -and $res.confirm_token) {
        $confArgs = Merge-ConfirmArgs $arguments $res.confirm_token
        $callBody2 = @{ jsonrpc='2.0'; id=3; method='tools/call'; params=@{ name=$name; arguments=$confArgs } }
        $r3 = Invoke-McpHttp $uri $callBody2 $sid $timeoutMs
        $res = Parse-McpResult (ConvertFrom-McpBody $r3.text)
    }
    return $res
}
```

Then add the request handler (in the worker block, near `Handle-Toggle`):

```powershell
function Handle-Schedules($ctx, $S) {
    if ($S.token) {
        $sup = $ctx.Request.Headers['X-Goose-Token']; if (-not $sup) { $sup = Get-QueryValue $ctx.Request.Url.Query 'token' }
        if ($sup -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
    }
    $uri = Resolve-SchedulerUri $S.gooseConfig
    try {
        if ($ctx.Request.HttpMethod -eq 'GET') {
            $res = Invoke-SchedulerTool $uri 'sched_list' @{}
            Send-Json $ctx @{ ok = $true; schedules = @($res.schedules) }; return
        }
        $req = $null; $bt = Read-Utf8Body $ctx
        try { if ($bt.Trim()) { $req = $bt | ConvertFrom-Json } } catch {}
        if ($null -eq $req -or -not $req.action) { Send-Json $ctx @{ error = 'action required' } 400; return }
        switch ($req.action) {
            'create'  { $res = Invoke-SchedulerTool $uri 'sched_create' @{ name=$req.name; kind=$req.kind; expr=$req.expr; session=$req.session; prompt=$req.prompt; mode=$req.mode } }
            'update'  { $res = Invoke-SchedulerTool $uri 'sched_update' @{ id=$req.id; fields=$req.fields } }
            'delete'  { $res = Invoke-SchedulerTool $uri 'sched_delete' @{ id=$req.id } }
            'toggle'  { $res = Invoke-SchedulerTool $uri (if ($req.enabled) { 'sched_resume' } else { 'sched_pause' }) @{ id=$req.id } }
            'run-now' { $res = Invoke-SchedulerTool $uri 'sched_run_now' @{ id=$req.id } }
            'history' { $res = Invoke-SchedulerTool $uri 'sched_history' @{ id=$req.id } }
            default   { Send-Json $ctx @{ error = "unknown action: $($req.action)" } 400; return }
        }
        Send-Json $ctx @{ ok = $true; result = $res }
    } catch {
        Send-Json $ctx @{ error = "scheduler offline: $_" } 502
    }
}
```

Add the routes in `Handle-Request` (alongside the other POST routes):

```powershell
elseif ($req.HttpMethod -eq 'GET' -and $path -eq '/api/schedules') { Handle-Schedules $ctx $S }
elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/schedules') { Handle-Schedules $ctx $S }
```

(Place the GET branch with the other GET routes and the POST branch with the POST routes.)

- [ ] **Step 5: Run the helper test to verify it passes**

Run: `powershell -NoProfile -File goose_web/tests/test_schedules.ps1`
Expected: `[OK] schedules helpers pass`.

- [ ] **Step 6: Live smoke test** (scheduler running from Task 6 verification): with the scheduler MCP up, start goose_web and:

Run:
```powershell
Invoke-RestMethod http://127.0.0.1:8799/api/schedules
```
Expected: `{ ok = True; schedules = @() }` (empty until schedules exist). If the scheduler is down, a `502 scheduler offline` — not a hang.

- [ ] **Step 7: Commit**

```bash
git add goose_web/server.ps1 goose_web/schedules_helpers_under_test.ps1 goose_web/tests/test_schedules.ps1
git commit -m "feat(goose_web): /api/schedules control via MCP tools/call"
```

---

## Task 9: goose_web Schedules UI

**Files:**
- Modify: `goose_web/index.html` — sidebar summary section, `Chat/排程` view toggle, Schedules table, create/edit drawer, history drawer, and the JS that drives them.

**Interfaces:**
- Consumes: `GET/POST /api/schedules` from Task 8; existing JS helpers `$`, `el`, `esc`, `fetch`, `loadHealth`, the token header pattern (`X-Goose-Token`), and the `renderSessions` sidebar-section pattern.
- Produces: `loadSchedules()`, `renderScheduleSidebar()`, `renderScheduleView()`, `openScheduleDrawer(rec?)`, `submitSchedule()`, `openHistory(id)`, `switchView(name)`.

- [ ] **Step 1: Add the sidebar summary section.** After the sessions section (`index.html:281-283`), add:

```html
<div class="sect" id="schedSect">
  <div class="sect-h">排程 <span class="badge" id="schedCount">0</span></div>
  <div id="schedList"></div>
  <button class="btnGhost" id="btnOpenSched">管理排程 →</button>
</div>
```

- [ ] **Step 2: Add the Schedules view container** in the main area (after the transcript div `index.html:303`), hidden by default:

```html
<div class="schedView" id="schedView" style="display:none">
  <div class="sv-head"><h2>排程</h2><button class="send" id="btnNewSched">+ 新排程</button></div>
  <table class="sv-table"><thead><tr>
    <th>名稱</th><th>週期</th><th>下次執行</th><th>上次</th><th>啟用</th><th>操作</th>
  </tr></thead><tbody id="schedRows"></tbody></table>
</div>
```

- [ ] **Step 3: Add the create/edit + history drawers** (before `</body>`):

```html
<div class="drawer" id="schedDrawer" style="display:none">
  <div class="dw-card">
    <h3 id="dwTitle">新排程</h3>
    <input type="hidden" id="dwId">
    <label>名稱 <input id="dwName"></label>
    <label>類型
      <select id="dwKind"><option value="cron">週期 (cron)</option><option value="at">一次性 (at)</option></select>
    </label>
    <label>預設
      <select id="dwPreset">
        <option value="">— 自訂 —</option>
        <option value="0 * * * *">每小時</option>
        <option value="0 9 * * *">每日 09:00</option>
        <option value="0 9 * * 1">每週一 09:00</option>
      </select>
    </label>
    <label id="dwCronWrap">cron / 時間 <input id="dwExpr" placeholder="0 9 * * *  或  2026-07-18T20:00"></label>
    <label>Session <input id="dwSession" placeholder="cron_health"></label>
    <label>模式
      <select id="dwMode"><option value="auto">auto（會自動跑工具）</option><option value="chat">chat（只回話）</option></select>
    </label>
    <label>Prompt <textarea id="dwPrompt" rows="3"></textarea></label>
    <div class="dw-warn" id="dwWarn">⚠ auto 模式會在無人看守時自動執行工具。</div>
    <div class="dw-actions"><button id="dwCancel">取消</button><button class="send" id="dwSave">儲存</button></div>
  </div>
</div>
<div class="drawer" id="histDrawer" style="display:none">
  <div class="dw-card"><h3>執行歷史</h3><div id="histBody"></div>
    <div class="dw-actions"><button id="histClose">關閉</button></div></div>
</div>
```

- [ ] **Step 4: Add the JS.** Near `loadHealth` (`index.html:520`), add:

```javascript
let SCHED = [];
function authHeaders(extra){ const h = extra||{}; if(window.TOKEN) h["X-Goose-Token"]=window.TOKEN; return h; }

async function loadSchedules(){
  try{ const r=await fetch("/api/schedules",{headers:authHeaders()}); const j=await r.json();
       SCHED = j.ok ? (j.schedules||[]) : []; }
  catch(e){ SCHED = []; }
  renderScheduleSidebar(); if($("#schedView").style.display!=="none") renderScheduleView();
}
function renderScheduleSidebar(){
  $("#schedCount").textContent = SCHED.length;
  const box=$("#schedList"); box.innerHTML="";
  SCHED.forEach(s=>{ const d=el("div","sched-row");
    d.innerHTML='<span class="dot '+(s.enabled?"up":"off")+'"></span>'+
      '<span class="nm">'+esc(s.name)+'</span><span class="nx">'+esc(s.label||"")+'</span>';
    d.onclick=()=>{ switchView("sched"); }; box.appendChild(d); });
}
function fmtStatus(s){ return s==="ok"?"✓ ok":s==="error"?"✗ err":s==="running"?"… run":"—"; }
function renderScheduleView(){
  const tb=$("#schedRows"); tb.innerHTML="";
  SCHED.forEach(s=>{ const tr=el("tr");
    tr.innerHTML='<td>'+esc(s.name)+'</td><td>'+esc(s.label||"")+'</td>'+
      '<td>'+esc((s.next_run||"").replace("T"," "))+'</td><td>'+fmtStatus(s.last_status)+'</td>'+
      '<td><input type="checkbox" '+(s.enabled?"checked":"")+' data-id="'+s.id+'" class="schTgl"></td>'+
      '<td class="sv-act">'+
        '<button title="立即執行" data-a="run-now" data-id="'+s.id+'">▶</button>'+
        '<button title="編輯" data-a="edit" data-id="'+s.id+'">✎</button>'+
        '<button title="刪除" data-a="del" data-id="'+s.id+'">🗑</button>'+
        '<button title="歷史" data-a="hist" data-id="'+s.id+'">⧗</button></td>';
    tb.appendChild(tr); });
  tb.querySelectorAll(".schTgl").forEach(c=>c.onchange=()=>postSched({action:"toggle",id:c.dataset.id,enabled:c.checked}));
  tb.querySelectorAll(".sv-act button").forEach(b=>b.onclick=()=>{
    const id=b.dataset.id, a=b.dataset.a;
    if(a==="edit") openScheduleDrawer(SCHED.find(x=>x.id===id));
    else if(a==="hist") openHistory(id);
    else if(a==="del"){ if(confirm("刪除此排程？")) postSched({action:"delete",id}); }
    else if(a==="run-now") postSched({action:"run-now",id});
  });
}
async function postSched(body){
  try{ const r=await fetch("/api/schedules",{method:"POST",headers:authHeaders({"Content-Type":"application/json"}),body:JSON.stringify(body)});
       const j=await r.json(); if(!r.ok||j.error) alert("排程操作失敗："+(j.error||r.status)); }
  finally{ await loadSchedules(); }
}
function switchView(name){
  const sched=name==="sched";
  $("#schedView").style.display=sched?"block":"none";
  $("#transcript").style.display=sched?"none":"block";
  $("#composer").style.display=sched?"none":"flex";
  if(sched) renderScheduleView();
}
function openScheduleDrawer(rec){
  $("#dwTitle").textContent=rec?"編輯排程":"新排程";
  $("#dwId").value=rec?rec.id:""; $("#dwName").value=rec?rec.name:"";
  $("#dwKind").value=rec?rec.kind:"cron"; $("#dwExpr").value=rec?rec.expr:"";
  $("#dwSession").value=rec?rec.session:""; $("#dwMode").value=rec?rec.mode:"auto";
  $("#dwPrompt").value=rec?rec.prompt:""; $("#dwPreset").value="";
  $("#dwWarn").style.display=($("#dwMode").value==="auto")?"block":"none";
  $("#schedDrawer").style.display="flex";
}
async function submitSchedule(){
  const id=$("#dwId").value;
  const body={ action: id?"update":"create", name:$("#dwName").value.trim(),
    kind:$("#dwKind").value, expr:$("#dwExpr").value.trim(), session:$("#dwSession").value.trim(),
    prompt:$("#dwPrompt").value, mode:$("#dwMode").value };
  if(id){ body.id=id; body.fields={ name:body.name, kind:body.kind, expr:body.expr,
    session:body.session, prompt:body.prompt, mode:body.mode }; }
  $("#schedDrawer").style.display="none"; await postSched(body);
}
async function openHistory(id){
  const r=await fetch("/api/schedules",{method:"POST",headers:authHeaders({"Content-Type":"application/json"}),body:JSON.stringify({action:"history",id})});
  const j=await r.json(); const runs=(j.result&&j.result.runs)||[];
  $("#histBody").innerHTML = runs.length? runs.map(x=>'<div class="hist-row">'+esc((x.time||"").replace("T"," "))+
    ' · '+esc(x.status)+' · <code>'+esc(x.log||"")+'</code></div>').join("") : "<div>尚無執行紀錄</div>";
  $("#histDrawer").style.display="flex";
}
```

- [ ] **Step 5: Wire the buttons + polling.** In the init/event-binding section, add:

```javascript
$("#btnOpenSched").onclick=()=>switchView("sched");
$("#btnNewSched").onclick=()=>openScheduleDrawer(null);
$("#dwCancel").onclick=()=>{$("#schedDrawer").style.display="none";};
$("#dwSave").onclick=submitSchedule;
$("#dwMode").onchange=()=>{$("#dwWarn").style.display=($("#dwMode").value==="auto")?"block":"none";};
$("#dwPreset").onchange=()=>{ if($("#dwPreset").value){ $("#dwKind").value="cron"; $("#dwExpr").value=$("#dwPreset").value; } };
$("#histClose").onclick=()=>{$("#histDrawer").style.display="none";};
$("#btnNew").addEventListener("click",()=>switchView("chat"));   // returning to a chat leaves the sched view
loadSchedules(); setInterval(loadSchedules, 30000);              // refresh alongside health
```

- [ ] **Step 6: Add minimal CSS** (in the `<style>` block) for `.sched-row`, `.badge`, `.dot.off`, `.schedView`, `.sv-table`, `.sv-act button`, `.drawer`, `.dw-card`, `.dw-warn`, `.hist-row`. Match the existing dark theme variables. Example:

```css
.badge{background:var(--line,#333);border-radius:8px;padding:0 6px;font-size:11px}
.dot.off{background:#666}
.schedView{padding:18px;overflow:auto}
.sv-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.sv-table{width:100%;border-collapse:collapse;font-size:13px}
.sv-table th,.sv-table td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line,#2a2a2a)}
.sv-act button{background:none;border:none;cursor:pointer;font-size:14px;padding:2px 4px}
.drawer{position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:50}
.dw-card{background:var(--panel,#1b1b1b);padding:20px;border-radius:12px;width:min(460px,92vw);display:flex;flex-direction:column;gap:10px}
.dw-card label{display:flex;flex-direction:column;gap:4px;font-size:12px}
.dw-warn{color:#e0a341;font-size:12px}
.dw-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:6px}
.hist-row{font-size:12px;padding:4px 0;border-bottom:1px solid var(--line,#2a2a2a)}
```

- [ ] **Step 7: Manual verification** — start the scheduler MCP + goose_web, open the UI:
  - the sidebar shows `排程 (0)`;
  - `管理排程 →` switches the main area to the Schedules table;
  - `+ 新排程` → fill name/`每日 09:00` preset/session/prompt → 儲存 → row appears, sidebar count becomes `1`;
  - toggle the enable checkbox, click ▶ run-now (writes a run log), ⧗ shows one history row, 🗑 deletes it.

- [ ] **Step 8: Commit**

```bash
git add goose_web/index.html
git commit -m "feat(goose_web): schedules sidebar summary + in-app management view"
```

---

## Self-Review Notes

- **Spec coverage:** module on 8793/Limited/AtLogOn (Tasks 6–7); cron+at (Task 1); store + history + overlap + at-auto-disable (Task 2); confirm-token gate on agent path (Tasks 4–5); goose_web auto-confirm two-step (Task 8); sidebar summary + in-app view + drawers (Task 9); `mode="auto"` explicit + flagged (Tasks 5, 9); missed-cron-no-catch-up / missed-at-runs-once (Task 2 `record_run` + `create` compute forward from now, and `due()` fires an overdue `at` on next tick); tests per module (every task).
- **Catch-up nuance:** the spec's "run a missed `at` once on next startup" is realized by `due(now)` returning any enabled `at` whose `next_run <= now`; the ticker fires it, then `record_run` auto-disables it. Missed cron is not caught up because `create`/`record_run` always compute `next_run` strictly forward from the current time.
- **Type consistency:** `next_run(kind, expr, now)`, `Store.record_run(sid, exit_code, log_path, now)`, `gate(action, args, confirm_token, do)`, `Invoke-SchedulerTool $uri $name $arguments $timeoutMs`, `Parse-McpResult`, `Merge-ConfirmArgs` are used consistently across tasks.
