# DTM Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dtm_download's downloads and dtm_deploy's MSI install/uninstall observable: progress lines to stdout + a per-build `download.log`, an MSI-log tail in every install/uninstall result, and a verified answer on MCP `report_progress` feasibility.

**Architecture:** Additive, best-effort instrumentation. `artifactory.py` gains a `_DlLog` sink and optional `label`/`log` parameters on `download_file`; `download_build` opens `<build>/download.log` and threads the sink through. `msi.py` gains a pure `tail_log()` used by `install_msi`/`uninstall_product`. The `report_progress` experiment is throwaway scratchpad code; only its findings note is committed.

**Tech Stack:** Python 3 stdlib + requests (existing), pytest. No `logging` module (repo convention is plain print/return-dict).

## Global Constraints

- Style: plain `print(..., flush=True)` + return-dict conventions; do NOT introduce the `logging` module.
- Progress emission is best-effort: no progress/file-write failure may fail a download that would otherwise succeed; `tail_log` never raises.
- Existing callers must be unaffected: new parameters are optional with no-op defaults; existing tests keep passing unmodified unless a test explicitly gains assertions.
- msiexec `/l*v` logs are UTF-16LE (usually with BOM); `tail_log` must handle BOM'd UTF-16, BOM-less UTF-16LE, and UTF-8 fallback.
- Branch is `feature/dtm-observability`; commit there; do not push.
- Every commit message body ends with the repo's two trailer lines:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013Wm8BeurMKFZK6TgvFhLjE`. (Omitted from sample commits below — add them.)

---

## File Structure

- Modify `mcp/dtm_download/artifactory.py` — `_PROGRESS_STEP`, `_progress_line()`, `_DlLog`, `download_file(label=, log=)`, `download_build` emission.
- Modify `mcp/dtm_download/tests/test_artifactory.py` — `FakeResponse.headers`, new progress tests.
- Modify `mcp/dtm_download/README.md` — progress lines + `download.log` note.
- Modify `mcp/dtm_deploy/msi.py` — `tail_log()`, `log_tail` in both result dicts.
- Modify `mcp/dtm_deploy/tests/test_msi.py` — `tail_log` tests + `log_tail` presence assertions.
- Modify `mcp/dtm_deploy/README.md` — `log_tail` note.
- Create `docs/superpowers/specs/2026-07-18-report-progress-experiment.md` — experiment findings (Task 3).

---

## Task 1: dtm_download progress lines + download.log

**Files:**
- Modify: `mcp/dtm_download/artifactory.py`
- Test: `mcp/dtm_download/tests/test_artifactory.py`
- Modify: `mcp/dtm_download/README.md`

**Interfaces:**
- Consumes: existing `download_file/discover_zip_files/verify_checksum/extract_zip/resolve_latest_build`.
- Produces:
  - `download_file(base_url, repo_path_file, token, out_file, timeout=600, label="", log=None)` — `log` is a callable `log(msg: str)` or None (None → progress lines still print to stdout).
  - `_DlLog(path)` with `.emit(msg)` (print + append-to-file, one-time warning then disable on write failure) and `.close()`.
  - `_progress_line(label, done_bytes, total_bytes) -> str`.
  - `download_build` behavior: writes `<download_path>/<build_id>/download.log` containing every emitted line.

- [ ] **Step 1: Write the failing tests** (append to `mcp/dtm_download/tests/test_artifactory.py`; also add `headers` to `FakeResponse` and a multi-chunk `iter_content`)

Replace the `FakeResponse` class with:

```python
class FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b"", headers=None, chunks=None):
        self.status_code = status_code
        self._json_body = json_body
        self._content = content
        self._chunks = chunks          # optional list of byte chunks for iter_content
        self.headers = headers or {}

    def json(self):
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body

    def iter_content(self, chunk_size=1):
        if self._chunks is not None:
            for c in self._chunks:
                yield c
        else:
            yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
```

Append the new tests:

```python
def test_download_file_emits_progress_with_percent(monkeypatch, tmp_path, capsys):
    # shrink the step so tiny fake chunks cross it
    monkeypatch.setattr(artifactory, "_PROGRESS_STEP", 4)
    chunks = [b"aaaa", b"bbbb", b"cc"]          # 10 bytes total
    resp = FakeResponse(content=b"", chunks=chunks, headers={"Content-Length": "10"})
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: resp)
    out = tmp_path / "f.zip"
    got = []
    artifactory.download_file("https://x", "repo/f.zip", "tok", str(out),
                              label="f.zip", log=got.append)
    assert out.read_bytes() == b"aaaabbbbcc"
    assert any("%" in line and "f.zip" in line for line in got)      # percent shown
    assert any("f.zip" in line for line in capsys.readouterr().out.splitlines())


def test_download_file_progress_without_content_length(monkeypatch, tmp_path):
    monkeypatch.setattr(artifactory, "_PROGRESS_STEP", 4)
    resp = FakeResponse(chunks=[b"aaaa", b"bbbb"], headers={})
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: resp)
    got = []
    artifactory.download_file("https://x", "repo/f.zip", "tok", str(tmp_path / "f.zip"),
                              label="f.zip", log=got.append)
    assert got and all("%" not in line for line in got)              # cumulative-only format


def test_download_file_defaults_unchanged(monkeypatch, tmp_path):
    # no label/log: behaves exactly as before (writes content, returns path)
    monkeypatch.setattr(artifactory.requests, "get",
                        lambda *a, **k: FakeResponse(content=b"hello"))
    out = tmp_path / "f.zip"
    assert artifactory.download_file("https://x", "repo/f.zip", "tok", str(out)) == str(out)
    assert out.read_bytes() == b"hello"


def test_dllog_writes_both_and_survives_write_failure(tmp_path, capsys):
    p = tmp_path / "download.log"
    dlog = artifactory._DlLog(str(p))
    dlog.emit("[dl] line one")
    dlog._fh.close()                              # force subsequent writes to fail
    dlog.emit("[dl] line two")                    # must not raise; warns once, disables file
    dlog.emit("[dl] line three")
    dlog.close()
    assert "[dl] line one" in p.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert out.count("could not write") == 1      # one-time warning
    assert "[dl] line three" in out               # stdout still gets every line


def test_download_build_writes_download_log(monkeypatch, tmp_path):
    cfg = {"artifactory_base_url": "https://x", "repo": "r", "download_path": str(tmp_path),
           "zip_filter": ["*"], "csv_files": [], "html_files": [], "default_channel": "Daily"}
    monkeypatch.setattr(artifactory, "resolve_latest_build", lambda *a, **k: "B1")
    monkeypatch.setattr(artifactory, "discover_zip_files", lambda *a, **k: ["a.zip"])

    def fake_download(base, path, tok, out, timeout=600, label="", log=None):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"x")
        return out
    monkeypatch.setattr(artifactory, "download_file", fake_download)
    monkeypatch.setattr(artifactory, "verify_checksum", lambda *a, **k: (True, "SHA256 verified"))
    monkeypatch.setattr(artifactory, "extract_zip", lambda *a, **k: 3)
    res = artifactory.download_build(cfg, "tok")
    log_text = (tmp_path / "B1" / "download.log").read_text(encoding="utf-8")
    assert "(1/1) a.zip" in log_text              # per-file start line
    assert "done" in log_text                     # completion line
    assert res["build_id"] == "B1"
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd mcp/dtm_download && python -m pytest tests/test_artifactory.py -v`
Expected: the 5 new tests FAIL (`AttributeError: _PROGRESS_STEP` / unexpected-kwarg `label`); the pre-existing tests still pass.

- [ ] **Step 3: Implement in `artifactory.py`**

Add near the top (after the imports / `ArtifactoryError`):

```python
_PROGRESS_STEP = 25 * 1024 * 1024   # emit a progress line every 25 MB


def _progress_line(label, done_bytes, total_bytes):
    mb = done_bytes // (1024 * 1024)
    if total_bytes:
        return "[dl] %s %dMB/%dMB (%d%%)" % (label, mb, total_bytes // (1024 * 1024),
                                             done_bytes * 100 // total_bytes)
    return "[dl] %s %dMB" % (label, mb)


class _DlLog:
    """Progress sink: prints every line to stdout AND appends it to <build>/download.log.
    Best-effort by design -- a file-write failure warns once, then disables the file and
    keeps printing; it must never fail a download that would otherwise succeed."""

    def __init__(self, path):
        self._fh = None
        self._warned = False
        try:
            self._fh = open(path, "a", encoding="utf-8")
        except OSError as e:
            print("[dl] warning: could not write download.log: %s" % e, flush=True)
            self._warned = True

    def emit(self, msg):
        print(msg, flush=True)
        if self._fh is None:
            return
        try:
            self._fh.write(msg + "\n")
            self._fh.flush()
        except (OSError, ValueError) as e:       # ValueError: write to closed file
            if not self._warned:
                print("[dl] warning: could not write download.log: %s" % e, flush=True)
                self._warned = True
            self._fh = None

    def close(self):
        try:
            if self._fh:
                self._fh.close()
        except OSError:
            pass
```

Replace `download_file` with:

```python
def download_file(base_url, repo_path_file, token, out_file, timeout=600, label="", log=None):
    url = "%s/%s" % (base_url.rstrip("/"), repo_path_file.lstrip("/"))
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    emit = log if log is not None else (lambda m: print(m, flush=True))
    name = label or repo_path_file.rsplit("/", 1)[-1]
    with requests.get(url, headers=_headers(token), timeout=timeout, stream=True, verify=False) as resp:
        if resp.status_code >= 400:
            raise ArtifactoryError("Download failed (HTTP %s): %s" % (resp.status_code, url))
        total = int(resp.headers.get("Content-Length") or 0)
        done, next_mark = 0, _PROGRESS_STEP
        with open(out_file, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    if (label or log is not None) and done >= next_mark:
                        while next_mark <= done:
                            next_mark += _PROGRESS_STEP
                        emit(_progress_line(name, done, total))
    return out_file
```

(The `label or log is not None` guard keeps legacy calls — no label, no log — byte-for-byte silent, so existing callers/tests see no new output.)

In `download_build`, after `os.makedirs(download_path, exist_ok=True)` insert the sink and wrap the remainder in try/finally:

```python
    dlog = _DlLog(os.path.join(download_path, "download.log"))
    try:
        dlog.emit("[dl] build %s (%s) -> %s" % (build_id, channel, download_path))
        ...  # existing body below, with the emission lines added
    finally:
        dlog.close()
```

Inside the existing body make these emission additions (keep all current logic identical):

```python
    zip_total = len(zip_names)
    for i, name in enumerate(zip_names, 1):
        out_file = os.path.join(download_path, name)
        dlog.emit("[dl] (%d/%d) %s ..." % (i, zip_total, name))
        download_file(base_url, "%s/%s" % (repo_path, name), token, out_file,
                      timeout=dl_timeout, label=name, log=dlog.emit)
        ok, detail = verify_checksum(...)          # unchanged
        if not ok:
            raise ArtifactoryError(detail)
        ...                                        # unchanged extract lines
        dlog.emit("[dl] (%d/%d) %s done (%s, extracted %d files)" % (i, zip_total, name,
                                                                     detail, file_count))
```

For the CSV loop: `dlog.emit("[dl] csv %s ok" % csv_name)` on success and
`dlog.emit("[dl] csv %s failed: %s" % (csv_name, e))` in the except branch (keep the result-dict
behavior unchanged). For the HTML loop: `dlog.emit("[dl] doc %s ok" % entry["file"])` on success (the
except branch stays a bare `continue` — docs are optional). Pass `label=`/`log=dlog.emit` to the CSV and
HTML `download_file` calls too.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mcp/dtm_download && python -m pytest tests/test_artifactory.py -v`
Expected: ALL pass (pre-existing + 5 new).

- [ ] **Step 5: Run the whole module suite**

Run: `cd mcp/dtm_download && python -m pytest -q`
Expected: all pass, no new warnings.

- [ ] **Step 6: README note** — in `mcp/dtm_download/README.md`, add 2-3 lines under an appropriate existing section: downloads emit `[dl] ...` progress lines (every 25 MB per file) to stdout → `logs/mcp/dtm_download.stdout.log`, and each build writes a self-contained `download.log` beside its artifacts.

- [ ] **Step 7: Commit**

```bash
git add mcp/dtm_download/artifactory.py mcp/dtm_download/tests/test_artifactory.py mcp/dtm_download/README.md
git commit -m "feat(dtm_download): progress lines to stdout + per-build download.log"
```

---

## Task 2: dtm_deploy MSI log tail

**Files:**
- Modify: `mcp/dtm_deploy/msi.py`
- Test: `mcp/dtm_deploy/tests/test_msi.py`
- Modify: `mcp/dtm_deploy/README.md`

**Interfaces:**
- Produces: `tail_log(log_file, n=40) -> list[str]` (never raises); `install_msi`/`uninstall_product` result dicts gain `"log_tail": tail_log(log_file)`.

- [ ] **Step 1: Write the failing tests** (append to `mcp/dtm_deploy/tests/test_msi.py`)

```python
def test_tail_log_utf16_bom(tmp_path):
    p = tmp_path / "install.log"
    lines = ["line %d" % i for i in range(50)]
    p.write_text("\n".join(lines), encoding="utf-16")      # writes BOM
    tail = msi.tail_log(str(p), n=40)
    assert len(tail) == 40
    assert tail[0] == "line 10" and tail[-1] == "line 49"


def test_tail_log_utf16le_no_bom(tmp_path):
    p = tmp_path / "install.log"
    p.write_bytes("alpha\nbeta".encode("utf-16-le"))       # BOM-less UTF-16LE
    assert msi.tail_log(str(p)) == ["alpha", "beta"]


def test_tail_log_utf8_fallback(tmp_path):
    p = tmp_path / "install.log"
    p.write_text("one\ntwo\nthree", encoding="utf-8")
    assert msi.tail_log(str(p), n=2) == ["two", "three"]


def test_tail_log_missing_file_returns_placeholder(tmp_path):
    tail = msi.tail_log(str(tmp_path / "nope.log"))
    assert len(tail) == 1 and tail[0].startswith("<unreadable:")


def test_results_include_log_tail(monkeypatch, tmp_path):
    log = tmp_path / "u.log"
    log.write_text("msi ok", encoding="utf-16")
    monkeypatch.setattr(msi, "run_msiexec", lambda args, log_file: (0, str(log)))
    r = msi.uninstall_product("{ABCDEF12-0000-0000-0000-000000000000}", str(tmp_path))
    assert r["log_tail"] == ["msi ok"]
    monkeypatch.setattr(msi, "get_msi_properties",
                        lambda p: {"ProductCode": "{X}", "ProductName": "N",
                                   "ProductVersion": "1.0", "UpgradeCode": "{U}"})
    r2 = msi.install_msi("C:/fake.msi", str(tmp_path))
    assert "log_tail" in r2
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `cd mcp/dtm_deploy && python -m pytest tests/test_msi.py -v`
Expected: the 5 new tests FAIL (`AttributeError: ... 'tail_log'` / missing key); pre-existing pass.

- [ ] **Step 3: Implement in `msi.py`**

Add (near `run_msiexec`; `codecs` from stdlib — add `import codecs` to the imports):

```python
def tail_log(log_file, n=40):
    """Last n lines of an msiexec verbose log. msiexec /l*v writes UTF-16LE (usually with a BOM);
    BOM-less UTF-16LE and plain UTF-8 are handled too. Never raises -- an unreadable file yields a
    single placeholder entry, because this feeds a result dict, not control flow."""
    try:
        with open(log_file, "rb") as f:
            raw = f.read()
        if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
            text = raw.decode("utf-16", errors="replace")
        elif b"\x00" in raw[:200]:
            text = raw.decode("utf-16-le", errors="replace")   # BOM-less msiexec log
        else:
            text = raw.decode("utf-8", errors="replace")
        return text.splitlines()[-n:]
    except OSError as e:
        return ["<unreadable: %s>" % e]
```

Extend both result dicts (one added key each, nothing else changes):

```python
def uninstall_product(product_code, log_dir):
    safe_code = re.sub(r"[{}]", "", product_code)
    log_file = os.path.join(log_dir, "uninstall_%s.log" % safe_code)
    exit_code, log_file = run_msiexec(["/x", product_code], log_file)
    return {"product_code": product_code, "exit_code": exit_code, "log_file": log_file,
            "log_tail": tail_log(log_file),
            "reboot_required": exit_code == 3010, "success": exit_code in (0, 3010)}


def install_msi(msi_path, log_dir):
    props = get_msi_properties(msi_path)
    safe_code = re.sub(r"[^0-9A-Fa-f\-]", "", props.get("ProductCode") or "unknown")
    log_file = os.path.join(log_dir, "install_%s.log" % safe_code)
    exit_code, log_file = run_msiexec(["/i", msi_path], log_file)
    return {"msi_path": msi_path, "properties": props, "exit_code": exit_code, "log_file": log_file,
            "log_tail": tail_log(log_file),
            "reboot_required": exit_code == 3010, "success": exit_code in (0, 3010)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mcp/dtm_deploy && python -m pytest tests/test_msi.py -v`
Expected: ALL pass (pre-existing + 5 new). Note: pre-existing tests monkeypatch `run_msiexec` with paths that don't exist — their results now carry the `<unreadable: ...>` placeholder in `log_tail`, which no existing assertion touches.

- [ ] **Step 5: Run the whole module suite**

Run: `cd mcp/dtm_deploy && python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: README note** — in `mcp/dtm_deploy/README.md`, add 1-2 lines: `dtm_install`/`dtm_uninstall` results include `log_tail` (last 40 lines of the msiexec verbose log) so failures are diagnosable without opening `log_file`.

- [ ] **Step 7: Commit**

```bash
git add mcp/dtm_deploy/msi.py mcp/dtm_deploy/tests/test_msi.py mcp/dtm_deploy/README.md
git commit -m "feat(dtm_deploy): msiexec log tail in install/uninstall results"
```

---

## Task 3: report_progress feasibility experiment

**Files:**
- Create: `docs/superpowers/specs/2026-07-18-report-progress-experiment.md` (the ONLY committed artifact)
- Scratchpad (NOT committed): a mini stdio MCP + driver, in the session scratchpad directory.

**Interfaces:** None. Deliverable = the findings note answering: does `goose run` render MCP progress notifications to stdout (where goose_web's parser would see them)?

- [ ] **Step 1: Build the throwaway mini-MCP** in the scratchpad directory (`slow_mcp.py`):

```python
"""Throwaway: one slow tool that emits MCP progress notifications. stdio transport."""
import anyio
from mcp.server.fastmcp import FastMCP, Context

mcp = FastMCP("slowdemo")

@mcp.tool()
async def slow_task(ctx: Context) -> str:
    """Sleeps ~10s, reporting progress every second."""
    for i in range(1, 11):
        await ctx.report_progress(i, 10)
        await anyio.sleep(1)
    return "slow_task complete"

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 2: Drive it via goose** (goose supports ad-hoc stdio extensions):

```powershell
goose run --with-extension "python <scratchpad>\slow_mcp.py" -t "Call the slow_task tool and tell me its result."
```

Capture the FULL stdout (redirect to a file in the scratchpad). If `--with-extension` is unsupported in the installed goose version, fall back to temporarily adding a stdio extension entry to a COPY of config.yaml and pointing `GOOSE_CONFIG` at it — do NOT edit the live config.yaml.

- [ ] **Step 3: Analyze** — grep the captured stdout for any rendering of the 1..10 progress (percent lines, progress bars, notification dumps). Decision rule: any per-tick visible output between tool_start and the result = FEASIBLE; silence until the final result = NOT FEASIBLE (goose swallows progress notifications).

- [ ] **Step 4: Write the findings note** `docs/superpowers/specs/2026-07-18-report-progress-experiment.md`: goose version tested, exact command, what appeared on stdout (paste the relevant excerpt), verdict (FEASIBLE / NOT FEASIBLE for the goose_web pipeline), and the recommendation (feasible → follow-up todo to wire `ctx.report_progress` into dtm_download/dtm_deploy + goose_web rendering; not feasible → question closed, stdout progress lines from Task 1 are the answer).

- [ ] **Step 5: Clean up** — delete nothing from the scratchpad manually (it is session-isolated), but verify `git status` shows ONLY the findings note as new.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-07-18-report-progress-experiment.md
git commit -m "docs: report_progress feasibility findings"
```

---

## Self-Review Notes

- Spec coverage: download progress + per-build log (Task 1); log_tail with UTF-16 handling (Task 2); experiment verify-only with committed findings (Task 3); best-effort error handling encoded in `_DlLog`/`tail_log`; READMEs in Tasks 1-2; no `logging` module anywhere.
- Backward compatibility: `download_file` legacy calls (no label/log) stay silent via the emission guard; existing msi tests unaffected by the added `log_tail` key.
- Type consistency: `log` is a plain callable everywhere (`dlog.emit`, `got.append`); `tail_log` always returns `list[str]`.
