# Scheduler MCP (`scheduler`)

A local, **UNELEVATED** MCP server that fires headless `goose run` agent tasks on cron and one-shot schedules, independently of goose_web. Binds **`127.0.0.1:8793`**, transport `streamable-http`, endpoint `/mcp`.

Mutating tools (`sched_create`, `sched_update`, `sched_delete`, `sched_pause`, `sched_resume`, `sched_run_now`) are confirm-token gated to protect the chat-agent path; goose_web auto-confirms via UI button clicks. A background Ticker thread spawns `goose run` for due jobs. Runs UNELEVATED under the same `RunLevel Limited` scheduled-task pattern as `dtm_download`.

---

## Tools

| Tool | Gated? | What it does |
|---|---|---|
| `sched_list()` | No | List all schedules with their cadence label, next run, and last status. Read-only. |
| `sched_get(id)` | No | Get one schedule by id, with full details. Read-only. |
| `sched_history(id)` | No | Recent run history (time, exit code, status, log path) for a schedule. Read-only. |
| `scheduler_health()` | No | Server health: schedule count, enabled count, next upcoming run, store path, tick interval. Check first on issues. |
| `sched_create(name, kind, expr, session, prompt, mode, confirm_token)` | **Yes** | Create a schedule. `kind='cron'` with a 5-field expr, or `kind='at'` with an ISO datetime. `mode='auto'` runs tools unattended; `mode='chat'` is model-only. First call returns a `confirm_token`; pass it back to execute. |
| `sched_update(id, fields, confirm_token)` | **Yes** | Update a schedule's fields (name/kind/expr/session/prompt/mode/enabled). Confirm-gated. |
| `sched_delete(id, confirm_token)` | **Yes** | Delete a schedule. Confirm-gated. |
| `sched_pause(id, confirm_token)` | **Yes** | Pause (disable) a schedule so it will not fire. Confirm-gated. |
| `sched_resume(id, confirm_token)` | **Yes** | Resume (enable) a schedule so it will fire on its cadence. Confirm-gated. |
| `sched_run_now(id, confirm_token)` | **Yes** | Fire a schedule immediately, out of band. Confirm-gated. |

---

## Scheduling Model

### Cron Schedules (`kind='cron'`)

Five-field cron expressions (`minute hour day_of_month month day_of_week`), evaluated on a configurable tick interval (default 30 seconds). A schedule is **due** when its cron expression matches the current time and it has not already run this minute.

**Overlap policy (Trigger/Skip):** If a schedule is already running (from a previous tick), the Ticker skips this tick and tries again at the next interval. This prevents concurrent runs of the same session.

**Catch-up policy:** If the server was offline or the Ticker was delayed, a cron schedule will run once it becomes due again — no backfill of missed intervals.

### At Schedules (`kind='at'`)

One-shot execution at an ISO 8601 datetime (`YYYY-MM-DDTHH:MM:SS`). Once the schedule fires, it is marked as done and will not fire again (even if resumed).

---

## Configuration

`config.json`: `schedules_path`, `runs_dir`, `history_limit`, `tick_seconds`, `goose_bin`, `default_max_turns`, `workspace`. Every key can be overridden via `SCHEDULER_MCP_<KEY>` env vars (see `config.py`).

The Scheduler stores schedule metadata and run history in its own directory tree; it does not read any environment secrets and all tool outputs are audit-logged.

---

## Running

```powershell
# one-off, foreground (for testing)
.\start_scheduler_mcp.ps1

# persist across logons (elevated shell needed to REGISTER the task; the server itself runs unelevated)
.\install_task.ps1
.\uninstall_task.ps1   # remove
```

---

## Unattended Tools Warning

When `mode="auto"` is set on a schedule, `goose run` will execute tools automatically without prompting. Ensure the `prompt` field contains only safe, audited agent instructions. Use `mode="chat"` for sensitive or exploratory workflows where tool execution should require model-only reasoning.

---

## Control from goose_web

goose_web controls the Scheduler via MCP `tools/call` requests. When a user clicks "Schedule a task" or "Run now", goose_web:
1. Calls `sched_create` or `sched_run_now` with a preview token.
2. Receives a `requires_confirmation` response with a new `confirm_token`.
3. Displays the confirmation to the user in the UI.
4. On user acceptance, re-calls the tool with the `confirm_token` to execute.

This pattern ensures that human confirmation is always recorded and visible in the audit trail.

---

## Tests

```powershell
pip install -r requirements.txt
python -m pytest tests -q
```

Unit tests mock the `Ticker` and `goose run` spawning; no live agent runs in the test suite.
