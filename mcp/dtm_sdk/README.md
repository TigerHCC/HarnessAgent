# DTM Sample/SDK Util MCP (`dtmsdk`)

A local, elevated MCP server that wraps the **five DTP sample utilities** (65 commands total) plus the
three datatype tables and the HowTo, so an agent can drive the Dell Telemetry Platform client SDK CLIs
through one gated interface. Binds **`127.0.0.1:8789`**, transport `streamable-http`, endpoint `/mcp`.

The extension id is **`dtmsdk`** — deliberately *not* `dtm`. `dtm` is the GB10 DTM Knowledge Agent (a
read-only telemetry/triage RAG); this server is a different thing entirely: it *runs the real DTP
utilities*. Keeping the ids distinct keeps the two from being confused in Goose config or in prompts.

---

## ⚠️ WARNING — this MCP is NOT read-only

Unlike the **twelve `windows_*` diagnostic MCPs** (8777–8788), which only *read* system state, `dtmsdk`
can **transmit telemetry to Dell** and **mutate DTP / system configuration**. Some commands send data off
this machine (egress); others change orchestrator/proxy/app configuration, enable datatypes, or unregister
the application. Treat it as a state-changing, potentially-egressing tool, not a diagnostic reader.

The safety model: every command is checked against its util's **safe allowlist**. Only the **24 safe
(read-only/query) commands** run directly. The other **41 commands run only after you pass back a
per-command confirmation token** that is bound to the exact util + command + argv, is **single-use**, and
**expires after 120 seconds**. There is no way to run a gated command without first seeing a preview of
the exact command line and explicitly confirming it.

> The server binds loopback with **no authentication**. Any local process can reach it, so on this box the
> confirmation gate is the only thing standing between a caller and a real telemetry transmission or a DTP
> config change. Do not run it on a machine with untrusted local users.

---

## The 9 tools

**Lookup (read-only, no util is ever executed):**

| Tool | What it does |
|---|---|
| `dtm_datatypes(kind, search="", commodity="", max=50)` | Search a datatype table (`kind` = `instrumentation` \| `analysis` \| `alert`) by Name substring and/or CommodityType. |
| `dtm_datatype(name)` | One datatype in full (Name, GUID, dependencies), matched case-insensitively across all three tables; returns near-match suggestions on a miss. |
| `dtm_help(util, command="")` | Returns the HowTo section for a util, or the block for one command — **use this to learn a command's real options.** |
| `dtm_health()` | Server + environment health: admin state, Dell TechHub service state, every resolved exe/table/HowTo path and whether it exists. **Check this first when a run fails.** |

**Execution (one per util — safe commands run directly, everything else is gated):**

| Tool | Wraps | Notes |
|---|---|---|
| `dtm_run_dtmutil(command, args=[], confirm_token="")` | `DTMUtil` (IDtmClientSdk) | orchestrator config, workflows, bundle transmission |
| `dtm_run_instrumentation(command, args=[], confirm_token="")` | `DtpInstrumentationUtil` | data collection/retrieval, commodities, datatype state |
| `dtm_run_analytics(command, args=[], confirm_token="")` | `DtpAnalyticsUtil` | analysis, alerts, subscriptions, retrieval |
| `dtm_run_transmission(command, args=[], confirm_token="")` | `DtpTransmissionUtil` | collect+transmit, retrieve+transmit, file upload — almost everything here **transmits to Dell** |
| `dtm_run_platinum(command, args=[], confirm_token="")` | `DTMPlatinumUtil` | Platinum event logging, upload, heartbeat/ping — most commands **contact Dell** |

---

## The confirmation flow (worked example)

Gated commands take two calls: a **preview** call (no token) that returns a token, then an **execute**
call that passes the token back.

**Step 1 — preview.** Call the run tool with an empty `confirm_token`:

```
dtm_run_transmission(command="collect-transmit", args=["--datatype-name", "X"])
```

Because `collect-transmit` is an **egress** command (not on the safe allowlist), nothing runs. Instead you
get a preview:

```json
{
  "requires_confirmation": true,
  "confirm_token": "a1b2c3d4e5f60718",
  "command_line": "...DtpTransmissionUtil.exe --json collect-transmit --datatype-name X",
  "category": "egress",
  "reason": "transmits data from this machine to Dell",
  "expires_in_seconds": 120
}
```

Inspect `command_line` and `reason`. If it is what you intend, proceed.

**Step 2 — execute.** Call the *same* tool with the *same* command and args, passing the token back:

```
dtm_run_transmission(command="collect-transmit", args=["--datatype-name", "X"],
                     confirm_token="a1b2c3d4e5f60718")
```

Now the util actually runs and you get the result (`ok`, `exit_code`, `parsed`, `stdout_raw`, …).

**Token rules:**

- **Bound to exact util + command + argv.** A token issued for `collect-transmit --datatype-name X` will
  not execute `--datatype-name Y`, and will not execute `cancel`. Change any of them and you get a fresh
  preview instead.
- **Single-use.** The token is consumed on the successful execute call; reusing it re-issues a preview.
- **Expires after 120 seconds.** After that the token is dead and you must preview again.

Safe commands skip all of this — e.g. `dtm_run_instrumentation(command="metadata")` runs immediately.

---

## Configuration

All paths live in **`config.json`**. Every value is resolved in three steps:

1. **`${}` expansion** — `${var}` is substituted from sibling top-level keys plus a built-in `${repo_root}`
   (the HarnessAgent repo root). Expansion iterates, so `${docs_root}` (which itself contains
   `${repo_root}`) fully resolves.
2. **Environment override** — an env var named `DTM_SDK_<UPPER_SNAKE>` overrides the corresponding key.
   For example `DTM_SDK_SAMPLES_ROOT` overrides `samples_root`, `DTM_SDK_TIMEOUT_SECONDS` overrides
   `timeout_seconds`, and `DTM_SDK_EXECUTABLES_DTMUTIL` overrides one resolved exe path.
3. **As-is** — whatever remains is used verbatim.

The loader records a `_resolved` map (`raw`, `resolved`, `exists`, `source`) for every exe / table / HowTo
so `dtm_health()` can point at the exact failing key instead of surfacing a bare `FileNotFoundError`.

**Redeploying to a new machine or a new Samples build is a one-liner: change `samples_root`.** Every exe
path is expressed relative to it (`${samples_root}/…`), so pointing `samples_root` at the new
`DTPSamples-x.y.z/Samples` folder repoints all five utilities at once. (`app_id` and `app_name` must be
set together or both left null.)

---

## Prerequisites

- **Administrator.** The DTP sample utilities require elevation; the server (and its scheduled task) run
  elevated. Run tools return `{"error": "not elevated; the DTP utils require Administrator",
  "is_admin": false}` if launched unelevated.
- **Dell TechHub service running.** Commands talk to the local DTP orchestrator via the TechHub service.
- **Call `dtm_health()` first.** It reports admin state, the Dell TechHub service state
  (`running` / `stopped` / `absent` / `unknown`), and whether each resolved exe / table / HowTo path
  exists — the fastest way to diagnose a failed run.

---

## Install / uninstall

Persist it as a logon Scheduled Task (`DtmSdk-MCP`, `RunLevel Highest`, at logon, elevated) — run from an
elevated shell:

```powershell
cd mcp\dtm_sdk
.\install_task.ps1        # register + describe how to start it
Start-ScheduledTask -TaskName DtmSdk-MCP
.\uninstall_task.ps1      # remove the task
```

Or use the repo-wide one-click installer, which also installs Python deps and registers the extension:

```powershell
powershell -ExecutionPolicy Bypass -File .\..\..\setup_mcp_servers.ps1   # Administrator
```

To run it standalone in the foreground (elevated):

```powershell
.\start_dtm_sdk_mcp.ps1
```

Goose connects over `streamable_http` at `http://127.0.0.1:8789/mcp`.

---

## Learning a command's options

This server does **not** hard-code the 65 command signatures. To see a command's real flags, call
**`dtm_help(util, command)`** — it extracts that command's block straight from
`Sample_Utilities_HowTo.md` (falling back to the whole util section if the exact block is not found). For
example `dtm_help("transmission", "collect-transmit")` returns the documented options for that command.

See [`DESIGN.md`](DESIGN.md) for the module map, the classification rationale, and the full safe/gated
split; see [`TODO_PHASE2.md`](TODO_PHASE2.md) for what live-testing is deferred.
