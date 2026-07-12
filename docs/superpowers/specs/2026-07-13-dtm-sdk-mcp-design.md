# DTM Sample/SDK Util MCP — design

**Date:** 2026-07-13
**Status:** approved (brainstorming → plan)

## Summary

A new MCP server, `dtmsdk`, that exposes the five DTM Sample Utilities (DTP SDK CLI tools) to the
Goose harness: `DTMUtil`, `DtpInstrumentationUtil`, `DtpAnalyticsUtil`, `DtpTransmissionUtil`,
`DTMPlatinumUtil` — 65 commands in total. It also exposes the three datatype GUID tables and the
`Sample_Utilities_HowTo.md` reference as queryable tools.

All executable and reference-file paths live in a single config file with variable expansion,
environment-variable overrides, and auto-probing, so the MCP can be deployed to another machine by
changing one line.

## Why this is different from the other MCPs

The twelve `windows_*` diagnostic MCPs are **strictly read-only with respect to the system**. This one
is not, and that difference is the central design constraint:

- Some commands **send telemetry off this machine to Dell** (`collect-transmit`, `file-upload`,
  `platinum-event`, `emit-custom-software-telemetry-event`, …).
- Some **change DTP configuration** (`enable-datatype`, `configure-orchestrator`, `configure-proxy`,
  `unregister`, …).
- All five utils **require Administrator** and a running **Dell TechHub** service.

So this MCP ships a confirmation gate (below). The README must state plainly that `dtmsdk` is the
first MCP in this repo that can change state and egress data.

## Goals

- Expose all 65 commands, without hard-coding 65 tool signatures.
- Make it impossible for the agent to *accidentally* transmit data or mutate DTP config.
- Make datatype names/GUIDs queryable (the HowTo warns that datatype matching is case-sensitive and
  a typo yields a confusing "Datatype Not Found").
- Deploy to another machine by editing one config value.

## Non-goals

- Wrapping the DTP SDKs directly (we shell out to the shipped utils).
- Building the utils from source.
- Any tool that needs the DCF/plugin internals.

## Deployment

Same pattern as the twelve diagnostic MCPs:

| Property | Value |
|---|---|
| Directory | `mcp/dtm_sdk/` |
| Extension id | **`dtmsdk`** (NOT `dtm` — that is the GB10 RAG agent) |
| Transport | `streamable_http`, `http://127.0.0.1:8789/mcp` |
| Port | **8789** (next free after the 8777–8788 diagnostic block) |
| Scheduled Task | `DtmSdk-MCP`, `RunLevel Highest`, `-AtLogOn`, runs as the current user |
| Files | `install_task.ps1` / `uninstall_task.ps1` / `start_dtm_sdk_mcp.ps1` |
| One-click | registered in `setup_mcp_servers.ps1`'s `$MCPS` (and therefore in `-Uninstall`) |
| goose_web | automatically togglable (loopback + `streamable_http` satisfies the predicate) |

Starting the MCP is inert — it only launches a util when a tool is called — so auto-start at logon
carries no egress risk.

## Configuration

`mcp/dtm_sdk/config.json`. Resolution order for every path: **`${var}` expansion → environment-variable
override → auto-probe fallback**.

```json
{
  "samples_root": "C:/Users/a9027/source/Agentic/DTM/DTPSamples-4.0.0.9390_x64_Release/Samples",
  "docs_root": "${repo_root}/docs/dtm_sdk_doc",

  "executables": {
    "dtmutil":         "${samples_root}/DTMUtil/bin/Release/DTMUtil.exe",
    "instrumentation": "${samples_root}/DtpInstrumentationUtil.SubAgent/bin/Release/DtpInstrumentationUtil.exe",
    "analytics":       "${samples_root}/DtpAnalyticsUtil.SubAgent/bin/Release/DtpAnalyticsUtil.exe",
    "transmission":    "${samples_root}/DtpTransmissionUtil.SubAgent/bin/Release/DtpTransmissionUtil.exe",
    "platinum":        "${samples_root}/DTMPlatinumUtil.SubAgent/bin/Release/DTMPlatinumUtil.exe"
  },

  "datatype_tables": {
    "instrumentation": "${docs_root}/InstrumentationDatatypeTable.csv",
    "analysis":        "${docs_root}/AnalysisDatatypeTable.csv",
    "alert":           "${docs_root}/AlertDatatypeTable.csv"
  },
  "howto": "${docs_root}/Sample_Utilities_HowTo.md",

  "default_client_id": "675f1370-b7ce-4113-8d6e-a128ee3bb74b",
  "default_client_name": null,

  "timeout_seconds": 120,
  "timeout_overrides": { "transmission:collect-transmit": 600 },

  "policy": { "<util>": { "safe": [ ... ], "blocked": [ ] } }
}
```

- **`${var}`** resolves against other top-level config keys plus the built-in `repo_root`.
- **Env override:** any key is overridable as `DTM_SDK_<UPPER_SNAKE>` — e.g.
  `DTM_SDK_SAMPLES_ROOT`, `DTM_SDK_TIMEOUT_SECONDS`. Env wins over the file.
- **Auto-probe:** if an executable path does not exist, probe a small list of conventional roots for
  a directory matching `DTPSamples-*/Samples`. A probe hit is *reported* by `dtm_health()`, never
  silently substituted without being visible.
- **`default_client_id` / `default_client_name`:** the default `--id` (and optional `--appName`) injected
  into every command for all five utils — **unless the caller already passed `--id`**, in which case the
  caller's value wins ("default" = used when not specified). Ships as `675f1370-…`, the shared built-in
  default for instrumentation/analytics/transmission. `default_client_name` is optional (id-only is valid;
  it is a default, not a mandatory pair). *(Revised after the initial `app_id`/`app_name` pairing design,
  per the requirement that all utils default to `--id 675f1370-…`.)*

`dtm_health()` reports, for every configured path: the raw value, the resolved value, whether it
exists, and which resolution step produced it. A missing exe must produce a message naming the config
key — never a bare `FileNotFoundError`.

## Tools

Nine tools, in two layers.

### Lookup layer — no exe, no admin

| Tool | Purpose |
|---|---|
| `dtm_datatypes(kind, search=None, commodity=None, max=50)` | Search the datatype tables. `kind` ∈ `instrumentation` (163 rows) / `analysis` (44) / `alert` (61). Returns name, GUID, commodity, and the key metadata columns. |
| `dtm_datatype(name)` | One datatype in full, including dependency rules. **Name matching is case-insensitive and suggests near-misses**, which directly defuses the HowTo's case-sensitivity trap. |
| `dtm_help(util, command=None)` | Returns the relevant section of `Sample_Utilities_HowTo.md`. |

`dtm_help` is the key design decision: **we do not hard-code the 65 command signatures.** When the
agent needs to know a command's options, it reads the shipped documentation. A util gaining a new
option therefore requires no MCP change.

### Execution layer — requires admin

Five tools, one per util, all with the same shape:

```
dtm_run_dtmutil(command: str, args: list[str] = [], confirm_token: str = "") -> dict
dtm_run_instrumentation(...)
dtm_run_analytics(...)
dtm_run_transmission(...)
dtm_run_platinum(...)
```

`command` is the (sub)command, e.g. `"metadata"` or `"workflow retrieve collection"`. `args` are
additional CLI options. Commands are validated against `^[a-z0-9][a-z0-9 -]*$` and passed as an argv
**list** — never through a shell — so there is no shell-injection surface.

### Health

`dtm_health()` — resolved config paths, exe existence, `is_admin`, **Dell TechHub service state**,
datatype tables loaded (row counts), HowTo present. The HowTo's Troubleshooting section identifies "Dell
TechHub not running" and "not elevated" as the two dominant causes of confusing failures, so health
surfaces both up front.

## Policy and the confirmation gate

Each util has a `safe` allowlist in config. **A command not on its util's `safe` list — whether known
to be dangerous or simply unrecognised — requires confirmation.** Unknown commands are therefore
treated as dangerous, which fails safe when a future util version adds a command.

An unconfirmed dangerous call does **not** execute. It returns a preview:

```json
{
  "requires_confirmation": true,
  "confirm_token": "<sha256(util|command|args)[:16]>",
  "command_line": "DtpTransmissionUtil.exe collect-transmit --datatype-name BatteryDynamicData",
  "category": "egress",
  "reason": "transmits telemetry from this machine to Dell",
  "expires_in_seconds": 120
}
```

The agent must call again with that `confirm_token`. The server recomputes the hash from the
*incoming* util/command/args; a mismatch is refused. Tokens are **single-use** and expire after 120s.

This binding is the point: a token issued for a preview of command A **cannot** be used to execute
command B. Without it, an agent that had been told "yes" once could execute anything.

`blocked` is an optional per-util hard-deny list (empty by default) — a command on it cannot run even
with a token.

### Classification (derived from the HowTo's documented SDK methods, not from command names)

Name-based guessing is unsafe here, and the source material proves it:
`bundle-transmission-date-range` *sounds* like it transmits but calls
`RetrieveBundleTransmissionStatusItemsAsync` — a pure query. `platinum-ping` *sounds* harmless but calls
`OneTimePingAsync` against Dell's Platinum transmitter and takes `--class` / `--unified-consent`.

**SAFE (24) — read-only queries that terminate:**

| Util | Safe commands |
|---|---|
| dtmutil (9) | `validate-app-configuration`, `workflow status`, `workflow retrieve collection`, `workflow retrieve analysis`, `workflow retrieve alert`, `workflow history`, `bundle-transmission-status`, `bundle-transmission-date-range`, `retrieve-bundle-id` |
| instrumentation (5) | `retrieve`, `client-retrieve`, `retrieve-requests`, `get-commodity`, `metadata` |
| analytics (8) | `retrieve-analysis`, `retrieve-alert`, `retrieve-alerts`, `retrieve-client-alerts`, `retrieve-custom`, `retrieve-alert-subscriptions`, `retrieve-temporary-enabling-requests`, `metadata` |
| transmission (1) | `transmission-status` |
| platinum (1) | `transmission-status` |

**REQUIRES CONFIRMATION (41)** — everything else, in three categories used to word the preview:

- **`egress`** — sends data off this machine: `collect-transmit`, `retrieve-transmit`,
  `periodic-transmit`, `file-upload`, `platinum-event`, `platinum-upload`, `platinum-heartbeat`,
  `platinum-ping`, `emit-custom-software-telemetry-event`, `invoke-emergency`.
- **`state`** — changes DTP/system configuration: `configure-orchestrator`,
  `apply-app-configuration`, `clear-app-configuration`, `configure-proxy`, `reset-proxy` (×2 utils),
  `enable-datatype`, `reset-datatype-state`, `set-commodity`, `temporary-enable`, `register-alert`,
  `create-alert-subscriptions`, `unregister` (×3 utils).
- **`action`** — triggers work, or does not terminate: `collect`, `periodic-collect`, `subscribe` (×2),
  `subscribe-commodity`, `custom-analysis`, `daily-analysis`, `weekly-analysis`, `default-alert`,
  `custom-alert`, `listen-alert-subscriptions`, `workflow start`, `workflow cancel`, `cancel`.

`retrieve-file` is deliberately **not** safe: it is a retrieval, but it writes to a caller-chosen path
from an **elevated** process, and the caller here is a language model.

## Execution and output

- Utils output **YAML by default, JSON with `--json`** (via `DtpUtilHelper`). The runner sets
  `DTPUTIL_JSON_OUTPUT=true` and passes `--json` for the four utils that share `DtpUtilHelper`.
- **`DTMPlatinumUtil` does not share `DtpUtilHelper`** and has no `--json`; its output is parsed on a
  best-effort basis.
- Parse order: `json.loads` → `yaml.safe_load` → raw text. **A parse failure never turns a successful
  command into a reported failure** — `stdout_raw` is always returned.
- Every execution returns:
  ```json
  {"ok": true, "exit_code": 0, "command_line": "...", "parsed": {...}, "stdout_raw": "...",
   "stderr": "...", "duration_seconds": 1.4, "format": "json|yaml|text"}
  ```
- Timeout: `timeout_seconds` (default 120), overridable per `util:command`. On timeout the process is
  killed and the **partial output collected so far is returned** with `timed_out: true`.

New dependency: **`pyyaml`** (for the YAML output path). No other MCP uses it; it goes in this MCP's
`requirements.txt`, which `setup_mcp_servers.ps1` now reads dynamically.

## Error handling

All errors are structured, actionable, and named — never a stack trace:

| Condition | Response |
|---|---|
| Not elevated | `{"error": "...", "is_admin": false}` (same shape as the twelve) |
| Dell TechHub not running | Detected and named explicitly — the top cause of confusing util failures |
| Exe missing | Names the **config key** and the resolved path |
| Datatype not found | Suggests near-matches from the CSV tables |
| Timeout | Partial output + `timed_out: true` |
| Bad command string | Rejected by the `^[a-z0-9][a-z0-9 -]*$` validator |

## File structure

```
mcp/dtm_sdk/
  dtm_sdk_mcp_server.py    # FastMCP; tool definitions only, thin
  config.py                # load, ${} expansion, env override, auto-probe, validation
  policy.py                # classification + confirm-token issue/verify (pure, no I/O)
  runner.py                # subprocess exec, timeout, output parsing
  datatypes.py             # CSV load + case-insensitive lookup + near-miss suggestions
  howto.py                 # section extraction from Sample_Utilities_HowTo.md
  config.json              # THE deployable config
  requirements.txt         # mcp, pyyaml, pytest
  start_dtm_sdk_mcp.ps1
  install_task.ps1 / uninstall_task.ps1
  conftest.py
  README.md / DESIGN.md
  tests/
```

Each module has one job and is testable without a real util. `policy.py` in particular is pure logic —
the security-critical part is the easiest part to test exhaustively.

## Testing

### Always-on (no admin, no DTP, no real util)

Run in the normal suite:

- **`policy`** — classification of all 65 commands; token issue/verify; **token bound to argv** (a
  token for command A is refused for command B); expiry; single-use; `blocked` hard-deny; unknown
  command → requires confirmation.
- **`config`** — `${}` expansion, env override precedence, missing-path reporting, `default_client_id`
  passthrough + env override.
- **`datatypes`** — case-insensitive resolve, GUID lookup, search, near-miss suggestion.
- **`runner`** — against a **fake exe** (a Python script that echoes its argv and exits with a chosen
  code): argv construction, `--json` injection, env injection, exit codes, timeout + partial output,
  json/yaml/text parse fallback.
- **`server`** — smoke: tools registered, health returns a structured payload.

### Phase 1 live tests — `instrumentation` + `analytics` only

Executed against the **real utils** to prove the plumbing end-to-end (exe resolves, elevation works,
Dell TechHub reachable, real YAML/JSON output parses).

Gated: skipped unless elevated **and** Dell TechHub is running **and** `DTM_SDK_LIVE_TESTS=1`. Skips
state the reason rather than passing vacuously.

Scope — **safe (read-only) commands and local-only actions**:

- instrumentation: `metadata`, `get-commodity`, `retrieve`, `retrieve-requests`, plus `collect`
  exercised **through the full confirmation flow** (local collection, no egress) so the confirm path is
  proven against a real util, not only the fake one.
- analytics: `metadata`, `retrieve-analysis`, `retrieve-alerts`, `retrieve-alert-subscriptions`, plus
  `custom-analysis` / `daily-analysis` through the confirmation flow (local computation, no egress).

**Excluded from automated tests — even for instrumentation/analytics:**
`emit-custom-software-telemetry-event` (egress), `unregister` (destructive), `enable-datatype`,
`reset-datatype-state`, `set-commodity`, `temporary-enable`, `register-alert`,
`create-alert-subscriptions` (mutate DTP config), `subscribe`, `subscribe-commodity`,
`listen-alert-subscriptions`, `periodic-collect` (do not terminate).

> These are excluded because an automated test must not transmit telemetry, unregister the
> application, or permanently change DTP configuration on the user's machine. This narrows "test
> everything" to "test everything that is safe to run repeatedly and unattended" — flagged explicitly
> for the user to override if they disagree.

### Phase 2 — deferred to a TODO

`dtmutil`, `transmission`, `platinum` get **no live tests in this phase**; they are covered by the
always-on fake-exe tests only. Recorded as a TODO.

When phase 2 runs: test those three utils live, **excluding every upload API**
(`file-upload`, `platinum-upload`) and, by the same reasoning as above, the other egress and
destructive commands.

## Security summary

- Elevated process; commands chosen by a language model. Hence: argv-list execution (no shell),
  command-string validation, an allowlist rather than a denylist, argv-bound single-use confirm tokens,
  and `retrieve-file` treated as dangerous because it writes as Administrator.
- Like the other loopback MCPs, `dtmsdk` binds `127.0.0.1` with **no authentication** — any local
  process can reach it. For the diagnostic twelve that means read-only information disclosure; for
  `dtmsdk` it means a local process could drive DTP. This is the same open item already tracked in
  `docs/HARDENING_BACKLOG.md` (MCP authentication), and `dtmsdk` raises its severity.

## Open items

1. The phase-1 live-test exclusion list above — confirm it matches the intent of "test everything else".
2. MCP authentication (backlog item) is now more pressing, because `dtmsdk` is not read-only.
