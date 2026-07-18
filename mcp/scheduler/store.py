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
            for k in ("name", "session", "prompt", "mode", "max_turns", "next_run", "enabled", "last_status"):
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
