# MarkItDown MCP (`markitdown`)

A local, **UNELEVATED** MCP server that converts documents and web/data resources to Markdown. Binds
**`127.0.0.1:8794`**, transport `streamable-http`, endpoint `/mcp`.

This is the **OFFICIAL Microsoft `markitdown-mcp` package** (PyPI: [`markitdown-mcp`](https://pypi.org/project/markitdown-mcp/)),
run unmodified via a thin launch shim. It is mounted **manifest-external** -- the 18th MCP service in
this repo, outside the canonical 17-server manifest (ports 8777-8793) -- because it exposes no
`health_tool`. The shared watchdog (`tools/mcp_watchdog`) and this repo's MCP batch-test harness both
key off a per-server health tool to confirm liveness; `markitdown-mcp` has exactly one tool
(`convert_to_markdown`) and no health probe, so it cannot participate in either. In practice this means
the server restarts only at next logon (via its own Scheduled Task, not the watchdog) -- if it crashes
mid-session, nothing auto-restarts it until you log on again or start it manually.

## Tool

| Tool | What it does |
|---|---|
| `convert_to_markdown(uri)` | Converts a resource at `uri` to Markdown and returns the text. Accepts `http:`, `https:`, `file:`, and `data:` URIs. |

Supported formats (via the underlying [`markitdown`](https://pypi.org/project/markitdown/) library):
PDF, PowerPoint, Word, Excel, images (with EXIF + OCR metadata), audio (with speech transcription),
HTML, CSV/JSON/XML, ZIP archives (iterates contents), YouTube URLs (transcript), EPub, and plain text.
Most formats work out of the box with no extra binaries.

## Why manifest-external

- No `health_tool` -> cannot be probed by `tools/mcp_watchdog` or the repo's MCP batch-test harness,
  which both require a health tool to confirm a server is alive and correctly configured.
- Consequence: it is **not** auto-restarted by the watchdog. It only comes back up at the next logon
  (its own `AtLogOn` Scheduled Task trigger) or if you start it manually.
- Everything else about it follows the same pattern as the canonical servers: unelevated
  (`RunLevel Limited`), loopback-only, launched hidden via `scripts/start_mcp_hidden.ps1`.

## Security notes

- Binds `127.0.0.1` only (loopback) and the Scheduled Task runs **unelevated** (`RunLevel Limited`),
  same as every other server in this repo.
- `convert_to_markdown("file://...")` reads **any file the logged-on user can read** -- there is no
  path allowlist or sandbox. `convert_to_markdown("http://..." / "https://...")` performs outbound
  network requests (egress) to fetch the resource.
- This is **not** a new capability grant: any developer machine already running the Goose desktop
  extension has an unrestricted local shell, which can already read any user-readable file and make
  arbitrary outbound requests. `markitdown-mcp` grants nothing beyond what the shell already permits --
  which is why, unlike some tools in this repo, it has **no confirm-token gating** on `convert_to_markdown`.
- It is the official, unmodified Microsoft package -- this repo does not patch or wrap its behavior,
  only its startup arguments (see `run_markitdown_mcp.py`).

## Optional binaries (not required for most formats)

- **ffmpeg** -- needed for audio transcription (non-WAV formats). Without it, `pydub` logs a warning
  at import time and audio conversion may fail for compressed formats.
- **exiftool** -- improves EXIF metadata extraction from images. Without it, image conversion still
  works but with less metadata.
- **Azure Document Intelligence extras are NOT installed** (`markitdown[az-doc-intel]`) -- only the
  base `markitdown-mcp` package (which pulls in base `markitdown`) is installed here. PDFs/images that
  would otherwise route through Azure Document Intelligence fall back to local extraction instead.

## Running

```powershell
# one-off, foreground (for testing)
python run_markitdown_mcp.py

# persist across logons (elevated shell needed to REGISTER the task; the server itself runs unelevated)
.\install_task.ps1
.\uninstall_task.ps1   # remove
```

The shim (`run_markitdown_mcp.py`) exists only because the shared hidden launcher
(`scripts/start_mcp_hidden.ps1`) takes a `.py` `ServerPath`. It pins `sys.argv` to
`["markitdown-mcp", "--http", "--host", "127.0.0.1", "--port", "8794"]` and hands off to the official
package's entry point (`markitdown_mcp.__main__.main`, resolved via `_resolve_main()`). All behavior is
the official package's, unmodified.

## Registering with goose

Register this server as a goose extension with `register_goose_extension.ps1` (Task 2 of this feature).
It points goose's `config.yaml` at `http://127.0.0.1:8794/mcp` under the extension name `markitdown`,
the same way the canonical 17 servers are registered.

## Troubleshooting

- **Server won't respond / check logs first**: `logs\mcp\markitdown.stderr.log` and
  `logs\mcp\markitdown.stdout.log` (written by `scripts/start_mcp_hidden.ps1`). Most startup failures
  (missing dependency, port conflict, bad argv) show up there immediately.
- **Port 8794 already in use**: find and stop whatever's bound to it --
  `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` and filter `CommandLine` for
  `run_markitdown_mcp`, then `Stop-Process -Id <pid>`. Only one instance should ever be running.
- **`ModuleNotFoundError: markitdown_mcp`**: `pip install -r requirements.txt` in this directory.
- **Bare `GET /mcp` returns 406/400/405**: this is expected -- FastMCP-style servers reject a plain GET
  without the correct `Accept`/session headers. It confirms the server is alive; it is not an error.

## Tests

```powershell
pip install -r requirements.txt
python -m pytest tests -v
```

`tests/test_shim.py` covers the shim's argv pinning, entry-point resolution, and that `sys.argv` is set
*before* the official entry point is invoked. No live network calls or real `markitdown_mcp` server
start in the unit suite.
