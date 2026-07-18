# DTM Download/Deploy Observability Design

## Goal

Make the long-running dtm_download and dtm_deploy operations observable: per-file and per-chunk download
progress in the process log plus a per-build on-disk record, an MSI log tail in every install/uninstall
result, and a verified answer to whether MCP progress notifications can reach the goose_web UI.

## Scope

- dtm_download: progress lines from `download_build`/`download_file` to stdout (the hidden launcher
  already routes stdout to `logs/mcp/dtm_download.stdout.log` with 10 MiB rotation) and to a per-build
  `download.log` inside the build's download folder.
- dtm_deploy: `install_msi`/`uninstall_product` results gain a `log_tail` field — the last N lines of the
  msiexec verbose log.
- A throwaway experiment (not committed to the repo) verifying whether `ctx.report_progress` from a
  FastMCP tool is rendered to stdout by `goose run`, with findings written to a dated experiment note.
  Wiring `report_progress` into the dtm modules is explicitly OUT of scope this round regardless of the
  outcome; if feasible, a follow-up todo is opened instead.

Style constraint: both modules use plain print/return-dict conventions; no `logging` module is
introduced (considered and rejected for consistency).

## Design

### dtm_download progress (artifactory.py)

- `download_file(base_url, repo_path_file, token, out_file, timeout=600, label="", log=None)` gains two
  optional parameters. In the chunk loop it emits a progress line every 25 MB:
  `[dl] <label> 75MB/312MB (24%)` — total size from the response `Content-Length` header; when absent,
  only the cumulative MB is shown. Emission goes through the shared logger helper.
- `download_build` emits: a per-file start line `[dl] (2/3) <name> ...` before each zip/CSV/HTML
  download, and a completion line after checksum-verify/extract (zip: includes file_count). It opens
  `<download_path>/<build_id>/download.log` once and passes the logger down.
- Logger helper `_log(fh, msg)`: prints `msg` to stdout AND appends it to the open file handle. Any
  file-write failure must not abort the download: swallow the exception and print a single warning.
- Existing callers without `label`/`log` behave exactly as before (defaults no-op the additions).

### dtm_deploy MSI log tail (msi.py)

- New pure function `tail_log(log_file, n=40) -> list[str]`: returns the last `n` lines of the msiexec
  log. msiexec `/l*v` writes UTF-16LE: open with `utf-16` first, fall back to `utf-8` with
  `errors="replace"`. An unreadable/missing file returns `["<unreadable: ...>"]` instead of raising.
- `install_msi` and `uninstall_product` result dicts gain `log_tail` (always present — on failure it is
  the primary diagnostic surfaced to the agent/UI without opening the file).

### report_progress feasibility experiment

- A throwaway mini-MCP in the scratchpad (never committed): one tool that sleeps ~10 s and calls
  `ctx.report_progress(i, 10)` each second.
- Drive it via `goose run` and observe whether progress notifications are rendered to stdout (which is
  what goose_web parses); optionally confirm through the goose_web event stream.
- Findings go to `docs/superpowers/specs/2026-07-18-report-progress-experiment.md`: feasible → open a
  follow-up wiring todo; not feasible → close the question with evidence.

## Error Handling

- Progress emission is best-effort: no progress failure (file write, encoding) may fail a download that
  would otherwise succeed.
- `tail_log` never raises; it degrades to a placeholder entry.

## Tests

- artifactory: extend the existing fake-response tests — progress lines appear on stdout (capsys) and in
  `download.log`; `Content-Length` absent → cumulative-only format; log-write failure does not abort.
- msi: `tail_log` on a synthetic UTF-16LE file (tail correctness + encoding), UTF-8 fallback, missing
  file placeholder; `install_msi`/`uninstall_product` include `log_tail` (msiexec stubbed).
- Experiment is manual; its deliverable is the findings note, not a test.

## Documentation

- `mcp/dtm_download/README.md`: note the progress lines and the per-build `download.log`.
- `mcp/dtm_deploy/README.md`: note the `log_tail` field.
