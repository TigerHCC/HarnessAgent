# report_progress feasibility experiment

**Question:** when an MCP tool calls `ctx.report_progress(...)`, does `goose run`
render anything to stdout *during* the tool call (visible to goose_web's stdout
parser), or is it silent until the tool returns?

**Status:** verify-only experiment. No wiring changes made to `dtm_download` /
`dtm_deploy` / goose_web in this round.

## Environment

- goose version: `1.39.0` (`goose --version` → `1.39.0`)
- `goose run --with-extension` is supported directly (no need for the
  config.yaml-copy fallback described in the brief).
- Python 3.13.12, `mcp` and `anyio` available in the active environment
  (`C:\Users\a9027\anaconda3`).

## Setup

Mini stdio MCP server (throwaway, in the session scratchpad, not committed):
`slow_mcp.py` — one tool, `slow_task`, that calls
`await ctx.report_progress(i, 10)` once per second for 10 seconds, then
returns `"slow_task complete"`. Content matches the brief's Step 1 verbatim.

Sanity check before driving it through goose: the file parses
(`ast.parse`) and imports cleanly, and instantiates a `FastMCP("slowdemo")`
server object.

## Exact command

```powershell
goose run --with-extension "python C:\Users\a9027\AppData\Local\Temp\claude\C--Users-a9027-source-Agentic-HarnessAgent\820812f2-f9c7-4107-afda-458d29975932\scratchpad\slow_mcp.py" -t "Call the slow_task tool and tell me its result." --debug
```

(First attempt used a Git-Bash-style `/c/...` path inside the `--with-extension`
value; goose is a native Windows binary and Python failed to open the mangled
path, so the extension never started. Re-run with a plain Windows-style path
worked — noted here in case others hit the same trap.)

Full stdout+stderr was captured to
`scratchpad\goose_output2.log` (plain run) and `scratchpad\goose_output3_debug.log`
(same run with `--debug`, which shows full tool-result payloads instead of
truncating them).

## What appeared on stdout

Plain run (`goose_output2.log`), byte-for-byte between the tool-start marker
and the model's final answer:

```
  ────────────────────────────────────────
  ▸ slow_task python



The `slow_task` tool returned:

**slow_task complete**

This tool sleeps for approximately 10 seconds while reporting progress each second, then returns this result.
```

`--debug` run (`goose_output3_debug.log`) — same shape, plus the raw tool
result content dump appearing only once, after completion:

```
  ────────────────────────────────────────
  ▸ slow_task python

Annotated {
    raw: Text(
        RawTextContent {
            text: "slow_task complete",
            meta: None,
        },
    ),
    annotations: None,
}


The `slow_task` tool completed successfully. ...
```

A raw-byte inspection of `goose_output2.log` (`repr()` of the full file) confirmed
there are no carriage returns, ANSI escape codes, percent signs, or `n/10`
style tokens anywhere between the `▸ slow_task python` line and the final
result — i.e. nothing was written and then overwritten in place either.
`grep -iE "%|progress|[0-9]+/10|notification"` over the captured output matched
only the model's own prose description of the tool ("reporting progress each
second"), not any actual per-tick emission.

Timing: the full run (model turn + 10s of `anyio.sleep` inside the tool + final
answer generation) took ~30s end-to-end, confirming the tool actually executed
and slept for the full duration — the silence isn't because the tool short-circuited.

Also checked goose's own log files
(`%APPDATA%\Block\goose\data\logs\cli\2026-07-19\*.log`) for all runs performed
during this experiment: `grep -iE "progress|notif|report_progress"` returned
zero matches in every file. Progress notifications are not surfaced to stdout,
and goose does not appear to log them anywhere else on disk either — they are
swallowed entirely (silently dropped or never surfaced by the CLI's renderer),
not merely redirected to a different visible channel.

## Verdict

**NOT FEASIBLE.**

`goose run`'s stdout is silent from the moment a tool call starts
(`▸ slow_task python`) until the tool returns and the final result is
rendered. `ctx.report_progress()` ticks sent by the MCP server during a
long-running tool are not visible anywhere in goose's stdout output, and are
not visible in goose's own log files either. The goose_web pipeline, which
parses `goose run`'s stdout, has no way to observe MCP progress notifications.

## Recommendation

Question closed — do not pursue wiring `ctx.report_progress` into
`dtm_download` / `dtm_deploy`. Task 1's approach (the tool itself prints
progress lines to stdout via `print()`/`log.emit()`, which `goose run` passes
through as ordinary tool output) remains the only viable mechanism for
goose_web to render incremental progress. No follow-up work is needed on the
MCP progress-notification path unless a future goose release changes how
`report_progress` is rendered in the CLI.
