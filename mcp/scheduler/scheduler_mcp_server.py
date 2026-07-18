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
    its result. Tokens are single-use with a TTL. The lock only guards the _TOKENS dict; do() runs after
    it is released so a slow do() (e.g. a blocking `goose run`) never blocks other mutating tools."""
    now = time.time()
    authorized = False
    with _TOKENS_LOCK:
        if confirm_token:
            rec = _TOKENS.get(confirm_token)
            if rec and rec[0] == action and rec[1] == args \
                    and policy.verify_token(action, args, confirm_token, now=now, issued_at=rec[2]):
                del _TOKENS[confirm_token]      # single-use: delete before execute
                authorized = True
            else:
                confirm_token = ""              # fall through to re-preview
        if not authorized:
            # prune expired previews, then issue a fresh preview token
            for t in [t for t, r in _TOKENS.items() if now - r[2] > policy.TOKEN_TTL_SECONDS]:
                _TOKENS.pop(t, None)
            token = policy.make_token(action, args)
            _TOKENS[token] = (action, args, now)
    if authorized:
        return do()                             # outside the lock
    return {"requires_confirmation": True, "confirm_token": token, "action": action,
            "expires_in_seconds": policy.TOKEN_TTL_SECONDS}


# ---- goose firing + ticker ------------------------------------------------
def fire_goose(store, cfg, sched):
    """Spawn `goose run` for one schedule, tee output to a run log, return (returncode, log_path)."""
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


def run_now(store, ticker, id):
    """Fire one schedule out of band, respecting the running-job overlap guard. The actual goose run
    happens on a background daemon thread (exactly like the ticker's .start() loop) so this returns
    immediately instead of blocking on `goose run`, which can take minutes. Returns an 'already
    running' error rather than starting a second concurrent run on the same session."""
    rec = store.get(id)
    if not rec:
        return {"error": "unknown schedule id: %s" % id}
    if rec.get("last_status") == "running":
        return {"error": "already running", "id": id}
    threading.Thread(target=ticker._fire_one, args=(rec, datetime.now()), daemon=True).start()
    return {"started": id}


@mcp.tool()
def sched_run_now(id: str, confirm_token: str = "") -> dict:
    """Fire a schedule immediately, out of band. Confirm-gated."""
    return gate("sched_run_now", {"id": id}, confirm_token,
                lambda: run_now(_STORE, _TICKER, id))


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
