# Goose Web File Attach — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the goose_web chat UI attach any file and have the goose agent read it.

**Architecture:** A new `POST /api/upload` saves raw request-body bytes to
`workspace/uploads/<session>/<file>`; `POST /api/chat` gains an `attachments[]` field and
appends the files' workspace-relative paths to the goose prompt so goose reads them with
its own tools (verified end-to-end 2026-06-30). Implemented identically in `server.py`
and `server.ps1`; the browser composer uploads files then sends the chat turn.

**Tech Stack:** Python 3 stdlib (`http.server`, `urllib`, `pathlib`, `unittest`); Windows
PowerShell 5.1 (.NET `HttpListener`); vanilla JS/HTML.

## Global Constraints

- `server.py`: Python 3 **stdlib only** (no pip; tests use stdlib `unittest`).
- `server.ps1`: Windows PowerShell 5.1 compatible, .NET only; no new modules.
- `server.py` and `server.ps1` MUST keep an **identical** `/api/upload` + `/api/chat` contract.
- Uploads confined to `WORKSPACE/uploads/`; filenames sanitized (no `..`, no absolute/drive paths).
- Per-file cap `max_upload_mb` default **25**; env `GOOSE_WEB_MAX_UPLOAD_MB` overrides; `uploads_subdir` default `uploads`.
- Token gate (`X-Goose-Token` / `?token=`) applies to `/api/upload` exactly as to `/api/chat`.
- Injected block format, appended to the user message (default body `請查看我附加的檔案。` when text empty):
  ```
  [附加檔案 (相對於工作目錄):]
  - uploads/<session>/<name> (<size>)
  ```
- Don't break existing streaming chat, health, discovery, or the token gate.

---

### Task 1: Python sanitizers + message composer (pure functions, unit-tested)

**Files:**
- Modify: `goose_web/server.py` (add helpers near the other module helpers, after `GOOSE_VERSION`/config area; add `UPLOADS_SUBDIR`, `MAX_UPLOAD_MB`, `MAX_UPLOAD_BYTES` near other config constants ~`server.py:104-110`)
- Test: `goose_web/tests/test_uploads.py` (create)

**Interfaces:**
- Produces:
  - `_safe_session(s: str) -> str` — `re.sub(r"[^A-Za-z0-9_.-]", "_", s.strip())[:80] or "web"`
  - `_safe_name(name: str) -> str` — basename only; allow `[A-Za-z0-9._ -]`, others→`_`; strip leading dots; ≤150; `"file"` fallback
  - `_human_size(n: int) -> str`
  - `_session_upload_dir(session: str) -> Path` — `WORKSPACE/UPLOADS_SUBDIR/<safeSession>`, resolved + contained under `WORKSPACE/UPLOADS_SUBDIR`
  - `_unique_path(d: Path, name: str) -> Path` — collision → ` (n)` before extension
  - `_compose_message(message: str, session: str, attachments: list) -> str`

- [ ] **Step 1: Write the failing tests**

Create `goose_web/tests/test_uploads.py`:
```python
import os, sys, tempfile, unittest
from pathlib import Path

# Point WORKSPACE at a temp dir BEFORE importing server (module resolves it at import).
_TMP = tempfile.mkdtemp(prefix="gw_test_")
os.environ["GOOSE_WEB_WORKSPACE"] = _TMP
os.environ["GOOSE_WEB_HOST"] = "127.0.0.1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402


class SafeName(unittest.TestCase):
    def test_strips_directories_and_traversal(self):
        self.assertEqual(server._safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(server._safe_name(r"C:\Windows\system32\cmd.exe"), "cmd.exe")
        self.assertEqual(server._safe_name("a/b/c/report.pdf"), "report.pdf")

    def test_allows_safe_chars_and_replaces_others(self):
        self.assertEqual(server._safe_name("my report (1).pdf"), "my report (1).pdf")
        self.assertEqual(server._safe_name("wei?rd*na:me.txt"), "wei_rd_na_me.txt")

    def test_strips_leading_dots_and_empty_fallback(self):
        self.assertEqual(server._safe_name("...hidden"), "hidden")
        self.assertEqual(server._safe_name(""), "file")
        self.assertEqual(server._safe_name("/"), "file")


class UploadDir(unittest.TestCase):
    def test_contained_under_workspace_uploads(self):
        d = server._session_upload_dir("web-1")
        root = (server.WORKSPACE / server.UPLOADS_SUBDIR).resolve()
        self.assertTrue(str(d).startswith(str(root)))

    def test_session_is_sanitized(self):
        d = server._session_upload_dir("../evil")
        root = (server.WORKSPACE / server.UPLOADS_SUBDIR).resolve()
        self.assertTrue(str(d).startswith(str(root)))


class Compose(unittest.TestCase):
    def _mk(self, session, name, content=b"hi"):
        d = server._session_upload_dir(session); d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(content)

    def test_no_attachments_returns_message_unchanged(self):
        self.assertEqual(server._compose_message("hello", "s1", []), "hello")
        self.assertEqual(server._compose_message("hello", "s1", None), "hello")

    def test_injects_existing_files_only(self):
        self._mk("s2", "a.txt"); 
        out = server._compose_message("read it", "s2", ["a.txt", "missing.txt"])
        self.assertIn("[附加檔案 (相對於工作目錄):]", out)
        self.assertIn("uploads/s2/a.txt", out)
        self.assertNotIn("missing.txt", out)
        self.assertTrue(out.startswith("read it"))

    def test_empty_message_uses_default_prompt(self):
        self._mk("s3", "b.txt")
        out = server._compose_message("", "s3", ["b.txt"])
        self.assertTrue(out.startswith("請查看我附加的檔案。"))

    def test_attachment_name_is_sanitized_on_lookup(self):
        self._mk("s4", "c.txt")
        out = server._compose_message("x", "s4", ["../c.txt"])  # resolves to c.txt in dir
        self.assertIn("uploads/s4/c.txt", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd goose_web && python -m unittest tests.test_uploads -v`
Expected: FAIL/ERROR — `AttributeError: module 'server' has no attribute '_safe_name'`.

- [ ] **Step 3: Add config constants + helpers to `server.py`**

After the workspace/config constants (near `MAX_WALL_SECONDS = int(...)`, ~`server.py:110`) add:
```python
UPLOADS_SUBDIR = str(WEBCFG.get("uploads_subdir", "uploads")).strip("/\\") or "uploads"
MAX_UPLOAD_MB = int(os.environ.get("GOOSE_WEB_MAX_UPLOAD_MB", WEBCFG.get("max_upload_mb", 25)))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
```
Add to `_DEFAULTS` (so config.json keys are recognized): `"uploads_subdir": "uploads", "max_upload_mb": 25,`.

Add helpers (place them after `_clean`/util helpers, before the `Handler` class):
```python
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]")


def _safe_session(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]", "_", str(s or "").strip())[:80]
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd goose_web && python -m unittest tests.test_uploads -v`
Expected: PASS (all tests OK).

- [ ] **Step 5: Commit**

```bash
git add goose_web/server.py goose_web/tests/test_uploads.py
git commit -m "feat(goose_web): upload sanitizers + attachment message composer (py)"
```

---

### Task 2: Python `/api/upload` endpoint + `/api/chat` attachments

**Files:**
- Modify: `goose_web/server.py` — extract `_auth_ok()`, route `/api/upload` in `do_POST`, add `_handle_upload`, thread `attachments` through `do_POST`→`_stream_chat`.

**Interfaces:**
- Consumes: Task 1 helpers (`_safe_name`, `_session_upload_dir`, `_unique_path`, `_compose_message`, `MAX_UPLOAD_BYTES`).
- Produces: `POST /api/upload` → `{ok,name,size}`; `_stream_chat(session, message, mode, attachments)`.

- [ ] **Step 1: Extract the auth check** (refactor the inline token gate in `do_POST`)

Add method to `Handler`:
```python
    def _auth_ok(self) -> bool:
        if not TOKEN:
            return True
        from urllib.parse import urlparse, parse_qs
        supplied = self.headers.get("X-Goose-Token", "") or \
            parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return supplied == TOKEN
```
Replace the existing inline token block in `do_POST` (the `if TOKEN: ...` section) with:
```python
        if not self._auth_ok():
            self._send_json({"error": "unauthorized"}, 401)
            return
```

- [ ] **Step 2: Route `/api/upload` and pass attachments in `do_POST`**

Change the top of `do_POST` routing from the single `/api/chat` check to:
```python
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
        attachments = req.get("attachments") or []
        message = _compose_message(message, session, attachments)
        if not message:
            self._send_json({"error": "empty message"}, 400)
            return
        self._stream_chat(session, message, mode)
```
(Remove the now-duplicated session/message/mode parsing and the old inline token gate below.)

- [ ] **Step 3: Add `_handle_upload`**

```python
    def _handle_upload(self):
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        session = qs.get("session", ["web"])[0]
        name = _safe_name(qs.get("name", [""])[0])
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            self._send_json({"error": "empty body"}, 400); return
        if length > MAX_UPLOAD_BYTES:
            self._send_json({"error": f"file too large (> {MAX_UPLOAD_MB} MB)"}, 413); return
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
                    f.write(chunk); written += len(chunk); remaining -= len(chunk)
        except Exception as e:  # noqa: BLE001
            try: dest.unlink()
            except Exception: pass
            self._send_json({"error": str(e)}, 400); return
        self._send_json({"ok": True, "name": dest.name, "size": written})
```

- [ ] **Step 4: Update `_stream_chat` signature note** — it already takes `(session, message, mode)`; no change needed (composed message is passed in). Confirm no other caller exists.

- [ ] **Step 5: Live verification**

Run (background): `cd goose_web && GOOSE_WEB_HOST=127.0.0.1 GOOSE_WEB_PORT=8801 python server.py`
Then:
```bash
printf 'SECRET_TOKEN_9Z said hi' > /tmp/u.txt
curl -s "http://127.0.0.1:8801/api/upload?session=web-x&name=u.txt" --data-binary @/tmp/u.txt
# expect: {"ok": true, "name": "u.txt", "size": 23}
ls "../workspace/uploads/web-x/"      # expect u.txt
# oversize -> 413
head -c 30000000 /dev/zero > /tmp/big.bin
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8801/api/upload?session=web-x&name=big.bin" --data-binary @/tmp/big.bin
# expect: 413
# traversal name stays contained
curl -s "http://127.0.0.1:8801/api/upload?session=web-x&name=../../evil.txt" --data-binary @/tmp/u.txt
ls "../workspace/uploads/web-x/"      # expect evil.txt here, NOT outside
```
Kill the server. Expected: first upload `{ok:true,...}`, oversize `413`, traversal contained.

- [ ] **Step 6: Commit**

```bash
git add goose_web/server.py
git commit -m "feat(goose_web): POST /api/upload + chat attachments injection (py)"
```

---

### Task 3: PowerShell `/api/upload` + attachments (server.ps1)

**Files:**
- Modify: `goose_web/server.ps1` — add `$UploadsSubdir`/`$MaxUploadBytes` config; sanitizer + compose helpers (in the worker `$worker` scriptblock so workers can call them); route `/api/upload` in `Handle-Request`; inject attachments in `Handle-Chat`.

**Interfaces:**
- Consumes: existing `$S` bundle (add `workspace`, `maxUploadBytes`, `uploadsSubdir`, `token`).
- Produces: identical `/api/upload` + `/api/chat` contract as `server.py`.

- [ ] **Step 1: Config constants** (near other config, after `$TimeoutSec`):
```powershell
$UploadsSubdir  = if ($CFG.uploads_subdir) { ([string]$CFG.uploads_subdir).Trim('/\') } else { 'uploads' }
$MaxUploadMb    = if ($env:GOOSE_WEB_MAX_UPLOAD_MB) { [int]$env:GOOSE_WEB_MAX_UPLOAD_MB } elseif ($CFG.max_upload_mb) { [int]$CFG.max_upload_mb } else { 25 }
$MaxUploadBytes = $MaxUploadMb * 1024 * 1024
```
Add to `$S` bundle: `uploadsSubdir = $UploadsSubdir; maxUploadBytes = $MaxUploadBytes`.

- [ ] **Step 2: Helpers inside `$worker`** (add near `Get-ChatUrl`):
```powershell
    function Safe-Session($s) {
        $s = [regex]::Replace([string]$s, '[^A-Za-z0-9_.-]', '_'); if ($s.Length -gt 80) { $s = $s.Substring(0,80) }
        if (-not $s) { 'web' } else { $s }
    }
    function Safe-Name($name) {
        $n = ([string]$name) -replace '\\','/'; $n = ($n -split '/')[-1]
        $n = ($n -replace '[^A-Za-z0-9._ -]','_').TrimStart('.').Trim()
        if ($n.Length -gt 150) { $n = $n.Substring(0,150) }
        if (-not $n) { 'file' } else { $n }
    }
    function Human-Size($n) {
        $f = [double]$n
        foreach ($u in 'B','KB','MB','GB') { if ($f -lt 1024 -or $u -eq 'GB') { if ($u -eq 'B') { return "$([int]$f) B" } else { return ("{0:N1} {1}" -f $f,$u) } }; $f /= 1024 }
    }
    function Session-UploadDir($S, $session) {
        $root = [System.IO.Path]::GetFullPath((Join-Path $S.workspace $S.uploadsSubdir))
        $d = [System.IO.Path]::GetFullPath((Join-Path $root (Safe-Session $session)))
        if ($d -ne $root -and -not $d.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar)) { throw 'escapes workspace' }
        return $d
    }
    function Unique-Path($dir, $name) {
        $p = Join-Path $dir $name; if (-not (Test-Path -LiteralPath $p)) { return $p }
        $base = [System.IO.Path]::GetFileNameWithoutExtension($name); $ext = [System.IO.Path]::GetExtension($name)
        $i = 1; while (Test-Path -LiteralPath (Join-Path $dir ("$base ($i)$ext"))) { $i++ }
        return (Join-Path $dir ("$base ($i)$ext"))
    }
    function Compose-Message($S, $message, $session, $attachments) {
        if (-not $attachments) { return $message }
        $dir = Session-UploadDir $S $session
        $sub = "$($S.uploadsSubdir)/$(Safe-Session $session)"
        $lines = @()
        foreach ($a in $attachments) {
            if ($a -isnot [string]) { continue }
            $sn = Safe-Name $a; $p = Join-Path $dir $sn
            if (Test-Path -LiteralPath $p -PathType Leaf) { $lines += "- $sub/$sn ($(Human-Size (Get-Item -LiteralPath $p).Length))" }
        }
        if ($lines.Count -eq 0) { return $message }
        $body = if ($message) { $message } else { '請查看我附加的檔案。' }
        return ($body + "`n`n[附加檔案 (相對於工作目錄):]`n" + ($lines -join "`n"))
    }
```

- [ ] **Step 3: Route `/api/upload` in `Handle-Request`** (add a branch alongside `/api/chat`):
```powershell
            } elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/upload') {
                Handle-Upload $ctx $S
```
And add `Handle-Upload`:
```powershell
    function Handle-Upload($ctx, $S) {
        if ($S.token) {
            $sup = $ctx.Request.Headers['X-Goose-Token']; if (-not $sup) { $sup = $ctx.Request.QueryString['token'] }
            if ($sup -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
        }
        $session = $ctx.Request.QueryString['session']; if (-not $session) { $session = 'web' }
        $name = Safe-Name $ctx.Request.QueryString['name']
        $len = [int64]$ctx.Request.ContentLength64
        if ($len -le 0) { Send-Json $ctx @{ error = 'empty body' } 400; return }
        if ($len -gt $S.maxUploadBytes) { Send-Json $ctx @{ error = 'file too large' } 413; return }
        try {
            $dir = Session-UploadDir $S $session
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
            $dest = Unique-Path $dir $name
            $fs = [System.IO.File]::Create($dest)
            try { $ctx.Request.InputStream.CopyTo($fs) } finally { $fs.Close() }
            $size = (Get-Item -LiteralPath $dest).Length
            Send-Json $ctx @{ ok = $true; name = [System.IO.Path]::GetFileName($dest); size = $size }
        } catch { Send-Json $ctx @{ error = ([string]$_) } 400 }
    }
```

- [ ] **Step 4: Inject attachments in `Handle-Chat`** — after parsing `$message`/`$mode`, before `Invoke-Chat`:
```powershell
        $attachments = $req.attachments
        $message = Compose-Message $S $message $session $attachments
        if (-not $message) { Send-Json $ctx @{ error = 'empty message' } 400; return }
```
(Keep the existing empty-check replaced by the one above.)

- [ ] **Step 5: Live verification**

Run `server.ps1` on `127.0.0.1:8802` (Start-Process, hidden), then:
```powershell
'SECRET_TOKEN_9Z' | Out-File $env:TEMP\u.txt -Encoding ascii -NoNewline
Invoke-RestMethod "http://127.0.0.1:8802/api/upload?session=web-y&name=u.txt" -Method Post -InFile $env:TEMP\u.txt   # {ok,name,size}
Test-Path "..\workspace\uploads\web-y\u.txt"   # True
# traversal contained:
Invoke-RestMethod "http://127.0.0.1:8802/api/upload?session=web-y&name=..\..\evil.txt" -Method Post -InFile $env:TEMP\u.txt
Test-Path "..\workspace\uploads\web-y\evil.txt"  # True (contained), and NOT created outside
```
Expected: JSON `ok`, file present, traversal contained. Stop the server.

- [ ] **Step 6: Commit**

```bash
git add goose_web/server.ps1
git commit -m "feat(goose_web): POST /api/upload + chat attachments injection (ps1)"
```

---

### Task 4: UI — attach button, drag-drop, paste, chips, upload-then-send

**Files:**
- Modify: `goose_web/index.html` — CSS for `.attachbtn`/`.chips`/`.chip`; markup in `.composer` (~L271-277); `pending[]` state + handlers; `send()` (~L434) uploads then posts chat with `attachments`; user bubble shows attached names.

**Interfaces:**
- Consumes: `POST /api/upload?session=&name=` and `attachments[]` in `/api/chat`.

- [ ] **Step 1: CSS** (add near the `/* composer */` block):
```css
  .cbox .attachbtn{background:none;border:0;color:var(--faint);cursor:pointer;font-size:18px;padding:0 6px;align-self:flex-end}
  .cbox .attachbtn:hover{color:var(--accent)}
  .chips{display:flex;flex-wrap:wrap;gap:7px;margin:0 0 8px}
  .chip{display:flex;align-items:center;gap:7px;background:var(--panel2);border:1px solid var(--line2);
    border-radius:9px;padding:4px 8px;font-size:12.5px;color:var(--txt);max-width:240px}
  .chip .nm{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .chip .sz{color:var(--faint);font-size:11px;flex:0 0 auto}
  .chip .x{cursor:pointer;color:var(--faint);flex:0 0 auto}
  .chip .x:hover{color:var(--down)}
  .composer.drag{outline:2px dashed var(--accent);outline-offset:-6px;border-radius:12px}
```

- [ ] **Step 2: Markup** — replace the `.composer` inner with chips row + attach button + hidden input:
```html
    <div class="composer" id="composer">
      <div class="chips" id="chips"></div>
      <div class="cbox">
        <button class="attachbtn" id="btnAttach" title="Attach files">📎</button>
        <textarea id="input" rows="1" placeholder="Send a message…  (Enter to send, Shift+Enter for newline)"></textarea>
        <button class="send" id="btnSend" title="Send">↑</button>
      </div>
      <input type="file" id="fileInput" multiple style="display:none">
      <div class="chint" id="hint">Agent mode runs tools (shell, files, knowledge) · Chat mode is model-only</div>
    </div>
```

- [ ] **Step 3: State + chip rendering + input handlers** (add in the script, near other UI wiring ~L540):
```javascript
let pending=[];   // File objects staged for upload
function fmtSize(n){const u=["B","KB","MB","GB"];let f=n,i=0;while(f>=1024&&i<3){f/=1024;i++;}return (i?f.toFixed(1):f)+" "+u[i];}
function renderChips(){
  const box=$("#chips"); box.innerHTML="";
  pending.forEach((f,idx)=>{
    const c=el("div","chip");
    c.innerHTML='<span class="nm">'+esc(f.name)+'</span><span class="sz">'+fmtSize(f.size)+'</span><span class="x" data-i="'+idx+'">✕</span>';
    c.querySelector(".x").onclick=()=>{pending.splice(idx,1);renderChips();};
    box.appendChild(c);
  });
}
function addFiles(list){ for(const f of list){ if(f) pending.push(f); } renderChips(); }
$("#btnAttach").onclick=()=>$("#fileInput").click();
$("#fileInput").addEventListener("change",e=>{addFiles(e.target.files);e.target.value="";});
const comp=$("#composer");
["dragenter","dragover"].forEach(ev=>comp.addEventListener(ev,e=>{e.preventDefault();comp.classList.add("drag");}));
["dragleave","drop"].forEach(ev=>comp.addEventListener(ev,e=>{e.preventDefault();if(ev!=="dragleave"||e.target===comp)comp.classList.remove("drag");}));
comp.addEventListener("drop",e=>{if(e.dataTransfer&&e.dataTransfer.files)addFiles(e.dataTransfer.files);});
$("#input").addEventListener("paste",e=>{const fs=[...(e.clipboardData?.files||[])];if(fs.length){e.preventDefault();addFiles(fs);}});
```

- [ ] **Step 4: Upload-then-send in `send()`** — modify `send()` (~L434). After computing `text` and BEFORE the `/api/chat` fetch, add upload of `pending`, then include `attachments`. Replace the guard `if(!text) return;` so attachments-only is allowed:
```javascript
async function send(){
  const text=$("#input").value.trim();
  const files=pending.slice();
  if((!text && files.length===0) || sending) return;
  sending=true; $("#btnSend").disabled=true;
  // upload staged files first
  let attachments=[];
  try{
    for(const f of files){
      const headers={}; if(TOKEN) headers["X-Goose-Token"]=TOKEN;
      const r=await fetch("/api/upload?session="+encodeURIComponent(active)+"&name="+encodeURIComponent(f.name),
        {method:"POST",headers,body:f});
      if(!r.ok){throw new Error("upload failed for "+f.name+" ("+r.status+")");}
      const j=await r.json(); attachments.push(j.name);
    }
  }catch(err){ showError(err.message); sending=false; $("#btnSend").disabled=false; return; }
  pending=[]; renderChips();
  // ... existing code: render user bubble (append attachment names), then:
  //     body:JSON.stringify({session:active,message:text,mode:MODE,attachments})
}
```
Wire `attachments` into the existing `/api/chat` `JSON.stringify({...})` body, and show `files.map(f=>f.name)` in the user bubble. Use the file-attach module's `sending` flag (reuse the existing streaming guard variable if present; otherwise add `let sending=false;`). On stream end, reset `sending=false`.

- [ ] **Step 5: Manual verification**

Run `server.py` on `127.0.0.1:8801`, open `http://127.0.0.1:8801`:
- Click 📎, pick a .txt → chip appears with size; ✕ removes it.
- Drag a file onto the composer → chip appears; composer shows dashed outline while dragging.
- Type "讀附件並回報內容", attach the .txt, send → user bubble shows text + filename; assistant reads the file (tool card `shell`/`text_editor`) and reports content.

- [ ] **Step 6: Commit**

```bash
git add goose_web/index.html
git commit -m "feat(goose_web): composer file attach (button/drag/paste) + upload-then-send"
```

---

### Task 5: Config + docs

**Files:**
- Modify: `goose_web/config.json` (add `max_upload_mb`, `uploads_subdir`, note in `_comment`)
- Modify: `goose_web/README.md` (document `/api/upload`, attachments, env vars)

- [ ] **Step 1:** Add to `config.json`: `"max_upload_mb": 25, "uploads_subdir": "uploads",` and extend `_comment` noting uploads land in `workspace/uploads/<session>/` and the agent reads them via its tools.

- [ ] **Step 2:** README: add `/api/upload` to Endpoints, a short "Attaching files" subsection (flow + that goose reads from `workspace/uploads/`), and `GOOSE_WEB_MAX_UPLOAD_MB` to the env table.

- [ ] **Step 3: Commit**

```bash
git add goose_web/config.json goose_web/README.md
git commit -m "docs(goose_web): document file attachments + max_upload_mb"
```

---

### Task 6: End-to-end verification (both servers + goose reads upload)

- [ ] **Step 1:** Run `python -m unittest tests.test_uploads -v` → all PASS.
- [ ] **Step 2:** For each server (server.py @8801, server.ps1 @8802) confirm `/api/health` still returns 7 extensions / 34 tools (no regression).
- [ ] **Step 3:** Upload a .txt with a known phrase via `/api/upload`, then POST `/api/chat` `{session, message:"讀附件回報通關密語", mode:"auto", attachments:["that.txt"]}`; confirm the streamed answer contains the phrase (goose read it). Do this against server.py and server.ps1.
- [ ] **Step 4:** Clean up test files under `workspace/uploads/`. Confirm no orphan goose/server processes.
- [ ] **Step 5: Commit** any fixes found.

---

## Self-Review

**Spec coverage:** upload endpoint (T2/T3), attachments injection (T1/T2/T3), storage+sanitize+containment (T1/T3), size cap/413 (T2/T3), token gate (T2/T3), UI button+drag+paste+chips (T4), both servers (T2/T3), config+README (T5), e2e incl. goose-reads (T6). All spec sections mapped.

**Placeholder scan:** code given for every code step; verification steps give exact commands + expected output. The one prose hand-off (T4 Step 4 "wire into existing JSON body / user bubble") references existing concrete code in `send()`; implementer edits the real lines.

**Type consistency:** `_safe_session`/`_safe_name`/`_session_upload_dir`/`_unique_path`/`_compose_message` names match between Task 1 (def) and Task 2 (use); PowerShell mirrors them as `Safe-Session`/`Safe-Name`/`Session-UploadDir`/`Unique-Path`/`Compose-Message`. Upload response `{ok,name,size}` consistent across py/ps1/JS (`j.name`).
