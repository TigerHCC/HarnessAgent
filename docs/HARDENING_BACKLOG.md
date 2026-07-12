# Hardening backlog

Durable state for the hardening loop (`docs/LOOP_PROMPT.md`). One item = one defect.
Read this before starting work; update it before finishing.

Severity: **Important** = wrong/fragile behaviour a user can hit · **Minor** = polish, no user impact.

---

## Open

_Seeded from the 2026-07-12 audit of the 12 diagnostic MCPs and goose_web. Not yet verified beyond a
code read — the loop must reproduce each one before fixing it, and move it to `## Rejected` if it can't._

- **[Important] The 12 MCP servers have no authentication.** They bind `127.0.0.1` and accept any
  caller, so *any* unprivileged local process can read data that would otherwise require Administrator
  (SRUM, Security log, Prefetch, pool tags). Read-only, so it's information disclosure rather than
  privilege escalation — but it is effectively a UAC-free window onto admin-level data.
  **Needs a user decision before implementing** (a shared token via the extension's `headers:` is the
  obvious minimal fix, but it touches all 12 servers + `config.yaml` + the installer). Do not
  unilaterally change the security model — ask.

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
