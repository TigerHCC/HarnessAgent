# Scheduler Module Design

## Goal

Add a standalone scheduler that autonomously triggers `goose run` agent tasks on a cron or one-shot
schedule, and let goose_web create, view, and control those schedules from its browser UI. The scheduler
fires independently of goose_web: schedules run whenever the machine is logged on, even with the web UI
closed.

## Scope

- New MCP module `scheduler` on `127.0.0.1:8793` — the 17th MCP, installed and supervised exactly like the
  existing 16 (AtLogOn Scheduled Task via the shared hidden launcher, MCP-Watchdog respawn, batch health
  test, goose `config.yaml` extension).
- Runs UNELEVATED (`run_level: Limited`): it only drives `goose` and writes its own store; no elevation is
  required.
- Schedule expression: standard 5-field cron (minute hour day-of-month month day-of-week) plus one-shot
  `at` (absolute local datetime, runs once, then auto-disables).
- Each schedule fires a headless `goose run -n <session> --max-turns N -i -` with a stored prompt on STDIN,
  a per-schedule `GOOSE_MODE`, and `cwd = workspace` — the same mechanism goose_web already uses for chat.
- goose_web gains an `/api/schedules` control surface and a two-tier UI (sidebar summary + in-app
  Schedules view). It reaches the scheduler by MCP `tools/call` over the existing `Invoke-McpHttp`
  streamable-http handshake — no second REST surface on the scheduler.
- The agent can also create/list/cancel schedules from chat through the same MCP tools.

Out of scope: scheduling arbitrary shell commands (only goose agent tasks), scheduling DTM/deploy actions
specifically, distributed/multi-machine scheduling, calendar/RRULE semantics beyond cron+at.

## Architecture

### Module layout (`mcp/scheduler/`)

Following the repo's small-unit convention (as in `dtm_download`):

- `cron.py` — pure functions: parse a 5-field cron or an `at` datetime and compute the next run time from a
  given "now". No I/O, no side effects; fully unit-testable. Also exposes a `describe()` helper that turns a
  schedule into the human label the UI shows ("每日 09:00", "每小時", "一次性").
- `store.py` — persistence for `schedules.json` (job definitions) and the `runs/` history tree
  (`runs/<id>/<timestamp>.log` plus a rolling per-job status). All writes go through a single lock; reads
  return plain dicts. Provides CRUD, `due(now)`, and `record_run(...)`.
- `server.py` (`scheduler_mcp_server.py`) — the FastMCP tool surface and the background trigger loop, in one
  process. Entry point `mcp.run(transport="streamable-http")`, matching the other modules.
- `config.py` / `config.json` — resolved config (workspace path, `max_turns`, tick interval, catch-up
  policy, overlap policy) with env overrides, matching the `dtm_download` config pattern.
- `start_scheduler_mcp.ps1`, `install_task.ps1`, `uninstall_task.ps1`, `requirements.txt`, `README.md`,
  `conftest.py`, `tests/`.

### The two faces of the scheduler

1. **MCP tool face** (shared by the chat agent and by goose_web's control panel):
   `sched_create`, `sched_list`, `sched_get`, `sched_update`, `sched_delete`, `sched_pause`,
   `sched_resume`, `sched_run_now`, `sched_history`, `scheduler_health`.
2. **Background trigger loop** (an asyncio task started alongside the FastMCP server): every tick it asks
   `store.due(now)` for schedules whose `next_run <= now`, launches each via
   `asyncio.create_subprocess_exec` so the event loop is never blocked, and on completion calls
   `store.record_run(...)` (exit code, captured output → `runs/<id>/<ts>.log`, `last_run`, `last_status`)
   and recomputes `next_run` (or auto-disables a fired `at` job).

### goose_web integration

- `server.ps1` gains `/api/schedules` routes: `GET` (list, backed by `sched_list`) and `POST` actions
  (`create`, `update`, `delete`, `toggle`, `run-now`, `history`). Each route builds the corresponding MCP
  `tools/call` request and sends it with the existing `Invoke-McpHttp` (initialize → tools/call), then
  shapes the result into the JSON the UI expects. The scheduler's `uri` is discovered from the same parsed
  `config.yaml` extensions block goose_web already reads, so there is no new hardcoded endpoint.
- `index.html` gains:
  - a **sidebar summary** section (mirroring the existing `sessions` block): each planned schedule as a row
    with a status dot (filled = active, hollow = paused), name, and next-run label; a count badge in the
    heading. This directly satisfies "show the currently-planned schedules".
  - an **in-app Schedules view**: a header toggle (Chat / 排程) swaps the transcript area for a table
    — name · cadence (cron/at label) · next run · last status · enable toggle · row actions (run-now, edit,
    delete, history). No separate browser page: the SPA shell, header, token handling, and `/api/health`
    polling are all reused.
  - a **create/edit drawer**: cadence builder (common presets + raw cron field + `at` datetime picker),
    session name, prompt, and `GOOSE_MODE` selector.
  - a **history drawer**: recent runs for a job (time · result · expandable log).

### Registration and install

- `config/mcp_servers.json` gets a 17th entry: `{name:"scheduler", directory:"scheduler", port:8793,
  task:"Scheduler-MCP", run_level:"Limited", health_tool:"scheduler_health", description:"..."}`.
- `setup_mcp_servers.ps1` is data-driven: extend the expected-port range to `8777..8793`, update the count
  banner to 17, and the union-requirements install + per-entry task registration + goose `config.yaml`
  extension all pick the new module up with no structural change.

## Data Model

A schedule stored in `schedules.json`:

- `id` (opaque string), `name` (display), `enabled` (bool).
- `kind`: `"cron"` or `"at"`.
- `cron` (5-field string, when `kind="cron"`) or `at` (ISO local datetime, when `kind="at"`).
- `session`, `prompt`, `mode` (`"auto"` | `"chat"`), `max_turns` (optional override).
- Bookkeeping: `next_run`, `last_run`, `last_status` (`ok` | `error` | `running` | `null`), `created`.

Per-run history: `runs/<id>/<timestamp>.log` holds the captured agent output; the store keeps the last N
run summaries (time, exit code, status, log path) per job for the history drawer.

## Security

- Mutating MCP tools (`sched_create`, `sched_update`, `sched_delete`, `sched_pause`, `sched_resume`,
  `sched_run_now`) are **confirm-token gated** using the same argv-bound, single-use preview→confirm pattern
  as `dtm_sdk`/`dtm_deploy`. This protects the **agent chat path**: a prompt-injected agent cannot silently
  schedule or delete a job. Read tools (`sched_list`, `sched_get`, `sched_history`, `scheduler_health`) run
  directly.
- **goose_web's control panel is a human clicking a button**, which is itself the confirmation. So the
  `/api/schedules` routes perform the two-step automatically: call the tool, and if it returns
  `requires_confirmation`, immediately re-call with the returned `confirm_token`. The gate still stands for
  the agent path; the UI path is not weakened because a UI action is an explicit human act.
- A schedule with `mode="auto"` runs tools unattended (e.g. 3am, no human watching). This is the highest
  risk in the design. Mitigations: `mode` is explicit per schedule (no silent default to `auto` — the
  create drawer requires choosing it); `auto` schedules are visibly flagged in both UI tiers; and creating
  one goes through the confirm gate.
- The module runs UNELEVATED. It never handles secrets; the Artifactory token and DTP elevation belong to
  other modules and are untouched here.

## Trigger Policy

- **Overlap**: if a job's previous run is still in flight when it comes due again, **skip** the new firing
  (no stacking of the same job). Recorded as a skipped tick, not an error.
- **Missed cron while asleep**: do **not** catch up — on wake, the loop recomputes `next_run` forward from
  now, so a machine that was off does not fire a burst of overdue jobs. (A per-job catch-up flag is left as
  a future knob, defaulted off.)
- **Missed one-shot `at`**: if an `at` time passed while the machine was down, run it **once** on next
  startup, then auto-disable — a one-shot should not be silently lost.
- **Tick interval**: a small fixed interval (e.g. 30s) from config; cron resolution is one minute, so a
  sub-minute tick guarantees each minute boundary is observed without busy-waiting.

## Error Handling

- A schedule whose `goose run` exits non-zero is recorded `last_status="error"` with stderr captured to its
  run log; the loop continues and reschedules the next occurrence. One failing job never stalls the loop.
- Invalid cron / `at` input is rejected at `sched_create`/`sched_update` time (via `cron.py` parse) with a
  clear error, before anything is stored.
- A subprocess launch failure (goose binary missing, workspace gone) is recorded as an error run and
  surfaced by `scheduler_health`.
- goose_web `/api/schedules` routes return the scheduler's error payloads verbatim so the UI can show the
  real cause; an unreachable scheduler yields a clear "scheduler offline" state rather than a hang (bounded
  `Invoke-McpHttp` timeout).

## Tests

- `cron.py`: next-run math across cron fields (wildcards, ranges, steps, day-of-week vs day-of-month),
  `at` parsing, and `describe()` labels — pure unit tests.
- `store.py`: CRUD round-trip, `due(now)` selection, overlap skip, `at` auto-disable, run-history rolling
  retention, concurrent-write safety.
- `server.py`: confirm-token gating on each mutating tool (preview then confirm), read tools run directly,
  and the trigger loop fires a fake/stub goose command and records the run (goose invocation stubbed so the
  test does not spawn a real agent).
- The batch `test_mcp_servers.ps1` picks the module up automatically and exercises `scheduler_health`.
- goose_web: a focused test that `/api/schedules` maps UI actions to the right `tools/call` and performs the
  auto-confirm two-step.

## Documentation

- `mcp/scheduler/README.md`: tools, schedule model, cron+at syntax, trigger/overlap/catch-up policy, the
  `mode="auto"` unattended-tools warning, and how goose_web controls it.
- Update `mcp/README.md` and the suite docs to list the 17th MCP and the new port `8793`.
- Note the goose_web `/api/schedules` surface and the Schedules UI in `goose_web/README.md`.
