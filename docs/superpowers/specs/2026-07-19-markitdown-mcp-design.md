# MarkItDown MCP Integration Design

## Goal

Give the goose agent a document-to-Markdown conversion tool (PDF, Office, images, audio, HTML, CSV,
ZIP, YouTube, EPub) by mounting Microsoft's official `markitdown-mcp` server on `127.0.0.1:8794` as a
lightweight, manifest-external service.

## Decision Record

- **Official package, not a custom wrapper.** `pip install markitdown-mcp`; it exposes one tool,
  `convert_to_markdown(uri)` (http/https/file/data URIs), over streamable-http. No Docker — the
  official Dockerfile is an optional sandbox for other hosts; on this machine every MCP already runs
  as a bare loopback Python process.
- **Lightweight mount, OUTSIDE the canonical manifest.** The suite's manifest
  (`config/mcp_servers.json`), setup validation, batch test, and watchdog all require a `health_tool`,
  which the official server does not have. Rather than modify four suite files to support
  health-tool-less servers, markitdown stays out of the manifest. Accepted cost: no watchdog respawn
  and no batch-test coverage — a dead server restarts at next logon only.
- goose_web needs no changes: its sidebar discovers extensions live from goose's `config.yaml` and
  handshakes them, so the markitdown card, tool list, and toggle appear automatically.

## Scope

New files only, all under `mcp/markitdown/`; zero changes to suite files (manifest, setup, batch
test, watchdog) and zero changes to goose_web.

- `requirements.txt` — `markitdown-mcp` (pulls `markitdown` and its converter deps).
- `run_markitdown_mcp.py` — a ~5-line shim: set
  `sys.argv = ["markitdown-mcp", "--http", "--host", "127.0.0.1", "--port", "8794"]`, then call the
  official entry point's `main()`. Exists because the shared hidden launcher
  (`scripts/start_mcp_hidden.ps1`) requires a resolvable `.py` `ServerPath`. The exact import path of
  the official `main` is verified at implementation time (`markitdown_mcp.__main__` or equivalent).
- `install_task.ps1` / `uninstall_task.ps1` — mirror `mcp/dtm_download`'s: Scheduled Task
  `MarkItDown-MCP`, AtLogOn, RunLevel **Limited** (unelevated), via the shared hidden launcher
  (stdout/stderr land in `logs/mcp/markitdown.stdout.log` / `.stderr.log` with 10 MiB rotation).
- `README.md` — what the tool does, how it is mounted (manifest-external and why), the security
  notes below, and optional external binaries (ffmpeg → audio transcription, exiftool → EXIF; most
  formats convert without them). Azure cloud extras are NOT installed.
- goose `config.yaml` gains one extension entry (`type: streamable_http`,
  `uri: http://127.0.0.1:8794/mcp`), added with a timestamped backup of the file first — same
  practice as `setup_mcp_servers.ps1`.

## Security

- Loopback-only (`--host 127.0.0.1`), unelevated, matching the official guidance ("do not bind other
  interfaces").
- `convert_to_markdown` reads any user-readable file via `file://` and performs outbound HTTP(S)
  fetches. This grants the agent **no capability it does not already have** — the developer
  extension provides a full shell — so no confirm-gating is added. The README states this plainly.
- No secrets involved; no elevation; no writes (the tool returns markdown text, it does not save
  files).

## Error Handling

- The shim passes through the official server's behavior; it adds nothing beyond argv. If the
  package is missing, the launcher's stderr log carries the ImportError — same diagnosis path as
  every other MCP.
- Port conflict on 8794 surfaces in `logs/mcp/markitdown.stderr.log`; the README's troubleshooting
  note points there first.

## Tests

- pytest (in `mcp/markitdown/tests/`): the shim imports and resolves the official entry point without
  executing the server, and builds the exact expected argv (monkeypatch the entry's `main` to capture
  argv; assert host/port/flags).
- Manual acceptance: `Start-ScheduledTask MarkItDown-MCP` → `GET http://127.0.0.1:8794/mcp` returns
  406/400 (alive); goose_web sidebar shows the markitdown card with `convert_to_markdown`; converting
  one real PDF or DOCX via the agent yields markdown.

## Documentation

- `mcp/markitdown/README.md` as above.
- One line in `mcp/README.md`'s overview noting markitdown as a manifest-external 18th service on
  8794 (clearly marked as outside the 17-server canonical suite — the "17 servers / 3 unelevated"
  claims about the canonical suite stay true and untouched).
