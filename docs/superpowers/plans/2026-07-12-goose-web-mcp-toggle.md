# Goose Web Per-MCP Enable/Disable Toggle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on/off toggle to each Windows diagnostic MCP card in the goose_web UI that flips goose's `config.yaml` `enabled:` flag, honored on the next chat turn with no restart.

**Architecture:** goose_web is a stdlib HTTP server with two parity implementations — `server.py` (Python) and `server.ps1` (PowerShell/HttpListener, the one live on this box) — plus a shared `index.html`. Both already parse goose's `config.yaml` to discover extensions and cache a snapshot for `/api/health`. We add: (1) a server-side togglable predicate = loopback `streamable_http`; (2) a surgical/atomic config writer that flips one `enabled:` line; (3) a `POST /api/extensions/toggle` endpoint; (4) health-snapshot changes that surface disabled togglable extensions with `enabled`/`togglable` fields; (5) a UI switch per togglable card. Because every chat turn spawns a fresh `goose run` that re-reads `config.yaml`, the flag takes effect on the next turn.

**Tech Stack:** Python 3 stdlib (no pip), Windows PowerShell 5.1 (.NET HttpListener), vanilla HTML/CSS/JS, pytest/unittest.

## Global Constraints

- **Python: stdlib only.** No new pip dependencies. `server.py` must stay importable without side effects beyond the existing ones.
- **PowerShell: Windows PowerShell 5.1 compatible.** No PS7-only syntax (`&&`, `??`, `?.`, ternary). Files written with a UTF-8 encoder **without BOM** (`New-Object System.Text.UTF8Encoding($false)`), matching the existing `server.ps1` convention.
- **Parity:** every behavior change lands in **both** `server.py` and `server.ps1`; the UI change lands once in the shared `index.html`. Identical HTTP contract.
- **Togglable predicate (verbatim):** an extension is togglable iff `type == "streamable_http"` AND its `uri` host ∈ {`127.0.0.1`, `localhost`, `::1`}. Enforced server-side; refuse others with 403.
- **Read-only vs the system:** the only file the feature ever writes is goose's `config.yaml` (one `enabled:` value), plus its backup `config.yaml.bak-webtoggle` and a same-dir `config.yaml.tmp` scratch file. Never starts/stops MCP server processes.
- **Config write safety:** back up to `config.yaml.bak-webtoggle` before the first edit (only if it doesn't already exist); write to a temp file then atomically replace; clear-then-restore the read-only attribute if set.
- **Auth:** `/api/extensions/toggle` uses the same token gate as `/api/chat` (`X-Goose-Token` header or `?token=`).
- **Commit trailers** (every commit):
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01XPrJ2LtFTYawxzecfDxaqj
  ```

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `goose_web/server.py` | Python server | predicate `_is_togglable`, writer `_set_extension_enabled` + `_atomic_write_config`, `_toggle_extension`, `_build_snapshot` changes, `_handle_toggle` route |
| `goose_web/mcp_toggle.ps1` | **new** — PowerShell togglable predicate + config writer, dot-sourceable and injectable into runspaces | `Test-Togglable`, `Set-ExtensionEnabled` |
| `goose_web/server.ps1` | PowerShell server | load `mcp_toggle.ps1` text; add `enabled`/`togglable` to ext entries + stop filtering disabled-togglable; `Handle-Toggle` + route; pass config path + fns to workers |
| `goose_web/index.html` | shared UI | toggle-switch CSS, switch in `renderExt`, `postToggle`, render disabled-togglable cards |
| `goose_web/tests/test_toggle.py` | **new** — Python unit tests | predicate, writer, snapshot shape, `_toggle_extension` |
| `goose_web/tests/test_toggle_ps.py` | **new** — PowerShell writer tests via subprocess | predicate + writer round-trip (skips if no powershell) |
| `goose_web/README.md` | docs | document the toggle + `config.yaml.bak-webtoggle` |

---

### Task 1: Python togglable predicate

**Files:**
- Modify: `goose_web/server.py` (add near `_host_port`, ~line 253)
- Test: `goose_web/tests/test_toggle.py` (new)

**Interfaces:**
- Produces: `_is_togglable(e: dict) -> bool` — True iff `e["type"]=="streamable_http"` and `urlparse(e["uri"]).hostname` is loopback.

- [ ] **Step 1: Write the failing test**

Create `goose_web/tests/test_toggle.py`:

```python
import os, sys, tempfile, unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="gw_toggle_")
os.environ["GOOSE_WEB_WORKSPACE"] = _TMP
os.environ["GOOSE_WEB_HOST"] = "127.0.0.1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402


class Togglable(unittest.TestCase):
    def test_loopback_streamable_http_is_togglable(self):
        self.assertTrue(server._is_togglable({"type": "streamable_http", "uri": "http://127.0.0.1:8777/mcp"}))
        self.assertTrue(server._is_togglable({"type": "streamable_http", "uri": "http://localhost:8788/mcp"}))

    def test_remote_and_builtin_not_togglable(self):
        self.assertFalse(server._is_togglable({"type": "streamable_http", "uri": "http://192.168.86.44:8765/mcp"}))
        self.assertFalse(server._is_togglable({"type": "builtin"}))
        self.assertFalse(server._is_togglable({"type": "stdio", "cmd": "x"}))
        self.assertFalse(server._is_togglable({"type": "streamable_http", "uri": ""}))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd goose_web && python -m pytest tests/test_toggle.py::Togglable -q`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_is_togglable'`

- [ ] **Step 3: Add the implementation**

In `goose_web/server.py`, immediately after the `_host_port` function (ends ~line 258), add:

```python
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_togglable(e: dict) -> bool:
    """True iff a loopback streamable_http MCP (the windows_* diagnostic suite).

    The 12 local diagnostic servers are all streamable_http on 127.0.0.1; dtm is
    streamable_http but remote, and developer/memory/computercontroller are builtin.
    """
    if e.get("type") != "streamable_http":
        return False
    host = (urlparse(e.get("uri", "")).hostname or "").lower()
    return host in _LOOPBACK_HOSTS
```

(`urlparse` is already imported at the top of `server.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd goose_web && python -m pytest tests/test_toggle.py::Togglable -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add goose_web/server.py goose_web/tests/test_toggle.py
git commit -m "feat(goose_web): togglable predicate for loopback streamable_http MCPs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XPrJ2LtFTYawxzecfDxaqj"
```

---

### Task 2: Python config writer

**Files:**
- Modify: `goose_web/server.py` (add after `_is_togglable`)
- Test: `goose_web/tests/test_toggle.py`

**Interfaces:**
- Consumes: `_goose_config_path()` (existing), `_parse_goose_extensions` (existing).
- Produces:
  - `_set_extension_enabled(ext_id: str, enabled: bool) -> bool` — flips the `enabled:` line for `ext_id` in `_goose_config_path()`; returns `True` if the file changed, `False` on idempotent no-op; raises `KeyError` if `ext_id` absent.
  - `_atomic_write_config(path: Path, content: str) -> None` — backup + read-only-safe + atomic replace.

- [ ] **Step 1: Write the failing tests**

Append to `goose_web/tests/test_toggle.py` (before the `if __name__` line):

```python
_FIXTURE = """GOOSE_PROVIDER: openai

extensions:
  developer:
    type: builtin
    enabled: true
  srum:
    type: streamable_http
    enabled: true
    uri: http://127.0.0.1:8777/mcp
  eventlog:
    type: streamable_http
    enabled: true
    uri: http://127.0.0.1:8778/mcp
  noflag:
    type: streamable_http
    uri: http://127.0.0.1:8790/mcp
"""


class Writer(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gw_cfg_")
        self.cfg = Path(self.d) / "config.yaml"
        self.cfg.write_text(_FIXTURE, encoding="utf-8", newline="")
        os.environ["GOOSE_CONFIG"] = str(self.cfg)

    def tearDown(self):
        os.environ.pop("GOOSE_CONFIG", None)

    def test_flip_true_to_false_only_target_line(self):
        changed = server._set_extension_enabled("srum", False)
        self.assertTrue(changed)
        txt = self.cfg.read_text(encoding="utf-8")
        self.assertIn("  srum:\n    type: streamable_http\n    enabled: false\n", txt)
        # eventlog + developer still true (3 remaining: developer, eventlog, and none else)
        self.assertEqual(txt.count("enabled: true"), 2)
        self.assertEqual(txt.count("enabled: false"), 1)

    def test_round_trip_back_to_true(self):
        server._set_extension_enabled("srum", False)
        server._set_extension_enabled("srum", True)
        txt = self.cfg.read_text(encoding="utf-8")
        self.assertIn("  srum:\n    type: streamable_http\n    enabled: true\n", txt)

    def test_idempotent_noop(self):
        self.assertFalse(server._set_extension_enabled("srum", True))  # already true

    def test_insert_when_no_enabled_line(self):
        changed = server._set_extension_enabled("noflag", False)
        self.assertTrue(changed)
        txt = self.cfg.read_text(encoding="utf-8")
        self.assertIn("  noflag:\n    enabled: false\n    type: streamable_http\n", txt)

    def test_unknown_id_raises(self):
        with self.assertRaises(KeyError):
            server._set_extension_enabled("does_not_exist", False)

    def test_backup_created_once(self):
        bak = self.cfg.with_name(self.cfg.name + ".bak-webtoggle")
        server._set_extension_enabled("srum", False)
        self.assertTrue(bak.exists())
        first = bak.read_text(encoding="utf-8")
        server._set_extension_enabled("eventlog", False)
        self.assertEqual(bak.read_text(encoding="utf-8"), first)  # backup not overwritten

    def test_crlf_preserved(self):
        self.cfg.write_text(_FIXTURE.replace("\n", "\r\n"), encoding="utf-8", newline="")
        server._set_extension_enabled("srum", False)
        raw = self.cfg.read_bytes()
        self.assertNotIn(b"\r\r\n", raw)  # no doubled CR
        self.assertIn(b"    enabled: false\r\n", raw)

    def test_readonly_file_written_and_restored(self):
        import stat as _stat
        os.chmod(self.cfg, _stat.S_IREAD)  # simulate the durability read-only guard
        try:
            self.assertTrue(server._set_extension_enabled("srum", False))
            self.assertIn("enabled: false", self.cfg.read_text(encoding="utf-8"))
            self.assertFalse(os.access(self.cfg, os.W_OK))  # read-only bit restored
        finally:
            os.chmod(self.cfg, _stat.S_IWRITE)  # let tempdir cleanup remove it
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd goose_web && python -m pytest tests/test_toggle.py::Writer -q`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_set_extension_enabled'`

- [ ] **Step 3: Add the implementation**

Add `import stat` to the import block at the top of `goose_web/server.py` (next to `import os`). Then, after `_is_togglable`, add:

```python
def _atomic_write_config(path: Path, content: str) -> None:
    """Backup-once, read-only-safe, atomic replace of the config file."""
    bak = path.with_name(path.name + ".bak-webtoggle")
    if not bak.exists():
        try:
            bak.write_bytes(path.read_bytes())
        except OSError:
            pass
    was_ro = path.exists() and not os.access(path, os.W_OK)
    if was_ro:
        os.chmod(path, stat.S_IWRITE)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:  # newline="" -> no CRLF translation
        f.write(content)
    os.replace(tmp, path)
    if was_ro:
        os.chmod(path, stat.S_IREAD)


def _set_extension_enabled(ext_id: str, enabled: bool) -> bool:
    """Flip `enabled:` for one extension in goose's config.yaml.

    Returns True if the file was changed, False if it already had the requested
    value. Raises KeyError if ext_id is not an extension in the file. Only the
    single `enabled:` value is rewritten; all other bytes are preserved.
    """
    path = _goose_config_path()
    with open(path, "r", encoding="utf-8", newline="") as f:  # newline="" -> keep \r\n as-is
        lines = f.read().splitlines(keepends=True)
    want = "true" if enabled else "false"

    # 1) find the `  <ext_id>:` key line (indent 2) inside the `extensions:` block
    key_idx = None
    in_ext = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        indent = len(ln) - len(ln.lstrip(" "))
        if indent == 0:
            in_ext = (s == "extensions:")
            continue
        if in_ext and indent == 2 and s == f"{ext_id}:":
            key_idx = i
            break
    if key_idx is None:
        raise KeyError(f"extension {ext_id!r} not found in {path}")

    # 2) block body = key_idx+1 .. first later non-blank/comment line with indent <= 2
    block_end = len(lines)
    for j in range(key_idx + 1, len(lines)):
        s = lines[j].strip()
        if not s or s.startswith("#"):
            continue
        if (len(lines[j]) - len(lines[j].lstrip(" "))) <= 2:
            block_end = j
            break

    # 3) find an existing enabled: line inside the block
    enabled_idx = None
    for j in range(key_idx + 1, block_end):
        if lines[j].strip().startswith("enabled:"):
            enabled_idx = j
            break

    newline = "\r\n" if lines[key_idx].endswith("\r\n") else "\n"
    if enabled_idx is not None:
        cur = lines[enabled_idx].split(":", 1)[1].strip().lower()
        if cur == want:
            return False  # idempotent no-op
        indent = len(lines[enabled_idx]) - len(lines[enabled_idx].lstrip(" "))
        lines[enabled_idx] = " " * indent + f"enabled: {want}" + newline
    else:
        key_indent = len(lines[key_idx]) - len(lines[key_idx].lstrip(" "))
        lines.insert(key_idx + 1, " " * (key_indent + 2) + f"enabled: {want}" + newline)

    _atomic_write_config(path, "".join(lines))
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd goose_web && python -m pytest tests/test_toggle.py::Writer -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add goose_web/server.py goose_web/tests/test_toggle.py
git commit -m "feat(goose_web): surgical/atomic config.yaml enabled-flag writer

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XPrJ2LtFTYawxzecfDxaqj"
```

---

### Task 3: Python health snapshot surfaces disabled togglable extensions

**Files:**
- Modify: `goose_web/server.py` — `_build_snapshot` (lines ~467-491)
- Test: `goose_web/tests/test_toggle.py`

**Interfaces:**
- Consumes: `_is_togglable`, `_parse_goose_extensions`, `_discover_extension` (existing).
- Produces: each extension dict in the snapshot now carries `enabled: bool` and `togglable: bool`; disabled **togglable** extensions appear with `status:"disabled"`, `count:0`; disabled **non-togglable** extensions remain hidden.

- [ ] **Step 1: Write the failing test**

Append to `goose_web/tests/test_toggle.py`:

```python
class Snapshot(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gw_snap_")
        self.cfg = Path(self.d) / "config.yaml"
        # srum disabled+togglable (should show as 'disabled'); eventlog enabled
        txt = _FIXTURE.replace(
            "  srum:\n    type: streamable_http\n    enabled: true\n",
            "  srum:\n    type: streamable_http\n    enabled: false\n")
        self.cfg.write_text(txt, encoding="utf-8", newline="")
        os.environ["GOOSE_CONFIG"] = str(self.cfg)

    def tearDown(self):
        os.environ.pop("GOOSE_CONFIG", None)

    def test_disabled_togglable_appears_disabled(self):
        exts, _tools = server._build_snapshot(handshake=False)
        by_id = {e["id"]: e for e in exts}
        self.assertIn("srum", by_id)
        self.assertEqual(by_id["srum"]["status"], "disabled")
        self.assertFalse(by_id["srum"]["enabled"])
        self.assertTrue(by_id["srum"]["togglable"])
        self.assertEqual(by_id["srum"]["count"], 0)

    def test_enabled_carries_flags(self):
        exts, _ = server._build_snapshot(handshake=False)
        by_id = {e["id"]: e for e in exts}
        self.assertTrue(by_id["eventlog"]["enabled"])
        self.assertTrue(by_id["eventlog"]["togglable"])
        self.assertFalse(by_id["developer"]["togglable"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd goose_web && python -m pytest tests/test_toggle.py::Snapshot -q`
Expected: FAIL — `KeyError: 'togglable'` (or `srum` missing / not "disabled")

- [ ] **Step 3: Update `_build_snapshot`**

In `goose_web/server.py`, replace the loop body in `_build_snapshot` (currently starting `for e in parsed:` with `if not e.get("enabled"): continue`) with:

```python
    for e in parsed:
        enabled = bool(e.get("enabled"))
        togglable = _is_togglable(e)
        if not enabled and not togglable:
            continue  # hide disabled non-togglable (unchanged behavior)
        typ = e.get("type", "")
        ext_id = e.get("id", "")
        name = e.get("name") or ext_id
        if not enabled:
            status, detail, discovered = "disabled", _host_port(e.get("uri", "")), []
        elif (typ == "builtin" and ext_id == "developer") or handshake:
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
            "enabled": enabled, "togglable": togglable,
        })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd goose_web && python -m pytest tests/test_toggle.py -q`
Expected: PASS (all Task 1-3 tests)

- [ ] **Step 5: Commit**

```bash
git add goose_web/server.py goose_web/tests/test_toggle.py
git commit -m "feat(goose_web): surface disabled togglable MCPs in /api/health

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XPrJ2LtFTYawxzecfDxaqj"
```

---

### Task 4: Python toggle endpoint

**Files:**
- Modify: `goose_web/server.py` — add `_toggle_extension`; extend `do_POST`; add `_handle_toggle`
- Test: `goose_web/tests/test_toggle.py`

**Interfaces:**
- Consumes: `_parse_goose_extensions`, `_is_togglable`, `_set_extension_enabled`, `_refresh_discovery` (existing).
- Produces: `_toggle_extension(ext_id: str, enabled: bool) -> dict` returning `{"ok":True,"id":...,"enabled":...,"_status":200}` or `{"error":...,"_status":404|403}`. Route `POST /api/extensions/toggle`.

- [ ] **Step 1: Write the failing test**

Append to `goose_web/tests/test_toggle.py`:

```python
class ToggleEndpoint(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gw_tog_")
        self.cfg = Path(self.d) / "config.yaml"
        self.cfg.write_text(_FIXTURE, encoding="utf-8", newline="")
        os.environ["GOOSE_CONFIG"] = str(self.cfg)

    def tearDown(self):
        os.environ.pop("GOOSE_CONFIG", None)

    def test_valid_toggle_writes_and_returns_ok(self):
        res = server._toggle_extension("srum", False)
        self.assertEqual(res.get("_status"), 200)
        self.assertTrue(res["ok"])
        self.assertFalse(res["enabled"])
        self.assertIn("enabled: false", self.cfg.read_text(encoding="utf-8"))

    def test_unknown_extension_404(self):
        res = server._toggle_extension("nope", False)
        self.assertEqual(res.get("_status"), 404)

    def test_non_togglable_refused_403(self):
        res = server._toggle_extension("developer", False)  # builtin
        self.assertEqual(res.get("_status"), 403)
        self.assertNotIn("enabled: false", self.cfg.read_text(encoding="utf-8"))  # not written
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd goose_web && python -m pytest tests/test_toggle.py::ToggleEndpoint -q`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_toggle_extension'`

- [ ] **Step 3: Add `_toggle_extension`**

In `goose_web/server.py`, after `_refresh_discovery` (ends ~line 498), add:

```python
def _toggle_extension(ext_id: str, enabled: bool) -> dict:
    """Validate + flip one extension's enabled flag; kick a background rediscovery.

    Returns a dict with an internal `_status` HTTP code the caller strips before sending.
    """
    parsed = _parse_goose_extensions(_goose_config_path().read_text(encoding="utf-8"))
    match = next((e for e in parsed if e.get("id") == ext_id), None)
    if match is None:
        return {"error": "unknown extension", "_status": 404}
    if not _is_togglable(match):
        return {"error": "extension not togglable", "_status": 403}
    _set_extension_enabled(ext_id, enabled)
    threading.Thread(target=_refresh_discovery, kwargs={"handshake": True}, daemon=True).start()
    return {"ok": True, "id": ext_id, "enabled": enabled, "_status": 200}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd goose_web && python -m pytest tests/test_toggle.py::ToggleEndpoint -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Wire the HTTP route**

In `goose_web/server.py` `do_POST`, change the guard line (~705):

```python
        if path not in ("/api/chat", "/api/upload"):
```
to:
```python
        if path not in ("/api/chat", "/api/upload", "/api/extensions/toggle"):
```

Then, right after the existing `if path == "/api/upload":` block (~711-713), add:

```python
        if path == "/api/extensions/toggle":
            self._handle_toggle()
            return
```

And add this method to the `Handler` class (next to `_handle_upload`):

```python
    def _handle_toggle(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "bad json"}, 400)
            return
        ext_id = str(req.get("id") or "").strip()
        enabled = req.get("enabled")
        if not ext_id or not isinstance(enabled, bool):
            self._send_json({"error": "id (str) and enabled (bool) required"}, 400)
            return
        try:
            res = _toggle_extension(ext_id, enabled)
        except Exception as e:  # noqa: BLE001
            self._send_json({"error": str(e)}, 500)
            return
        self._send_json(res, res.pop("_status", 200))
```

(Auth: `do_POST` already calls `self._auth_ok()` for every path in the allowed set, so the toggle is token-gated with no extra code.)

- [ ] **Step 6: Verify full Python suite still passes**

Run: `cd goose_web && python -m pytest tests/ -q`
Expected: PASS (test_uploads + all test_toggle)

- [ ] **Step 7: Commit**

```bash
git add goose_web/server.py goose_web/tests/test_toggle.py
git commit -m "feat(goose_web): POST /api/extensions/toggle (server.py)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XPrJ2LtFTYawxzecfDxaqj"
```

---

### Task 5: PowerShell writer + predicate module (with tests)

**Files:**
- Create: `goose_web/mcp_toggle.ps1`
- Test: `goose_web/tests/test_toggle_ps.py` (new)

**Interfaces:**
- Produces (dot-sourceable PowerShell):
  - `Test-Togglable($e) -> [bool]` — mirrors `_is_togglable`.
  - `Set-ExtensionEnabled($configPath, $extId, [bool]$enabled) -> [bool]` — mirrors `_set_extension_enabled`; `$true` if changed, `$false` on no-op; throws if `$extId` absent.

- [ ] **Step 1: Write the failing test**

Create `goose_web/tests/test_toggle_ps.py`:

```python
import os, shutil, subprocess, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOGGLE = HERE.parent / "mcp_toggle.ps1"
PWSH = shutil.which("powershell") or shutil.which("pwsh")

_FIX = """GOOSE_PROVIDER: openai

extensions:
  developer:
    type: builtin
    enabled: true
  srum:
    type: streamable_http
    enabled: true
    uri: http://127.0.0.1:8777/mcp
  eventlog:
    type: streamable_http
    enabled: true
    uri: http://127.0.0.1:8778/mcp
"""


@unittest.skipUnless(PWSH and TOGGLE.exists(), "PowerShell or mcp_toggle.ps1 unavailable")
class PsToggle(unittest.TestCase):
    def _ps(self, script):
        full = ". '%s'; %s" % (str(TOGGLE), script)
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", full],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_predicate(self):
        self.assertEqual(self._ps("Test-Togglable @{type='streamable_http';uri='http://127.0.0.1:8777/mcp'}"), "True")
        self.assertEqual(self._ps("Test-Togglable @{type='streamable_http';uri='http://192.168.86.44:8765/mcp'}"), "False")
        self.assertEqual(self._ps("Test-Togglable @{type='builtin'}"), "False")

    def test_flip_only_target_and_idempotent(self):
        d = tempfile.mkdtemp(prefix="gw_ps_")
        cfg = Path(d) / "config.yaml"
        cfg.write_text(_FIX, encoding="utf-8", newline="")
        out = self._ps("Set-ExtensionEnabled '%s' 'srum' $false" % cfg)
        self.assertEqual(out, "True")
        txt = cfg.read_text(encoding="utf-8")
        self.assertIn("enabled: false", txt)
        self.assertEqual(txt.count("enabled: true"), 2)  # developer + eventlog untouched
        # idempotent
        self.assertEqual(self._ps("Set-ExtensionEnabled '%s' 'srum' $false" % cfg), "False")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd goose_web && python -m pytest tests/test_toggle_ps.py -q`
Expected: FAIL/skip — module missing (or skipped if no powershell; on this box powershell exists so it FAILS)

- [ ] **Step 3: Create `goose_web/mcp_toggle.ps1`**

```powershell
# mcp_toggle.ps1 -- per-MCP enable/disable helpers shared by server.ps1 and its
# worker/discoverer runspaces. Pure text edit of goose's config.yaml; no goose
# restart needed (each `goose run` re-reads config). Parity twin of the Python
# _is_togglable / _set_extension_enabled in server.py. Windows PowerShell 5.1 safe.

function Test-Togglable($e) {
    # True iff a loopback streamable_http MCP (the windows_* diagnostic suite).
    if ($e.type -ne 'streamable_http') { return $false }
    if (-not $e.uri) { return $false }
    try { $h = ([System.Uri]$e.uri).Host.ToLower() } catch { return $false }
    return ($h -eq '127.0.0.1' -or $h -eq 'localhost' -or $h -eq '::1')
}

function Set-ExtensionEnabled($configPath, $extId, [bool]$enabled) {
    # Flip `enabled:` for one extension. Returns $true if changed, $false on no-op.
    $want = if ($enabled) { 'true' } else { 'false' }
    $raw  = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
    $nl   = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($l in ($raw -split "`n")) { [void]$lines.Add(($l -replace "`r$", '')) }

    # 1) find `  <extId>:` key line (indent 2) inside the extensions: block
    $keyIdx = -1; $inExt = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $s = $lines[$i].Trim()
        if ($s -eq '' -or $s.StartsWith('#')) { continue }
        $indent = $lines[$i].Length - $lines[$i].TrimStart(' ').Length
        if ($indent -eq 0) { $inExt = ($s -eq 'extensions:'); continue }
        if ($inExt -and $indent -eq 2 -and $s -eq "${extId}:") { $keyIdx = $i; break }
    }
    if ($keyIdx -lt 0) { throw "extension '$extId' not found in $configPath" }

    # 2) block body = keyIdx+1 .. first later non-blank/comment line with indent <= 2
    $blockEnd = $lines.Count
    for ($j = $keyIdx + 1; $j -lt $lines.Count; $j++) {
        $s = $lines[$j].Trim()
        if ($s -eq '' -or $s.StartsWith('#')) { continue }
        $ind = $lines[$j].Length - $lines[$j].TrimStart(' ').Length
        if ($ind -le 2) { $blockEnd = $j; break }
    }

    # 3) find an existing enabled: line inside the block
    $enIdx = -1
    for ($j = $keyIdx + 1; $j -lt $blockEnd; $j++) {
        if ($lines[$j].Trim().StartsWith('enabled:')) { $enIdx = $j; break }
    }

    if ($enIdx -ge 0) {
        $cur = ($lines[$enIdx].Split(':', 2)[1]).Trim().ToLower()
        if ($cur -eq $want) { return $false }
        $ind = $lines[$enIdx].Length - $lines[$enIdx].TrimStart(' ').Length
        $lines[$enIdx] = (' ' * $ind) + "enabled: $want"
    } else {
        $kind = $lines[$keyIdx].Length - $lines[$keyIdx].TrimStart(' ').Length
        $lines.Insert($keyIdx + 1, (' ' * ($kind + 2)) + "enabled: $want")
    }

    $out = ($lines -join $nl)

    # one-time backup
    $bak = "$configPath.bak-webtoggle"
    if (-not (Test-Path -LiteralPath $bak)) { try { Copy-Item -LiteralPath $configPath -Destination $bak -Force } catch {} }
    # honor a read-only durability guard: clear, write, restore
    $item = Get-Item -LiteralPath $configPath
    $wasRo = $item.IsReadOnly
    if ($wasRo) { $item.IsReadOnly = $false }
    $tmp  = "$configPath.tmp"
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmp, $out, $utf8)
    Move-Item -LiteralPath $tmp -Destination $configPath -Force   # atomic rename on NTFS
    if ($wasRo) { (Get-Item -LiteralPath $configPath).IsReadOnly = $true }
    return $true
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd goose_web && python -m pytest tests/test_toggle_ps.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add goose_web/mcp_toggle.ps1 goose_web/tests/test_toggle_ps.py
git commit -m "feat(goose_web): PowerShell config writer + predicate (mcp_toggle.ps1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XPrJ2LtFTYawxzecfDxaqj"
```

---

### Task 6: Wire the toggle into server.ps1

**Files:**
- Modify: `goose_web/server.ps1`

**Interfaces:**
- Consumes: `mcp_toggle.ps1` (`Test-Togglable`, `Set-ExtensionEnabled`), `Parse-GooseExtensions`, `Discover-Extension` (existing, in `$DiscoveryFns`).
- Produces: `POST /api/extensions/toggle` on the PowerShell server; ext snapshot entries carry `enabled`/`togglable`; disabled togglable entries surfaced with `status='disabled'`.

- [ ] **Step 1: Load mcp_toggle.ps1 into the shared fns text**

In `server.ps1`, immediately after the `$DiscoveryFns = @'` … `'@` here-string closes (~line 304) and **before** `. ([scriptblock]::Create($DiscoveryFns))` (~line 308), insert:

```powershell
# Fold in the per-MCP toggle helpers so both the main scope (seed) and the
# worker/discoverer runspaces (which Invoke-Expression this text) get them.
$ToggleFns = ''
try { $ToggleFns = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $Here 'mcp_toggle.ps1') } catch { Write-Warning "[goose_web] could not load mcp_toggle.ps1: $_" }
$DiscoveryFns = $DiscoveryFns + "`n" + $ToggleFns
```

- [ ] **Step 2: Add enabled/togglable + a disabled short-circuit in `Discover-Extension`**

Inside the `$DiscoveryFns` here-string, in `Discover-Extension($e, $gooseBin)`, replace the first line `$detail = ''; $status = 'offline'; $etools = @()` with:

```powershell
    $detail = ''; $status = 'offline'; $etools = @()
    $togglable = Test-Togglable $e
    if (-not $e.enabled) {
        if ($e.uri) { try { $u = [System.Uri]$e.uri; $detail = "$($u.Host):$($u.Port)" } catch {} }
        return @{ ext = @{ id = $e.id; name = $e.name; transport = $e.type; status = 'disabled'; count = 0; detail = $detail; enabled = $false; togglable = $togglable }; tools = @() }
    }
```

And in the same function's final `return`, change the `ext` hashtable to include the two flags:

```powershell
    return @{ ext = @{ id = $e.id; name = $e.name; transport = $e.type; status = $status; count = @($etools).Count; detail = $detail; enabled = $true; togglable = $togglable }; tools = $rows }
```

- [ ] **Step 3: Stop filtering disabled-togglable in the seed pass and discoverer loop**

In the **seed** loop (~lines 310-318), replace:
```powershell
    foreach ($e in (Parse-GooseExtensions $GooseConfigPath)) {
        if (-not $e.enabled) { continue }
        $detail = ''
        if ($e.uri) { try { $u = [System.Uri]$e.uri; $detail = "$($u.Host):$($u.Port)" } catch {} }
        $status = if ($e.type -eq 'builtin' -and $e.id -eq 'developer') { 'builtin' } else { 'checking' }
        $seedExts += @{ id = $e.id; name = $e.name; transport = $e.type; status = $status; count = 0; detail = $detail }
    }
```
with:
```powershell
    foreach ($e in (Parse-GooseExtensions $GooseConfigPath)) {
        $togglable = Test-Togglable $e
        if (-not $e.enabled -and -not $togglable) { continue }
        $detail = ''
        if ($e.uri) { try { $u = [System.Uri]$e.uri; $detail = "$($u.Host):$($u.Port)" } catch {} }
        $status = if (-not $e.enabled) { 'disabled' } elseif ($e.type -eq 'builtin' -and $e.id -eq 'developer') { 'builtin' } else { 'checking' }
        $seedExts += @{ id = $e.id; name = $e.name; transport = $e.type; status = $status; count = 0; detail = $detail; enabled = [bool]$e.enabled; togglable = $togglable }
    }
```

In the **discoverer** runspace loop (~lines 330-335), replace `if (-not $e.enabled) { continue }` with:
```powershell
                if (-not $e.enabled -and -not (Test-Togglable $e)) { continue }
```

- [ ] **Step 4: Add a config-write lock + pass the config path + fns text to the workers**

First, add a shared lock object to the synchronized state so config writes can be serialized across the worker runspaces (parity with the Python `_config_write_lock`). Change the `$Shared` initialization (~line 121):
```powershell
$Shared = [hashtable]::Synchronized(@{ seen = @{}; locks = @{} })
```
to:
```powershell
$Shared = [hashtable]::Synchronized(@{ seen = @{}; locks = @{}; cfgWriteLock = (New-Object object) })
```

Then, in the `$S = @{ … }` bundle (~lines 395-400), add two entries:
```powershell
    gooseConfig = $GooseConfigPath; discoveryFns = $DiscoveryFns
```

- [ ] **Step 5: Give the worker the helper functions + a Handle-Toggle**

At the top of the `$worker = {` scriptblock, just after `$UTF8 = New-Object System.Text.UTF8Encoding($false)`, add:
```powershell
    Invoke-Expression $S.discoveryFns   # Parse-GooseExtensions / Discover-Extension / Test-Togglable / Set-ExtensionEnabled
```

Add this function next to `Handle-Upload` inside the worker:
```powershell
    function Handle-Toggle($ctx, $S) {
        if ($S.token) {
            $sup = $ctx.Request.Headers['X-Goose-Token']; if (-not $sup) { $sup = $ctx.Request.QueryString['token'] }
            if ($sup -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
        }
        $reader = New-Object System.IO.StreamReader($ctx.Request.InputStream, [System.Text.Encoding]::UTF8)
        $bodyText = $reader.ReadToEnd(); $reader.Close()
        $req = $null; try { if ($bodyText.Trim()) { $req = $bodyText | ConvertFrom-Json } } catch {}
        if ($null -eq $req) { Send-Json $ctx @{ error = 'bad json' } 400; return }
        $extId = if ($req.id) { [string]$req.id } else { '' }
        if (-not $extId -or ($req.enabled -isnot [bool])) { Send-Json $ctx @{ error = 'id (str) and enabled (bool) required' } 400; return }
        $enabled = [bool]$req.enabled
        $match = $null
        foreach ($e in (Parse-GooseExtensions $S.gooseConfig)) { if ($e.id -eq $extId) { $match = $e; break } }
        if ($null -eq $match) { Send-Json $ctx @{ error = 'unknown extension' } 404; return }
        if (-not (Test-Togglable $match)) { Send-Json $ctx @{ error = 'extension not togglable' } 403; return }
        # serialize config writes across worker runspaces (parity with Python _config_write_lock)
        $werr = $null
        [System.Threading.Monitor]::Enter($S.shared.cfgWriteLock)
        try { [void](Set-ExtensionEnabled $S.gooseConfig $extId $enabled) }
        catch { $werr = [string]$_ }
        finally { [System.Threading.Monitor]::Exit($S.shared.cfgWriteLock) }
        if ($werr) { Send-Json $ctx @{ error = $werr } 500; return }
        # immediate snapshot update so the next /api/health reflects it.
        # Run the MCP handshake OUTSIDE the lock so a slow/hung server can't
        # stall every /api/health read while we hold SyncRoot.
        try {
            $match.enabled = $enabled
            $d = Discover-Extension $match $S.gooseBin
            $shared = $S.shared
            [System.Threading.Monitor]::Enter($shared.SyncRoot)
            try {
                $exts  = @($shared.exts  | Where-Object { $_.id -ne $extId })
                $tools = @($shared.tools | Where-Object { $_.group -ne $extId })
                $exts += $d.ext
                foreach ($r in $d.tools) { $tools += $r }
                $shared.exts = $exts; $shared.tools = $tools
            } finally { [System.Threading.Monitor]::Exit($shared.SyncRoot) }
        } catch {}
        Send-Json $ctx @{ ok = $true; id = $extId; enabled = $enabled }
    }
```

- [ ] **Step 6: Route the endpoint**

In `Handle-Request`, in the POST branch chain (~lines 717-720), add before the final `else`:
```powershell
            } elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/extensions/toggle') {
                Handle-Toggle $ctx $S
```

- [ ] **Step 7: Parse-check both scripts**

Run:
```bash
powershell -NoProfile -Command "[void][scriptblock]::Create((Get-Content -Raw ./goose_web/mcp_toggle.ps1)); [void][scriptblock]::Create((Get-Content -Raw ./goose_web/server.ps1)); 'parse-ok'"
```
Expected: `parse-ok` (no parser exceptions)

- [ ] **Step 8: Commit**

```bash
git add goose_web/server.ps1
git commit -m "feat(goose_web): POST /api/extensions/toggle (server.ps1 parity)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XPrJ2LtFTYawxzecfDxaqj"
```

---

### Task 7: Frontend toggle switch

**Files:**
- Modify: `goose_web/index.html`

**Interfaces:**
- Consumes: `/api/health` extension objects now carry `enabled`/`togglable`; `POST /api/extensions/toggle` `{id, enabled}`.
- Produces: a switch per togglable card; `postToggle(id, enabled, card, cb)`.

- [ ] **Step 1: Add CSS**

In the `<style>` block, after the `.extn-empty` rule (~line 99), add:

```css
  .extn.ext-off{opacity:.5}
  .dot.off{background:#9993}
  .extn-sw{position:relative;width:34px;height:18px;flex:0 0 auto;margin-left:8px}
  .extn-sw input{position:absolute;inset:0;opacity:0;margin:0;cursor:pointer;z-index:1}
  .extn-sw .sl{position:absolute;inset:0;border-radius:999px;background:#8884;transition:.15s}
  .extn-sw .sl::before{content:"";position:absolute;left:2px;top:2px;width:14px;height:14px;border-radius:50%;background:#fff;transition:.15s}
  .extn-sw input:checked+.sl{background:var(--accent)}
  .extn-sw input:checked+.sl::before{transform:translateX(16px)}
```

- [ ] **Step 2: Update `renderExt` — signature, disabled styling, switch**

Change the `renderExt` signature (~line 524):
```javascript
  function renderExt(id,name,transport,status,detail,myTools){
```
to:
```javascript
  function renderExt(id,name,transport,status,detail,myTools,enabled,togglable){
```

Just after `const card=el("div","extn");` add:
```javascript
    if(togglable && enabled===false)card.classList.add("ext-off");
```

Change the status→dot mapping (~line 527) from:
```javascript
    const st=status==="ok"?"up":(status==="error"||status==="down"?"down":"checking");
```
to:
```javascript
    const st=status==="ok"?"up":(status==="disabled"?"off":(status==="error"||status==="down"?"down":"checking"));
```

Change the empty-tools text (~line 547) from:
```javascript
      const e=el("div","extn-empty");e.textContent=status==="checking"?"discovering tools…":"no tools exposed";body.appendChild(e);
```
to:
```javascript
      const e=el("div","extn-empty");e.textContent=status==="disabled"?"disabled":(status==="checking"?"discovering tools…":"no tools exposed");body.appendChild(e);
```

Just before the final `card.appendChild(body);tw.appendChild(card);` line (~549), add the switch:
```javascript
    if(togglable){
      const top=head.querySelector(".extn-top");
      const sw=el("label","extn-sw");
      sw.title="enable / disable this MCP for the agent (applies to your next message)";
      sw.innerHTML='<input type="checkbox"'+(enabled?" checked":"")+'><span class="sl"></span>';
      const cb=sw.querySelector("input");
      sw.onclick=e=>e.stopPropagation();          // clicking the switch must not collapse the card
      cb.onchange=()=>postToggle(id,cb.checked,card,cb);
      top.appendChild(sw);
    }
```

- [ ] **Step 3: Render disabled-togglable cards + pass the flags**

Replace the `exts.forEach(...)` block (~lines 551-557) with:
```javascript
  exts.forEach(x=>{
    if(x.transport==="platform")return;               // goose internal platform extensions — not user tools
    const myTools=tools.filter(t=>t.group===x.id);
    if(myTools.length===0 && x.status!=="checking" && !x.togglable)return;
    shown.add(x.id);
    renderExt(x.id,x.name,x.transport,x.status,x.detail,myTools,x.enabled,x.togglable);
  });
```

Update the fallback-group call (~line 560) to pass the two extra args:
```javascript
    renderExt(g,g,"",null,null,tools.filter(t=>t.group===g),true,false);
```

- [ ] **Step 4: Add `postToggle`**

After the `promptToken` function (~line 568), add:
```javascript
async function postToggle(id,enabled,card,cb){
  if(card)card.classList.toggle("ext-off",!enabled);   // optimistic
  const headers={"Content-Type":"application/json"};if(TOKEN)headers["X-Goose-Token"]=TOKEN;
  try{
    const r=await fetch("/api/extensions/toggle",{method:"POST",headers,body:JSON.stringify({id,enabled})});
    if(r.status===401){TOKEN="";localStorage.removeItem("haToken");promptToken();throw new Error("token required");}
    if(!r.ok){let j={};try{j=await r.json();}catch(_){}throw new Error(j.error||("error "+r.status));}
    setTimeout(loadHealth,1200);                        // reconcile with the refreshed snapshot
  }catch(err){
    if(cb)cb.checked=!enabled;                          // revert switch
    if(card)card.classList.toggle("ext-off",enabled);   // revert style
    alert("toggle failed: "+(err.message||err));
  }
}
```

- [ ] **Step 5: Manual UI verification**

There is no automated DOM test harness in this repo. Verify manually after Task 8's server restart (do it there). For now, sanity-check the HTML parses and the JS has no syntax error:

Run: `python -c "import pathlib,html.parser; html.parser.HTMLParser().feed(pathlib.Path('goose_web/index.html').read_text(encoding='utf-8')); print('html-ok')"`
Expected: `html-ok`

- [ ] **Step 6: Commit**

```bash
git add goose_web/index.html
git commit -m "feat(goose_web): per-MCP enable/disable switch in the UI

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XPrJ2LtFTYawxzecfDxaqj"
```

---

### Task 8: Docs + live acceptance

**Files:**
- Modify: `goose_web/README.md`

- [ ] **Step 1: Document the feature**

Append to `goose_web/README.md` a new section (before the `## Files` table if present, else at end):

```markdown
## Enable/disable MCPs from the UI

Each **Windows diagnostic MCP** card (the loopback `streamable_http` servers, ports
8777–8788) has an on/off switch. Flipping it sets that extension's `enabled:` flag in
goose's live `config.yaml` and takes effect on your **next message** — no restart. It is
a config-level switch (whether goose loads the extension); it does **not** start or stop
the backend MCP server process.

- Only loopback `streamable_http` MCPs are togglable. `developer`/`memory`/
  `computercontroller` (builtin) and `dtm` (remote) have no switch and are refused
  server-side.
- `POST /api/extensions/toggle` `{ "id": "<ext>", "enabled": <bool> }`, token-gated like
  `/api/chat`.
- The first edit backs up `config.yaml` to `config.yaml.bak-webtoggle` (once). Writes are
  atomic and honor a read-only durability guard on the config file.
```

- [ ] **Step 2: Commit docs**

```bash
git add goose_web/README.md
git commit -m "docs(goose_web): document per-MCP enable/disable toggle

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01XPrJ2LtFTYawxzecfDxaqj"
```

- [ ] **Step 3: Live acceptance test (requires restarting goose_web)**

The live server on :8799 is the OLD `server.ps1`; it must be restarted to load the new code. **The user restarts it** (kernel/service ownership) — ask them to stop the running server and run `goose_web\serve_web.ps1` again (or restart the scheduled task if one runs it). Then verify:

1. Open `http://127.0.0.1:8799/` — each windows_* MCP card shows a switch; `developer`/`memory`/`dtm` show none.
2. Toggle `filterstack` **off**. Confirm the card greys and `GET /api/health` shows `filterstack` with `enabled:false`, `status:"disabled"`.
3. Send a chat message like "list your filterstack tools" — confirm the agent no longer has them (the spec's open assumption: `goose run` re-read `config.yaml`).
4. Toggle `filterstack` **on**; confirm its tools return on the next message.
5. Confirm `config.yaml.bak-webtoggle` was created and the live `config.yaml` is otherwise byte-identical to the backup except `filterstack`'s `enabled:` (which should be back to `true`).

Command to check the config diff (PowerShell):
```powershell
Compare-Object (Get-Content "$env:APPDATA\Block\goose\config\config.yaml") (Get-Content "$env:APPDATA\Block\goose\config\config.yaml.bak-webtoggle")
```
Expected: no differences after toggling `filterstack` off then back on.

- [ ] **Step 4: Full test sweep**

Run: `cd goose_web && python -m pytest tests/ -q`
Expected: PASS (test_uploads + test_toggle + test_toggle_ps)

---

## Notes for the implementer

- **Read before you edit.** Line numbers here are from the current files and will drift as you add code; anchor on the quoted surrounding text, not the numbers.
- **Parity is the point.** Tasks 1-4 (Python) and 5-6 (PowerShell) implement the same contract. If you change a field name or status string in one, change it in the other and in `index.html`.
- **The one status you're adding is `"disabled"`.** It joins `ok`/`offline`/`checking` and maps to the greyed `off` dot in the UI.
- **Do not restart the live server yourself** — Task 8 hands that to the user.
