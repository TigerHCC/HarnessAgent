#!/usr/bin/env python3
"""Goose Harness Web -- a thin HTTP bridge that drives `goose run` and streams to a browser.

Single-file, stdlib-only (no pip). Lets you use the local Goose harness agent
(qwen-3.6-chat on the GB10 box, with the developer / memory / dtm MCP tools)
from any browser on the LAN.

Endpoints
  GET  /                 the web UI (index.html, same directory)
  GET  /api/health       JSON: model + backend status + tool list (cached version)
  POST /api/chat         streams NDJSON events; body {"session","message","mode"}
                         events: {type:text|tool_start|tool_args|done|error, ...}

Each chat turn runs:
    goose run -n <session> [-r] --max-turns N -t <message>
with cwd = workspace dir and GOOSE_MODE from the request ("auto" runs tools,
"chat" is model-only). The first turn for a session omits -r (goose errors if
you -r a session that does not exist yet); later turns add -r to resume context.

Configuration (config.json next to this file -- shared with server.ps1):
  Sets model, provider_label, and the backends list (vLLM chat / vLLM embed /
  Ollama URLs + health paths) shown in the status panel, plus host/port/token/
  workspace/max_turns/timeout/goose_bin. NOTE: backends here only drive the web
  UI's health panel + displayed provider host; goose's REAL model provider lives
  in goose's own config (~/.config/goose/config.yaml). Point at a different file
  with GOOSE_WEB_CONFIG=/path/to.json.

Env knobs (override config.json; all optional):
  GOOSE_WEB_HOST       bind address              (default 0.0.0.0)
  GOOSE_WEB_PORT       port                      (default 8799)
  GOOSE_WEB_TOKEN      shared secret; if set, /api/chat requires it
  GOOSE_WEB_WORKSPACE  agent working directory   (default ../workspace)
  GOOSE_WEB_MAXTURNS   --max-turns per turn      (default 50)
  GOOSE_WEB_TIMEOUT    hard wall-clock kill (s)  (default 1800)
  GOOSE_WEB_MODEL      model name shown in UI
  GOOSE_WEB_CONFIG     path to the config.json   (default: ./config.json)
  GOOSE_BIN            path to goose binary      (default: which goose / ~/.local/bin/goose)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOME = Path.home()

# ---- central config (config.json next to this script); env vars override it ----
# Shared with the PowerShell port (server.ps1) -- keep the schema in sync.
_DEFAULTS = {
    "host": "0.0.0.0",
    "port": 8799,
    "token": "",
    "workspace": str(HERE.parent / "workspace"),
    "max_turns": 50,
    "timeout_seconds": 1800,
    "goose_bin": "",
    "model": "qwen-3.6-chat",
    "provider_label": "vLLM (OpenAI-compat)",
    "backends": [
        {"name": "vLLM chat", "url": "http://192.168.86.44:8000", "health_path": "/v1/models", "role": "chat"},
        {"name": "vLLM embed", "url": "http://192.168.86.44:8001", "health_path": "/v1/models", "role": "embed"},
        {"name": "Ollama", "url": "http://192.168.86.44:11434", "health_path": "/api/tags", "role": "ollama"},
    ],
}


def _load_web_config() -> dict:
    """Defaults <- config.json <- GOOSE_WEB_* env (env wins, for serve_web.sh back-compat)."""
    cfg = dict(_DEFAULTS)
    path = Path(os.environ.get("GOOSE_WEB_CONFIG", str(HERE / "config.json")))
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            cfg.update({k: v for k, v in loaded.items() if not k.startswith("_")})
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        print(f"[goose_web] WARNING: could not read {path}: {e}; using defaults/env")
    env = os.environ.get
    cfg["host"] = env("GOOSE_WEB_HOST", cfg["host"])
    cfg["port"] = int(env("GOOSE_WEB_PORT", cfg["port"]))
    cfg["token"] = str(env("GOOSE_WEB_TOKEN", cfg["token"])).strip()
    cfg["workspace"] = env("GOOSE_WEB_WORKSPACE", cfg["workspace"])
    cfg["max_turns"] = str(env("GOOSE_WEB_MAXTURNS", cfg["max_turns"]))
    cfg["timeout_seconds"] = int(env("GOOSE_WEB_TIMEOUT", cfg["timeout_seconds"]))
    cfg["model"] = env("GOOSE_WEB_MODEL", cfg["model"])
    if env("GOOSE_BIN"):
        cfg["goose_bin"] = env("GOOSE_BIN")
    return cfg


WEBCFG = _load_web_config()

HOST = WEBCFG["host"]
PORT = int(WEBCFG["port"])
TOKEN = str(WEBCFG["token"]).strip()
WORKSPACE = Path(WEBCFG["workspace"]).resolve()
MAX_TURNS = str(WEBCFG["max_turns"])
MAX_WALL_SECONDS = int(WEBCFG["timeout_seconds"])


def _find_goose() -> str:
    cand = WEBCFG.get("goose_bin") or os.environ.get("GOOSE_BIN")
    if cand and os.access(cand, os.X_OK):
        return cand
    for p in (HOME / ".local/bin/goose", Path("/usr/local/bin/goose")):
        if os.access(p, os.X_OK):
            return str(p)
    # fall back to PATH lookup
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d) / "goose"
        if os.access(p, os.X_OK):
            return str(p)
    return "goose"


GOOSE_BIN = _find_goose()


def _chat_url() -> str:
    """The backend whose role is 'chat' (for the displayed provider host)."""
    for b in WEBCFG["backends"]:
        if b.get("role") == "chat":
            return b["url"].rstrip("/")
    return WEBCFG["backends"][0]["url"].rstrip("/") if WEBCFG["backends"] else ""


# Cache the goose version once at startup (advisor: do not fork a 278 MB binary per health poll).
def _goose_version() -> str:
    try:
        out = subprocess.run([GOOSE_BIN, "--version"], capture_output=True, text=True, timeout=15)
        return (out.stdout + out.stderr).strip().split("\n")[0].strip()
    except Exception:
        return "unknown"


GOOSE_VERSION = _goose_version()

# Tools surfaced by the configured extensions (for the sidebar).
TOOLS = [
    {"group": "developer", "name": "shell / write / edit", "desc": "run commands, read & edit files"},
    {"group": "memory", "name": "remember / retrieve", "desc": "persistent key/value memory (MCP)"},
    {"group": "dtm", "name": "dtm_query", "desc": "auto-route a DTM question"},
    {"group": "dtm", "name": "dtm_telemetry_lookup", "desc": "datatypes/fields/plugins for a data need"},
    {"group": "dtm", "name": "dtm_triage", "desc": "triage a Windows issue from telemetry + Jira history"},
    {"group": "dtm", "name": "dtm_data_feature", "desc": "deep-dive a DTM plugin"},
    {"group": "dtm", "name": "dtm_hw_spec", "desc": "hardware / platform spec lookup"},
    {"group": "dtm", "name": "dtm_health", "desc": "DTM agent health"},
]

# ---- per-session state: which sessions exist (=> use -r), and a serialize lock ----
_seen_lock = threading.Lock()
_seen_sessions: set[str] = set()
_session_locks: dict[str, threading.Lock] = {}


def _session_lock(name: str) -> threading.Lock:
    with _seen_lock:
        lk = _session_locks.get(name)
        if lk is None:
            lk = _session_locks[name] = threading.Lock()
        return lk


# ---- output cleaning + parsing ----
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
_MASCOT = re.compile(r"__\(|\\____\)|^\s*L L\s|goose is ready|● (?:new session|resuming)")
_RULE = re.compile(r"^[\s─━—_-]*$")  # box-drawing / dashes / blank
_TOOL = re.compile(r"^\s*▸\s+(.+?)\s*$")


def _clean(line: str) -> str:
    return _ANSI.sub("", line).replace("\r", "").rstrip("\n")


def _url_ok(url: str, timeout: float = 4.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def _health() -> dict:
    backends = []
    for b in WEBCFG["backends"]:
        url = b["url"].rstrip("/")
        hp = b.get("health_path", "/")
        backends.append({"name": b.get("name", url), "detail": url, "ok": _url_ok(url + hp)})
    return {
        "ok": True,
        "version": GOOSE_VERSION,
        "model": WEBCFG["model"],
        "provider": WEBCFG["provider_label"] + " @ " + _chat_url(),
        "workspace": str(WORKSPACE),
        "token_required": bool(TOKEN),
        "backends": backends,
        "tools": TOOLS,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GooseHarnessWeb"

    def log_message(self, fmt, *args):  # quieter logs
        pass

    # -- helpers --
    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str):
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _emit(self, obj) -> bool:
        """Write one NDJSON event; return False if the client has gone away."""
        try:
            self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    # -- routes --
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_file(HERE / "index.html", "text/html; charset=utf-8")
        elif path == "/api/health":
            self._send_json(_health())
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/chat":
            self._send_json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "bad json"}, 400)
            return

        # token gate
        if TOKEN:
            supplied = self.headers.get("X-Goose-Token", "")
            if not supplied:
                from urllib.parse import urlparse, parse_qs
                supplied = parse_qs(urlparse(self.path).query).get("token", [""])[0]
            if supplied != TOKEN:
                self._send_json({"error": "unauthorized"}, 401)
                return

        session = str(req.get("session") or "web").strip()[:80] or "web"
        session = re.sub(r"[^A-Za-z0-9_.-]", "_", session)
        message = str(req.get("message") or "").strip()
        mode = "chat" if req.get("mode") == "chat" else "auto"
        if not message:
            self._send_json({"error": "empty message"}, 400)
            return

        self._stream_chat(session, message, mode)

    # -- the streaming chat run --
    def _stream_chat(self, session: str, message: str, mode: str):
        # decide resume vs first turn
        with _seen_lock:
            resume = session in _seen_sessions
            _seen_sessions.add(session)

        cmd = [GOOSE_BIN, "run", "-n", session, "--max-turns", str(MAX_TURNS)]
        if resume:
            cmd.append("-r")
        cmd += ["-t", message]

        env = dict(os.environ)
        env["GOOSE_MODE"] = mode
        env["PATH"] = str(HOME / ".local/bin") + os.pathsep + env.get("PATH", "")
        WORKSPACE.mkdir(parents=True, exist_ok=True)

        # start the streaming response
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        lock = _session_lock(session)
        if not lock.acquire(timeout=MAX_WALL_SECONDS):
            self._emit({"type": "error", "text": "session busy"})
            self._emit({"type": "done", "code": -1})
            return

        proc = None
        try:
            self._emit({"type": "meta", "session": session, "resume": resume, "mode": mode})
            proc = subprocess.Popen(
                cmd, cwd=str(WORKSPACE), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )

            # watchdog: hard wall-clock kill
            def _watchdog(p):
                t0 = time.time()
                while p.poll() is None:
                    if time.time() - t0 > MAX_WALL_SECONDS:
                        p.kill()
                        return
                    time.sleep(1.0)
            threading.Thread(target=_watchdog, args=(proc,), daemon=True).start()

            in_tool = False
            tool_buf: list[str] = []
            alive = True

            def flush_tool():
                nonlocal in_tool, tool_buf
                if in_tool:
                    if tool_buf:
                        self._emit({"type": "tool_args", "text": "\n".join(tool_buf)})
                    in_tool = False
                    tool_buf = []

            # readline() streams as soon as goose flushes a line (verified: pipe is line-buffered)
            for raw_line in iter(proc.stdout.readline, ""):
                line = _clean(raw_line)
                if _MASCOT.search(line):
                    continue
                m = _TOOL.match(line)
                if m:
                    flush_tool()
                    parts = m.group(1).split()
                    name = parts[0]
                    ext = parts[1] if len(parts) > 1 else ""
                    in_tool = True
                    tool_buf = []
                    alive = self._emit({"type": "tool_start", "name": name, "ext": ext})
                    if not alive:
                        break
                    continue
                if in_tool:
                    if line.strip() == "":
                        flush_tool()
                        continue
                    if raw_line[:1] in (" ", "\t"):
                        tool_buf.append(line.strip())
                        continue
                    flush_tool()  # tool block ended; fall through to treat as text
                if _RULE.match(line):
                    continue
                alive = self._emit({"type": "text", "text": line + "\n"})
                if not alive:
                    break

            flush_tool()
            if alive:
                proc.wait()
                self._emit({"type": "done", "code": proc.returncode})
        except Exception as e:  # noqa: BLE001
            self._emit({"type": "error", "text": str(e)})
            self._emit({"type": "done", "code": -1})
        finally:
            if proc and proc.poll() is None:
                proc.kill()
            lock.release()


def main():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    bind_public = HOST not in ("127.0.0.1", "localhost", "::1")
    print("=" * 64)
    print(f"  Goose Harness Web  ->  http://{HOST}:{PORT}")
    print(f"  goose     : {GOOSE_VERSION}  ({GOOSE_BIN})")
    print(f"  model     : {WEBCFG['model']}  via {_chat_url()}")
    print(f"  workspace : {WORKSPACE}")
    print(f"  token     : {'required' if TOKEN else 'NONE'}")
    print("=" * 64)
    if bind_public and not TOKEN:
        print("  \033[33m[!] SECURITY: bound to a public interface with GOOSE_MODE=auto and")
        print("      no token. Anyone who can reach this port can run shell commands on")
        print("      this box via the agent. Set GOOSE_WEB_TOKEN=<secret> to require a")
        print("      token, or bind GOOSE_WEB_HOST=127.0.0.1 for local-only use.\033[0m")
        print("=" * 64)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
