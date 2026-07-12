# Hardening backlog

Durable state for the hardening loop (`docs/LOOP_PROMPT.md`). One item = one defect.
Read this before starting work; update it before finishing.

Severity: **Important** = wrong/fragile behaviour a user can hit · **Minor** = polish, no user impact.

---

## Open

_Seeded from the 2026-07-12 audit of the 12 diagnostic MCPs and goose_web. Not yet verified beyond a
code read — the loop must reproduce each one before fixing it, and move it to `## Rejected` if it can't._

- **[Important, RAISED] The loopback MCP servers have no authentication.** They bind `127.0.0.1` and
  accept any caller, so *any* unprivileged local process can reach them. For the 12 read-only diagnostic
  MCPs that is information disclosure (a UAC-free window onto admin-level data: SRUM, Security log,
  Prefetch, pool tags). **`dtmsdk` (port 8789) raises the stakes: it is NOT read-only** — a local process
  could drive DTP, transmit telemetry to Dell, or change DTP config through it. dtmsdk's confirm-token
  gate is an argv binding, not a real second factor (deterministic, self-serviceable by the caller — see
  `mcp/dtm_sdk/DESIGN.md`), so it does not mitigate an unauthenticated local caller. A per-machine shared
  token (via each extension's `headers:`) would close both. **Needs a user decision before implementing**
  (touches all 13 servers + `config.yaml` + the installer). Do not unilaterally change the security model
  — ask.

- **[Minor] `RUN.md` never mentions the Windows diagnostic MCP suite.** `README.md` points at it for
  "how to launch the harness", but it covers only the GB10/DTM/PK side — a reader looking for
  Windows-MCP usage finds nothing. Decide: extend it, or narrow its stated scope.

- **[Minor] 24 scripts resolve Python with no friendly error.** Every `mcp/windows_*/install_task.ps1`
  and `start_*_mcp.ps1` calls `(Get-Command python).Source` bare, so a missing Python dies with a raw
  `CommandNotFoundException` instead of `setup_mcp_servers.ps1`'s `[X] Python 3 not found...`.

- **[Minor] `mcp_toggle.ps1` overstates atomicity.** The comment says "atomic rename on NTFS" but
  `Move-Item -Force` is delete-then-rename, not an atomic replace. Either use
  `[System.IO.File]::Replace()` or fix the comment. (`server.py` uses `os.replace`, which *is* atomic —
  so this is also a py↔ps parity gap.)

- **[Minor] `server.py` swallows the backup write error.** In `_atomic_write_config`, a failed
  `bak.write_bytes()` is caught and ignored, so a config write can proceed with no backup and no
  warning.

- **[Minor] `index.html` reports toggle failures with `alert()`**, which is inconsistent with the rest
  of the UI's inline error style and blocks the page.

- **[Minor] `workspace/hello.txt` is untracked and unexplained.** Decide whether it's a fixture worth
  committing or leftover scratch worth deleting/ignoring.

## Done

- **[Minor] `windows_crash` couldn't find `cdb.exe` on Windows-on-ARM.** `_find_cdb()` hardcoded
  `Debuggers\x64\cdb.exe`, but the Debugging Tools install per-architecture, so on ARM64 the probe
  always missed and BSOD decoding silently degraded to the header-only parse. Now picks the arch dirs
  the *process* can execute (`platform.machine()`), native first: ARM64 → `arm64` then emulated `x64`;
  AMD64 → `x64` only, so an x64 box can never pick up a cross-installed arm64 cdb it can't run.
  Behaviour on x64 is byte-identical to before — there's a regression test asserting exactly that.
  Still unverified on real ARM64 hardware (no such box here); the *logic* is tested by monkeypatching
  `platform.machine()`. → `2df5716`

  Context: goose ships no Windows ARM64 build (checked: v1.41.0 has only `x86_64-pc-windows-msvc`), so
  it runs x64-emulated there — but the Python MCPs can run native ARM64 (`psutil` and `pywin32` both
  publish `win_arm64` wheels, and the ctypes structs are portable because ARM64 Windows is LLP64 like
  x64). The two need not match: architecture doesn't cross the loopback HTTP boundary.

- **[Important] goose_web mangled non-ASCII input.** `HttpListenerRequest.ContentEncoding` fell back to
  the system ANSI codepage (Big5 here) because the browser sends `application/json` with no `charset`,
  so Chinese chat messages reached `goose` as mojibake; `QueryString` %-decoded with the same encoding,
  mangling non-ASCII upload filenames too. Forced UTF-8 in `goose_web/http_encoding.ps1`. → `57dd2e8`

- **[Important] `setup_mcp_servers.ps1`'s `$deps` was a hand-copied duplicate** of the `requirements.txt`
  union and had already drifted (all 12 declared `pytest>=8.0`; the installer never installed it). Now
  read from the files. → `756a8f3`

- **[Important] `docs/SETUP_GUIDE.md` was stale and orphaned** — it described a 2-of-12 manual install,
  predating both the one-click installer and 10 of the servers, and nothing linked to it. Rewrote the
  Windows half, linked it from `README.md`. → `756a8f3`

- **[Important] No way to uninstall the MCP suite.** Added `setup_mcp_servers.ps1 -Uninstall`. → `756a8f3`

- **[Important] Privilege model was undocumented**, conflating three different things (installer needs
  admin / servers start elevated *at logon* / goose never needs admin). Documented per-MCP in
  `mcp/README.md`. → `756a8f3`

- **[Minor] `Handle-Toggle` handshaked on the request path**, so enabling an MCP whose backend was down
  blocked the response for the handshake timeout, and the toggled card bounced to the end of the
  sidebar. Now updates in place and wakes the discoverer via a `ManualResetEvent`. → `dfa0a08`

## Rejected

_Nothing yet. When an item can't be reproduced, move it here with what was tried — so it doesn't get
re-opened on a later pass._
