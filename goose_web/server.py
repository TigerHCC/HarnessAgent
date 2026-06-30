#!/usr/bin/env python3
"""Goose Harness Web -- a thin HTTP bridge that drives `goose run` and streams to a browser.

Single-file, stdlib-only (no pip). Lets you use the local Goose harness agent
(qwen-3.6-chat on the GB10 box, with the developer / memory / dtm MCP tools)
from any browser on the LAN.

Endpoints
  GET  /                 the web UI (index.html, same directory)
  GET  /api/health       JSON: model + backend status + live-discovered MCP
                         extensions + flat tool list (served from a cached snapshot)
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
  GOOSE_CONFIG         path to goose's config.yaml used for live MCP tool
                       discovery (default: OS path, see _goose_config_path)
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
from urllib.parse import urlparse

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
    "max_upload_mb": 25,
    "uploads_subdir": "uploads",
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
UPLOADS_SUBDIR = str(WEBCFG.get("uploads_subdir", "uploads")).strip("/\\") or "uploads"
MAX_UPLOAD_MB = int(os.environ.get("GOOSE_WEB_MAX_UPLOAD_MB", WEBCFG.get("max_upload_mb", 25)))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


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

# ---------------------------------------------------------------------------
# Live MCP tool discovery
# ---------------------------------------------------------------------------
# The sidebar tool list is discovered LIVE from goose's own config.yaml instead
# of being hardcoded. We parse the `extensions:` block (a tiny YAML subset -- no
# PyYAML, stdlib only), then handshake each enabled extension for its real tool
# set:
#   builtin (developer) -> curated static list (developer is in-process and NOT
#                          handshakeable: `goose mcp developer` is invalid)
#   stdio               -> spawn cmd+args, newline-delimited JSON-RPC handshake
#   streamable_http     -> urllib POST initialize / initialized / tools/list
# Results are cached behind a lock; /api/health serves the snapshot and NEVER
# blocks on a handshake. A daemon thread refreshes every REFRESH_SECONDS.

REFRESH_SECONDS = 90  # background re-discovery interval

# developer is in-process (not an MCP server); curated to match its real tools.
_BUILTIN_DEVELOPER_TOOLS = [
    {"name": "shell", "description": "Run a shell command"},
    {"name": "text_editor", "description": "View, write, and edit files"},
]

_disc_lock = threading.Lock()
_disc_extensions: list[dict] = []  # /api/health "extensions" (config order)
_disc_tools: list[dict] = []       # /api/health "tools" (flat, grouped by ext)


def _goose_config_path() -> Path:
    """Path to goose's config.yaml (source of truth for connected extensions)."""
    override = os.environ.get("GOOSE_CONFIG")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(HOME / "AppData" / "Roaming")
        return Path(base) / "Block" / "goose" / "config" / "config.yaml"
    return HOME / ".config" / "goose" / "config.yaml"


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _parse_goose_extensions(text: str) -> list[dict]:
    """Minimal YAML-subset parser for the `extensions:` subtree of config.yaml.

    Returns extension dicts in config order, each with a subset of:
    id, type, enabled (bool), name, cmd, uri, args (list). Enabled filtering is
    left to the caller. Only the `extensions:` block is parsed; everything else
    is ignored. Assumes goose's 2-space indent, full-line `#` comments, and
    unquoted scalar values (no inline comments on values).
    """
    exts: list[dict] = []
    cur: dict | None = None
    in_block = False
    in_args = False
    args_indent = -1
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if not in_block:
            if indent == 0 and stripped == "extensions:":
                in_block = True
            continue
        if indent == 0:  # next column-0 key ends the extensions block
            break
        if in_args:
            if stripped.startswith("-") and indent > args_indent:
                if cur is not None:
                    cur.setdefault("args", []).append(_unquote(stripped[1:].strip()))
                continue
            in_args = False  # list ended; reinterpret this line below
        if indent == 2 and stripped.endswith(":"):
            cur = {"id": stripped[:-1].strip()}
            exts.append(cur)
            continue
        if indent >= 4 and cur is not None:
            if stripped == "args:" or stripped.startswith("args:"):
                cur["args"] = []
                in_args = True
                args_indent = indent
                continue
            key, sep, val = stripped.partition(":")
            if not sep:
                continue
            key = key.strip()
            val = val.strip()
            if key in ("type", "name", "cmd", "uri"):
                cur[key] = _unquote(val)
            elif key == "enabled":
                cur["enabled"] = (val.lower() == "true")
    return exts


def _host_port(uri: str) -> str:
    """host:port for an http uri (the `detail` field); "" if unparseable."""
    try:
        return urlparse(uri).netloc or ""
    except Exception:
        return ""


def _short_desc(desc: str) -> str:
    """Trim an MCP tool description to its first sentence or ~80 chars."""
    s = re.sub(r"\s+", " ", (desc or "")).strip()
    if not s:
        return ""
    m = re.search(r"\. ", s)
    if m:
        s = s[: m.start() + 1]
    if len(s) > 80:
        s = s[:79].rstrip() + "…"
    return s


def _extract_jsonrpc(raw: str) -> dict | None:
    """Parse a JSON-RPC reply that is either raw JSON or an SSE `data:` line."""
    raw = raw.strip()
    if not raw:
        return None
    if raw[0] == "{":
        try:
            return json.loads(raw)
        except ValueError:
            pass
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload.startswith("{"):
                try:
                    return json.loads(payload)
                except ValueError:
                    continue
    return None


class _MCPRedirect(urllib.request.HTTPRedirectHandler):
    """Follow 307/308 on POST, preserving method+body.

    The local srum/eventlog MCP servers answer `/mcp` with a 307 to `/mcp/`
    (Starlette redirect_slashes); urllib refuses to re-POST those by default.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code in (307, 308):
            return urllib.request.Request(
                newurl, data=req.data,
                headers=dict(req.header_items()), method=req.get_method())
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_HTTP_OPENER = urllib.request.build_opener(_MCPRedirect)


def _http_post(uri: str, headers: dict, payload: bytes, timeout: float):
    return _HTTP_OPENER.open(
        urllib.request.Request(uri, data=payload, headers=headers, method="POST"),
        timeout=timeout,
    )


def _discover_streamable_http(uri: str, timeout: float = 9.0) -> list[dict]:
    """initialize -> capture Mcp-Session-Id -> initialized -> tools/list."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "goose_web", "version": "1"},
        },
    }).encode("utf-8")
    with _http_post(uri, headers, init, timeout) as r:
        session_id = r.getheader("Mcp-Session-Id")
        endpoint = r.geturl() or uri  # honor a trailing-slash redirect for later calls
        r.read()
    call_headers = dict(headers)
    if session_id:
        call_headers["Mcp-Session-Id"] = session_id
    # initialized notification -- no response expected; ignore errors
    try:
        note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode("utf-8")
        with _http_post(endpoint, call_headers, note, timeout) as r:
            r.read()
    except Exception:
        pass
    body = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).encode("utf-8")
    with _http_post(endpoint, call_headers, body, timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    data = _extract_jsonrpc(raw)
    if data is None:
        raise ValueError("unparseable tools/list response")
    return (data.get("result") or {}).get("tools") or []


def _discover_stdio(cmd: str, args: list[str], timeout: float = 20.0):
    """Spawn cmd+args, run a newline JSON-RPC handshake, return tools or None.

    Drains stderr asynchronously (pipe-deadlock guard) and kills the child when
    done. Returns a (possibly empty) tool list on success, or None on failure.
    """
    if not cmd:
        return None
    try:
        proc = subprocess.Popen(
            [cmd] + list(args),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except Exception:
        return None

    def _drain(stream):
        try:
            for _ in iter(stream.readline, ""):
                pass
        except Exception:
            pass

    threading.Thread(target=_drain, args=(proc.stderr,), daemon=True).start()

    result: dict = {}

    def _run():
        try:
            init = json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "goose_web", "version": "1"},
                },
            })
            note = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            lst = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            proc.stdin.write(init + "\n")
            proc.stdin.write(note + "\n")
            proc.stdin.write(lst + "\n")
            proc.stdin.flush()
            for raw_line in iter(proc.stdout.readline, ""):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                res = msg.get("result")
                if isinstance(res, dict) and "tools" in res:
                    result["tools"] = res.get("tools") or []
                    return
        except Exception:
            pass

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout)
    try:
        proc.kill()
    except Exception:
        pass
    return result.get("tools")


def _discover_extension(e: dict) -> tuple[str, str, list[dict]]:
    """Return (status, detail, tools) for one parsed, enabled extension dict."""
    typ = e.get("type", "")
    ext_id = e.get("id", "")
    if typ == "builtin":
        # developer is NOT handshakeable (`goose mcp developer` is invalid) -> static.
        if ext_id == "developer":
            return "builtin", "", _BUILTIN_DEVELOPER_TOOLS
        # other bundled servers (memory, computercontroller, ...) run in-process at
        # chat time but are still introspectable via `goose mcp <id>` over stdio.
        res = _discover_stdio(GOOSE_BIN, ["mcp", ext_id])
        return ("builtin", "", res) if res is not None else ("offline", "", [])
    if typ == "streamable_http":
        detail = _host_port(e.get("uri", ""))
        try:
            return "ok", detail, _discover_streamable_http(e.get("uri", ""))
        except Exception:
            return "offline", detail, []
    if typ == "stdio":
        res = _discover_stdio(e.get("cmd", ""), e.get("args") or [])
        if res is None:
            return "offline", "", []
        return "ok", "", res
    return "offline", "", []


def _build_snapshot(handshake: bool) -> tuple[list[dict], list[dict]]:
    """Build (extensions, tools) from config.yaml.

    With handshake=False this is the cheap startup seed: stdio/http extensions get
    status 'checking' and 0 tools (builtin is always resolved, it needs no I/O).
    With handshake=True each non-builtin extension is queried for its real tools.
    """
    try:
        parsed = _parse_goose_extensions(_goose_config_path().read_text(encoding="utf-8"))
    except Exception as ex:  # noqa: BLE001
        print(f"[goose_web] WARNING: could not read goose config.yaml: {ex}")
        parsed = []
    exts_meta: list[dict] = []
    tools: list[dict] = []
    for e in parsed:
        if not e.get("enabled"):
            continue
        typ = e.get("type", "")
        ext_id = e.get("id", "")
        name = e.get("name") or ext_id
        if (typ == "builtin" and ext_id == "developer") or handshake:
            status, detail, discovered = _discover_extension(e)
        else:  # seed pass: don't block startup on stdio/http/builtin handshakes
            status = "checking"
            detail = _host_port(e.get("uri", "")) if typ == "streamable_http" else ""
            discovered = []
        for t in discovered:
            if not isinstance(t, dict):
                continue
            nm = t.get("name")
            if not nm:
                continue
            tools.append({"group": ext_id, "name": nm, "desc": _short_desc(t.get("description", ""))})
        cnt = sum(1 for t in discovered if isinstance(t, dict) and t.get("name"))
        exts_meta.append({
            "id": ext_id, "name": name, "transport": typ,
            "status": status, "count": cnt, "detail": detail,
        })
    return exts_meta, tools


def _refresh_discovery(handshake: bool = True) -> None:
    exts_meta, tools = _build_snapshot(handshake)
    with _disc_lock:
        _disc_extensions[:] = exts_meta
        _disc_tools[:] = tools


def _discovery_loop() -> None:
    while True:
        try:
            _refresh_discovery(handshake=True)
        except Exception as ex:  # noqa: BLE001
            print(f"[goose_web] discovery refresh error: {ex}")
        time.sleep(REFRESH_SECONDS)


def _start_discovery() -> None:
    """Seed the cache synchronously (cheap), then handshake in the background."""
    _refresh_discovery(handshake=False)
    threading.Thread(target=_discovery_loop, name="mcp-discovery", daemon=True).start()

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
    with _disc_lock:  # serve the live-discovery snapshot; never block on a handshake
        extensions = [dict(x) for x in _disc_extensions]
        tools = [dict(x) for x in _disc_tools]
    return {
        "ok": True,
        "version": GOOSE_VERSION,
        "model": WEBCFG["model"],
        "provider": WEBCFG["provider_label"] + " @ " + _chat_url(),
        "workspace": str(WORKSPACE),
        "token_required": bool(TOKEN),
        "backends": backends,
        "extensions": extensions,
        "tools": tools,
    }


# ---- uploads: filename safety + attachment message composition ----
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ ()-]")


def _safe_session(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", str(s or "").strip())[:80]
    s = s.strip(".")  # no leading/trailing dots -> kills "." / ".." traversal
    return s or "web"


def _safe_name(name: str) -> str:
    name = str(name or "").replace("\\", "/").split("/")[-1].strip()
    name = _SAFE_NAME_RE.sub("_", name).lstrip(".").strip()
    return name[:150].strip() or "file"


def _human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{int(f)} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024


def _uploads_root() -> Path:
    return (WORKSPACE / UPLOADS_SUBDIR).resolve()


def _session_upload_dir(session: str) -> Path:
    root = _uploads_root()
    d = (root / _safe_session(session)).resolve()
    if d != root and root not in d.parents:
        raise ValueError("upload path escapes workspace")
    return d


def _unique_path(d: Path, name: str) -> Path:
    p = d / name
    if not p.exists():
        return p
    stem, dot, ext = name.rpartition(".")
    base, suffix = (stem, "." + ext) if dot else (name, "")
    i = 1
    while (d / f"{base} ({i}){suffix}").exists():
        i += 1
    return d / f"{base} ({i}){suffix}"


def _compose_message(message: str, session: str, attachments) -> str:
    names = [a for a in (attachments or []) if isinstance(a, str)]
    if not names:
        return message
    d = _session_upload_dir(session)
    sub = f"{UPLOADS_SUBDIR}/{_safe_session(session)}"
    lines = []
    for n in names:
        sn = _safe_name(n)
        p = d / sn
        if p.is_file():
            lines.append(f"- {sub}/{sn} ({_human_size(p.stat().st_size)})")
    if not lines:
        return message
    body = message or "請查看我附加的檔案。"
    return body + "\n\n[附加檔案 (相對於工作目錄):]\n" + "\n".join(lines)


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
        """Write one NDJSON event; return False if the client has gone away.

        Thread-safe when self._wlock is set: the keepalive pinger and the main
        stream loop both call this concurrently.
        """
        lock = getattr(self, "_wlock", None)
        if lock is not None:
            lock.acquire()
        try:
            self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False
        finally:
            if lock is not None:
                lock.release()

    # -- routes --
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_file(HERE / "index.html", "text/html; charset=utf-8")
        elif path == "/api/health":
            self._send_json(_health())
        else:
            self._send_json({"error": "not found"}, 404)

    def _auth_ok(self) -> bool:
        if not TOKEN:
            return True
        from urllib.parse import urlparse, parse_qs
        supplied = self.headers.get("X-Goose-Token", "") or \
            parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return supplied == TOKEN

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/chat", "/api/upload"):
            self._send_json({"error": "not found"}, 404)
            return
        if not self._auth_ok():
            self._send_json({"error": "unauthorized"}, 401)
            return
        if path == "/api/upload":
            self._handle_upload()
            return
        # ---- /api/chat ----
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "bad json"}, 400)
            return
        session = _safe_session(req.get("session") or "web")
        message = str(req.get("message") or "").strip()
        mode = "chat" if req.get("mode") == "chat" else "auto"
        message = _compose_message(message, session, req.get("attachments") or [])
        if not message:
            self._send_json({"error": "empty message"}, 400)
            return
        self._stream_chat(session, message, mode)

    def _handle_upload(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        session = qs.get("session", ["web"])[0]
        name = _safe_name(qs.get("name", [""])[0])
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            self._send_json({"error": "empty body"}, 400)
            return
        if length > MAX_UPLOAD_BYTES:
            self._send_json({"error": f"file too large (> {MAX_UPLOAD_MB} MB)"}, 413)
            return
        dest = None
        try:
            d = _session_upload_dir(session)
            d.mkdir(parents=True, exist_ok=True)
            dest = _unique_path(d, name)
            remaining, written = length, 0
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
        except Exception as e:  # noqa: BLE001
            if dest is not None:
                try:
                    dest.unlink()
                except Exception:
                    pass
            self._send_json({"error": str(e)}, 400)
            return
        self._send_json({"ok": True, "name": dest.name, "size": written})

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
        env["GOOSE_TELEMETRY_ENABLED"] = "false"  # privacy: never upload usage telemetry (env overrides config)
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

        self._wlock = threading.Lock()
        ping_stop = threading.Event()
        proc = None
        try:
            self._emit({"type": "meta", "session": session, "resume": resume, "mode": mode})
            proc = subprocess.Popen(
                cmd, cwd=str(WORKSPACE), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
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

            # keepalive: goose can stay silent 100s+ during tool runs; ping every ~5s so bytes
            # keep flowing under even aggressive mobile/cellular/VPN (Tailscale) NAT idle
            # timeouts (~10-15s) that a slower ping would miss.
            def _pinger():
                while not ping_stop.wait(5):
                    if not self._emit({"type": "ping"}):
                        return
            threading.Thread(target=_pinger, daemon=True).start()

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
            ping_stop.set()
            if proc and proc.poll() is None:
                proc.kill()
            lock.release()


def main():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    _start_discovery()  # seed tool cache now; handshake extensions in the background
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
