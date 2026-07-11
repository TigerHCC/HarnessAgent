# Windows Crash / WER MCP — Design Spec

> Date: 2026-07-11 · Status: approved → implementation
> Location: `HarnessAgent/mcp/windows_crash/` · Boundary: **never modifies `PersonalKnowledge-GB10`**
> Third sibling of the SRUM (`8777`) and Event Log (`8778`) MCPs; same architecture pattern.
> Rationale + tier ranking: `docs/windows-diagnostic-mcp-candidates.md` (this is candidate #1, value 5).

## 1. Goal
Give the Windows goose harness read-only tools to answer **"what has been crashing / hanging / blue-screening on this machine, and why"** from two binary/proprietary sources the shell cannot easily read:
- **WER report store** — every app crash (APPCRASH/BEX/BEX64), hang (AppHangB1/MoAppHang), and
  install/other failure the OS recorded, as UTF-16 `Report.wer` key=value files scattered across
  hundreds of folders in two stores.
- **Crash dumps** — kernel BSOD minidumps (`C:\Windows\Minidump\*.dmp`, DUMP_HEADER64 / `PAGEDU64`
  magic), `MEMORY.DMP`, `LiveKernelReports\**\*.dmp`, and user-mode `.dmp` (MDMP magic) — decoded to
  bugcheck code / exception / faulting module without needing a debugger installed.

Complements `eventlog` (which sees the terse System-log crash event) and `srum` (resource history):
this MCP reads the rich diagnostic payload neither exposes.

## 2. Architecture
```
Windows machine (single host)
  goose (USER mode) ──streamable_http──▶ 127.0.0.1:8779/mcp
   └ extension: crash                        │
                                             ▼
                    crash_mcp_server.py  (ELEVATED / admin, read-only)
                      ├ wer_reader.py    (WER store scan + Report.wer UTF-16 parse + bucketing)
                      ├ dump_reader.py   (dump enumeration + DUMP_HEADER64/MDMP struct parse + optional cdb)
                      └ bugchecks.py     (bugcheck-code + NTSTATUS exception-code lookup tables)
```
- Runs **elevated**: `C:\Windows\Minidump`, `MEMORY.DMP`, and some WER `ReportQueue` folders are
  admin-only. The machine `ReportArchive` under `C:\ProgramData` is usually readable unprivileged, so
  WER tools degrade gracefully (per-folder `PermissionError` is skipped, never fatal).
- goose runs **user mode**, talks over **loopback HTTP** — privilege decoupled (same as SRUM/eventlog).
- Bind **`127.0.0.1:8779`** only. Transport streamable HTTP via `mcp` SDK (FastMCP). Goose 1.39 dropped SSE.

## 3. Constraints / assumptions
- Windows only. Python 3.13, **stdlib only** for parsing (no `minidump`/`libesedb`; `cdb` optional).
  `mcp` + `pywin32` already present (pywin32 unused by readers — `ctypes` for `is_admin`).
- **UTF-16 LE everywhere.** `Report.wer` begins `FF FE` and CONTAINS non-ASCII (localized Chinese
  `Sig[].Name` values on this box). Read with `encoding="utf-16"`; never touch cp950/console decoding
  (this is the exact cp950 pitfall the goose_web server already hit).
- **`Sig[].Name` is localized** → parse crash signatures by **(EventType, index)** position, not name text.
- Read-only: no tool writes/clears/deletes any report or dump.
- No changes to `PersonalKnowledge-GB10`.

## 4. Tool surface (6)
- `crash_summary(days=30, top_n=20, include_noncrash=False)` → **headline tool**. Scans the WER store,
  dedupes into buckets keyed by `(event_type, app, faulting_module, code)`, returns
  `{window_days, total_reports, bucket_count, buckets:[{event_type, app, faulting_module, code,
  code_meaning, count, first_seen, last_seen, sample_report}], dumps:{minidumps, memory_dmp,
  livekernel}}` (the `dumps` counts are merged in by the server so one call answers "is there a BSOD
  dump to look at"). `include_noncrash` folds in StoreAgentInstallFailure / WcpOtherFailure etc.
  (default: real crashes/hangs only).
- `list_crashes(days=30, event_type=None, app=None, max=50)` → `{window_days, count, total_matching,
  truncated, crashes:[{report_id, store, folder, event_type, app, app_path, faulting_module,
  exception_code, code_meaning, is_fatal, time, bucket_id, has_dump}]}`, newest first. `total_matching`
  + `truncated` make the `max` cap observable so an agent never mistakes a capped list for the full count.
- `get_crash(report_id)` → full parsed `Report.wer` for one report (all sections: header fields,
  `signatures:[{index,name,value}]`, `parsed` typed fields, `dynamic`, `os_info`, `files:[...]`).
  `report_id` = the report folder name (as returned by the list tools).
- `list_dumps()` → `{minidumps:[{path,size_mb,modified}], memory_dmp?, livekernel:[...]}`.
- `analyze_dump(path)` → parse one dump. Kernel (`PAGEDU64`): `{kind:"kernel", bugcheck_code,
  bugcheck_name, bugcheck_desc, parameters:[4], build, machine, processors}`. User (`MDMP`):
  `{kind:"user", exception_code, code_meaning, exception_address, modules:[...]}`. If `cdb` is
  installed, adds `analyze_v:{probably_caused_by, failure_bucket, image_name, stack:[...]}` (cached by
  path+mtime); otherwise `analyze_v_available:false` with the one-line install hint.
- `crash_health()` → `{is_admin, stores:[{path, exists, report_count, readable}], dumps:{...},
  cdb_available, bugcheck_table_size, sample_ok}`.

All list/summary tools cap at `max`/`top_n`. Every tool returns a structured `{error:...}` dict
instead of raising.

## 5. WER reader (`wer_reader.py`)
1. Enumerate report folders under the machine store `C:\ProgramData\Microsoft\Windows\WER\
   {ReportArchive,ReportQueue}` and per-user `%LOCALAPPDATA%\Microsoft\Windows\WER\{...}` (skip
   `PermissionError`). Each folder holds one `Report.wer` + optional attachments (`.dmp`, `.txt`,
   `WERInternalMetadata.xml`, minidump).
2. Parse `Report.wer` as `encoding="utf-16"`, split `Key=Value` per line into an ordered dict.
   Collect `Sig[n]`, `DynamicSig[n]`, `State[n]`, `OsInfo[n]` families into arrays keyed by index.
3. `EventTime` is a Windows **FILETIME** (100 ns ticks since 1601-01-01 UTC) → ISO 8601; fall back to
   folder mtime when absent.
4. Typed `parsed` fields by EventType position map (values are language-independent even though names
   aren't). APPCRASH: Sig0 app / Sig1 app_ver / Sig2 app_ts / Sig3 module / Sig4 mod_ver / Sig5 mod_ts
   / Sig6 exception_code / Sig7 exception_offset. BEX/BEX64: Sig6 offset / Sig7 exception_code / Sig8
   data. AppHangB1/MoAppHang: Sig0 app / Sig1 app_ver / Sig3 hang-signature. Unknown types → app from
   `OriginalFilename`/`AppPath` only.
5. `exception_code` (e.g. `c0000005`) mapped via `bugchecks.NTSTATUS_EXCEPTIONS`
   (c0000005 ACCESS_VIOLATION, c0000409 STACK_BUFFER_OVERRUN, e0434352 CLR exception, …).
6. Bucketing: group by `(event_type, app.lower(), faulting_module.lower(), code)`; count, min/max time,
   keep one sample folder. Results cached ~60 s (store changes slowly).

## 6. Dump reader (`dump_reader.py`)
- **Kernel** (`PAGEDU64` / 32-bit `PAGEDUMP`): struct-parse DUMP_HEADER64 — Signature/ValidDump,
  MajorVersion@0x08, **MinorVersion(build)@0x0C**, MachineImageType@0x30, NumberProcessors@0x34,
  **BugCheckCode@0x38**, 4× BugCheckParameter @0x40/0x48/0x50/0x58. Map code → name+desc via
  `bugchecks.BUGCHECKS`. (Verified on this box: build 26100 decodes correctly.)
- **User** (`MDMP`): parse MINIDUMP_HEADER (stream count/RVA) → walk the stream directory for
  `ExceptionStream` (code + address) and `ModuleListStream` (loaded module names/versions). Pure struct,
  no `minidump` lib.
- **cdb (optional)**: locate `cdb.exe` in known SDK paths / PATH. If present, `analyze_dump` may run
  `cdb -z <dmp> -c "!analyze -v; q"` (with `_NT_SYMBOL_PATH` msdl) and regex-extract
  PROBABLY_CAUSED_BY / FAILURE_BUCKET_ID / IMAGE_NAME / stack; cache by path+mtime. Absent → header
  parse still answers "which bugcheck, which build"; report `cdb_available:false`.

## 7. Bugcheck / exception tables (`bugchecks.py`)
Curated dicts, no deps: `BUGCHECKS` = {code:(name, short_desc)} for the common stop codes (0x0A
IRQL_NOT_LESS_OR_EQUAL, 0x1E, 0x50 PAGE_FAULT_IN_NONPAGED_AREA, 0x7E, 0x9F
DRIVER_POWER_STATE_FAILURE, 0xA0 INTERNAL_POWER_ERROR, 0x101, 0x116 VIDEO_TDR, 0x117, 0x124
WHEA_UNCORRECTABLE_ERROR, 0x133 DPC_WATCHDOG_VIOLATION, 0x139 KERNEL_SECURITY_CHECK_FAILURE, 0x1A, …).
`NTSTATUS_EXCEPTIONS` = {hexstr:(name, desc)} for user-mode exception codes. Both expose a
`describe()` helper returning `(name, desc)` or `(None, None)`.

## 8. Files & responsibilities
| File | Responsibility |
|---|---|
| `crash_mcp_server.py` | FastMCP server; registers the 6 tools; serves 127.0.0.1:8779 |
| `wer_reader.py` | WER store scan + Report.wer UTF-16 parse + typed signatures + bucketing (no MCP deps) |
| `dump_reader.py` | dump enumeration + DUMP_HEADER64/MDMP struct parse + optional cdb (no MCP deps) |
| `bugchecks.py` | bugcheck-code + NTSTATUS exception lookup tables + describe() (no deps) |
| `start_crash_mcp.ps1`, `install_task.ps1`, `uninstall_task.ps1` | run / persist (Scheduled Task `Crash-MCP`, port 8779) |
| `requirements.txt` | `mcp`, `pywin32`, `pytest` |
| `README.md` | install + goose snippet + verify + gotchas |
| `tests/` | unit (wer/dump/bugchecks against real store) + smoke (6 tools registered) |

## 9. goose extension config (added to Windows live config + repo template)
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

## 10. Error handling
- Not elevated → WER machine `ReportArchive` still read; `Minidump`/`MEMORY.DMP` return a structured
  "requires admin" note per source; `crash_health` reports which stores are readable.
- Missing store / dump dir → `exists:false`, empty list (not an error).
- Corrupt/short `Report.wer` or dump header → that item flagged `{parse_error:...}`, scan continues.
- `analyze_dump` on a path outside the known dump dirs → refused (path-containment; read-only anyway).

## 11. Testing
- `wer_reader`: parses the real store (≥1 APPCRASH bucket), FILETIME→ISO correct, UTF-16 + non-ASCII
  `Sig` names survive, position map extracts exception_code `c0000005`→ACCESS_VIOLATION.
- `dump_reader`: enumerates `C:\Windows\Minidump`; kernel header parse yields a plausible build +
  bugcheck; cdb-absent path returns the hint not an exception.
- `bugchecks`: known codes describe(); unknown → (None, None).
- Smoke: server registers all 6 tools. Integration: MCP handshake → `crash_health` + `crash_summary`;
  then end-to-end through goose + visible in the goose_web sidebar.

## 12. Security
Loopback-only, no auth (local-tool model). Elevated + strictly read-only; `analyze_dump` path-confined
to the OS dump directories; no tool deletes/uploads a report or dump.

## 13. Out of scope (YAGNI)
Full kernel-memory analysis / symbol-resolved stacks without cdb. Auto-installing the Debuggers.
Uploading dumps anywhere. Cross-machine. Writing/clearing WER. Real-time crash push.
Cross-correlation with SRUM/eventlog (the agent composes that itself across the three MCPs).

## 14. Open risks
- Signature **position maps drift** by EventType/OS build → always return the raw `signatures` array so
  the agent can fall back; typed `parsed` is best-effort per known type.
- Kernel minidump layout could vary by build → validate build field sanity; on mismatch, return raw
  header fields flagged `layout_uncertain` rather than a wrong bugcheck.
- Large `MEMORY.DMP` (GB) → never read whole; header-only unless cdb drives it.
