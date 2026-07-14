# MCP Batch Test and Documentation Audit Design

**Date:** 2026-07-14

## Objective

Add a one-command, non-destructive integration test for all 14 local MCP servers. The
test must continue after individual failures, produce machine-readable and human-readable
reports, and identify each successful or failed server and stage. At the same time, audit
all repository Markdown and update documents whose current operational claims are stale or
which need links to the new test workflow and architecture reference.

## Scope

The batch test covers the local servers on `127.0.0.1:8777-8790`:

- Twelve read-only Windows diagnostic MCPs: `srum`, `eventlog`, `crash`, `exec`,
  `drift`, `netconn`, `perfmon`, `disk`, `procinspect`, `memstate`, `filterstack`,
  and `winupdate`.
- The confirmation-gated `dtmsdk` MCP.
- The confirmation-gated `obsidian` MCP.

For safety, the test does not invoke arbitrary tools. It performs MCP initialization,
tool discovery, and exactly one designated read-only health tool per server. It does not
create confirmation tokens, write notes, transmit telemetry, modify DTP configuration,
save baselines, or invoke diagnostics that require caller-supplied targets.

## Architecture

### Shared server manifest

Create `config/mcp_servers.json` as the source of truth for local MCP metadata. Each entry
contains:

- `name`: Goose extension and report identifier.
- `directory`: directory below `mcp/`.
- `port`: loopback MCP port.
- `task`: Windows Scheduled Task name.
- `run_level`: `Highest` or `Limited`.
- `description`: text used when adding the Goose extension.
- `health_tool`: the read-only MCP tool used by the batch test.

`setup_mcp_servers.ps1` loads this manifest instead of declaring `$MCPS` inline. It keeps
its current setup/uninstall behavior, parameter surface, descriptions, and task privilege
behavior. Loading validates required fields, unique names and ports, valid run levels, and
the expected 14 entries before any installation action.

### Entry point and test engine

`test_mcp_servers.ps1` is the user-facing Windows entry point. It finds Python 3, invokes
`scripts/test_mcp_servers.py`, passes through timeout/output options, prints the final
summary and report paths, and returns the engine's exit code.

The Python engine uses only the standard library. It loads the shared manifest and tests
servers sequentially so results and timing remain easy to interpret. The engine supports
an explicit manifest path and output directory for automated tests.

### MCP protocol sequence

For each manifest entry, the engine sends these requests to
`http://127.0.0.1:<port>/mcp`:

1. `initialize`, accepting either `application/json` or SSE-framed JSON.
2. Capture and reuse `Mcp-Session-Id` when the server returns one.
3. `notifications/initialized`.
4. `tools/list` and verify that the configured `health_tool` is present.
5. `tools/call` with `{ "name": health_tool, "arguments": {} }`.

Every request has a bounded timeout. A failure is assigned to one of `connect`,
`initialize`, `tools_list`, or `health_call`. The engine records the error and proceeds to
the next server. A health tool's domain payload may report degraded or unavailable data;
the protocol test still passes when the tool call itself returns a valid MCP result without
`isError: true`. This distinction prevents expected privilege or optional-software gaps
from being mistaken for a broken MCP transport.

## Report Contract

Each run writes timestamped files under `reports/mcp/` by default:

- `mcp-test-YYYYMMDD-HHMMSS.json`
- `mcp-test-YYYYMMDD-HHMMSS.md`

The JSON document contains:

- Schema version and UTC start/end timestamps.
- Host and Python runtime information.
- Summary counts: total, passed, failed, and duration.
- One result per manifest entry with name, port, endpoint, status, failed stage,
  duration, discovered tool names/count, health tool, normalized health response, and
  error details.

The Markdown report renders the same summary as a table followed by failure details and
per-server health output. Potential secrets are not expected in this loopback workflow;
nevertheless, raw HTTP headers are not written to reports.

Exit code is `0` only when all 14 servers pass. Any server failure, malformed manifest, or
report-write failure returns nonzero. Report generation is attempted even when one or more
servers fail.

## Error Handling

- A server that is not listening is reported as a `connect` failure.
- Invalid HTTP, JSON, SSE, or JSON-RPC responses identify the relevant protocol stage.
- A missing configured health tool is a `tools_list` failure.
- JSON-RPC errors, `isError: true`, and malformed tool results are `health_call` failures.
- Failure of one server never suppresses results for later servers.
- Manifest validation fails before network calls and reports actionable field-level errors.
- Existing reports are never overwritten because filenames include the run timestamp; a
  collision receives a numeric suffix.

## Test Strategy

Implementation follows red-green-refactor:

1. Start with Python tests using a local fake HTTP MCP server.
2. Verify JSON responses and SSE-framed responses.
3. Verify session ID propagation and the complete request sequence.
4. Verify connection failure, malformed initialization, missing health tool, and health
   tool error classification.
5. Verify that a failed server does not stop later servers.
6. Verify JSON/Markdown report contents and aggregate exit status.
7. Verify manifest consistency with all 14 `FastMCP` entry points and the PowerShell setup
   loader.
8. Run the existing repository tests and, when the local servers are available, run the
   new batch test against the live endpoints. Environmental live failures remain visible in
   the generated report and are not rewritten as test success.

## Markdown Audit

All 57 Markdown files currently tracked or present in the repository are reviewed. The
audit checks:

- MCP totals, names, ports, privilege statements, and read/write classifications.
- Setup, launch, test, rollback, and troubleshooting commands.
- References to the architecture document and the new batch test/report workflow.
- Relative Markdown links and references to renamed or missing repository files.
- Claims made obsolete by the DTM SDK, Obsidian, watchdog, or 14-server expansion.

Current operational documents are corrected where needed. Historical design specs,
implementation plans, spike notes, and recorded install results retain their historical
context; they receive a clearly labeled current-status pointer only when readers could
otherwise mistake them for current instructions. Files with no inaccurate or missing
information remain unchanged. A documentation audit summary records which files were
changed and which categories were checked.

## Documentation Changes

At minimum, current entry-point documents will describe the test command, safety boundary,
reports, and exit behavior:

- `README.md`
- `RUN.md`
- `mcp/README.md`
- `docs/SETUP_GUIDE.md`
- `docs/MODULE_RELATIONSHIPS.md`

Individual MCP READMEs link back to the batch-test section where useful instead of
duplicating the full procedure. Additional Markdown is changed only when the audit finds a
specific stale claim or broken reference.

## Non-Goals

- Executing every MCP tool or synthesizing arguments for tools with side effects.
- Automatically starting, restarting, installing, or repairing failed MCP servers.
- Testing remote DTM/PK proxies or model-provider quality.
- Replacing each MCP's pytest suite.
- Adding a web UI for historical reports.
