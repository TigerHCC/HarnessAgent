# Windows Crash / WER MCP

A local, **read-only** MCP server that answers *"what has been crashing / hanging / blue-screening on
this machine, and why"* from two binary/proprietary sources the shell can't easily read:

- **WER report store** — app crashes (APPCRASH/BEX/BEX64), hangs (AppHangB1/MoAppHang), and other
  failures, as UTF-16 `Report.wer` files scattered across hundreds of folders in two stores.
- **Crash dumps** — kernel BSOD minidumps (`C:\Windows\Minidump`, `PAGEDU64` / DUMP_HEADER64),
  `MEMORY.DMP`, `LiveKernelReports`, and user-mode `.dmp` (MDMP) — decoded to bugcheck / exception /
  faulting module **without a debugger installed**.

Third sibling of the [`windows_srum`](../windows_srum) (8777) and [`windows_eventlog`](../windows_eventlog)
(8778) servers. Complements them: `eventlog` sees the terse System-log crash event, `srum` the resource
history — this reads the rich payload neither exposes. Rationale + full candidate ranking:
[`docs/windows-diagnostic-mcp-candidates.md`](../../docs/windows-diagnostic-mcp-candidates.md).

## Tools (6)
| Tool | What it answers |
|---|---|
| `crash_summary(days=30, top_n=20, include_noncrash=False)` | **Start here.** Recent crashes/hangs deduped into buckets `(event_type, app, faulting_module, code)` with counts + first/last seen. |
| `list_crashes(days=30, event_type=None, app=None, max=50)` | Flat newest-first list of parsed reports. Filter by event type / app substring. |
| `get_crash(report_id)` | Full parsed `Report.wer`: signatures, typed fields, OS info, attached files. |
| `list_dumps()` | Enumerate Minidump / MEMORY.DMP / LiveKernelReports (size + time). |
| `analyze_dump(path, use_cdb=False)` | Decode a dump: kernel → bugcheck code/name/params + build; user → exception + loaded modules. `use_cdb=True` runs `!analyze -v` if `cdb.exe` is installed. |
| `crash_health()` | Admin status, store paths + counts, dump counts, `cdb` availability, table sizes. |

Every tool returns a structured `{...}` (errors as `{"error": ...}`), never raises.

## Run it
```powershell
# Elevated (kernel dumps + some WER folders need admin):
.\start_crash_mcp.ps1
# or persist as a logon Scheduled Task 'Crash-MCP':
.\install_task.ps1 ; Start-ScheduledTask -TaskName Crash-MCP
```
Serves `http://127.0.0.1:8779/mcp` (streamable HTTP; Goose 1.39 dropped SSE). Pure stdlib parsing —
no `minidump`/`libesedb`; `cdb.exe` is optional and only enhances `analyze_dump`.

## goose extension config
Add to `%APPDATA%\Block\goose\config\config.yaml` (and the repo template `config/windows_config.yaml`):
```yaml
  crash:
    type: streamable_http
    bundled: false
    name: crash
    enabled: true
    uri: http://127.0.0.1:8779/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows crash/WER analysis (app crashes, hangs, BSOD bugchecks) via local elevated MCP server (127.0.0.1:8779)
```

## Verify
```powershell
python -c "import crash_mcp_server as s; print(s.list_tool_names())"   # 6 tools
pytest -q                                                              # unit + smoke
```
Through goose: ask *"用 crash_summary 看最近 30 天有什麼在當機"* — it returns the APPCRASH/BEX/AppHang
buckets. `analyze_dump` on a `C:\Windows\Minidump\*.dmp` returns the bugcheck name.

## Gotchas
- **UTF-16 everywhere.** `Report.wer` is UTF-16 LE and contains localized (non-ASCII) `Sig[].Name`
  values — the reader decodes as `utf-16`; do **not** route it through the console/cp950 path.
- **`Sig[].Name` is localized**, so crash signatures are parsed by `(EventType, index)` position, not
  by name text. The raw `signatures` array is always returned so the agent can fall back.
- **`C:\Windows\Minidump` holds *kernel* dumps** (`PAGEDU64`), not user MDMP. The header parse gives
  bugcheck + params + build; a symbol-resolved culprit driver needs `cdb` (`use_cdb=True`).
- Not elevated → machine `ReportArchive` still reads; kernel dumps return a "requires admin" note.
- Read-only: no tool deletes/clears/uploads a report or dump; `analyze_dump` is path-confined to the OS
  dump dirs + WER store.

## Verified (2026-07-11)
29 unit tests green · full MCP handshake → all 6 tools · `crash_summary` buckets a real BEX64
(`IntelProviderDataHelperService.exe` STACK_BUFFER_OVERRUN ×5) with dump counts · `analyze_dump` on a
real kernel minidump → `INTERNAL_POWER_ERROR` (0xA0), build 26100, x64 · `get_crash` → svchost.exe /
ntdll.dll / ACCESS_VIOLATION · path-containment rejects out-of-scope paths · discovered by the
goose_web sidebar as `crash [ok] tools=6`. Hardened per an adversarial review (bounded MDMP mmap parse,
FILETIME overflow guard, TOCTOU-safe path resolution, observable truncation).

## Files
`crash_mcp_server.py` (FastMCP, 6 tools) · `wer_reader.py` (WER scan/parse/bucket) ·
`dump_reader.py` (dump enum + struct parse + optional cdb) · `bugchecks.py` (bugcheck + NTSTATUS
tables) · `start_crash_mcp.ps1` / `install_task.ps1` / `uninstall_task.ps1` · `tests/`.
