# DTM Download MCP (`dtm_download`)

A local, **UNELEVATED** MCP server that downloads DTP build artifacts (installer MSI, sample zips,
datatype CSVs, doc HTML) from Artifactory. Binds **`127.0.0.1:8791`**, transport `streamable-http`,
endpoint `/mcp`.

Pure-Python reimplementation of the download half of
`ccp/tools/DTMTransmissionAutoTest-/Install-DTP.ps1` (`requests` + stdlib `zipfile`/`hashlib`; no
`.ps1` subprocess dependency). It only writes into its own configured `download_path`, so it needs no
confirm-token gating and runs unelevated -- same `RunLevel Limited` scheduled-task pattern as
`windows_obsidian`.

Pair this with [`dtm_deploy`](../dtm_deploy/README.md) (elevated): pass the `msi_path` this server
returns into `dtm_deploy`'s `dtm_install` tool. The two servers share no state or IPC.

---

## Tools

| Tool | What it does |
|---|---|
| `dtm_download_build(channel="", build_id="", arch="", build_type="")` | Resolves the latest build in `channel` (`Daily`\|`Formal`\|`Test`, default from config) if `build_id` is omitted -- highest build version number wins. Downloads + SHA256-verifies the installer/sample zips matching `arch` (`"x64"`\|`"arm64"`, default: auto-detected architecture of the machine running this MCP server) and `build_type` (`"Release"`\|`"Debug"`, default `"Release"`), extracts them into fixed-name folders (`DTPInstallers`, `DTPSamples`) so re-downloads overwrite in place, downloads the datatype CSVs, and returns `{download_path, msi_path, build_id, arch, build_type, zips, extracted, csv_files}`. Example: "install the latest Formal release build for x64" -> `channel="Formal", arch="x64"` (leave `build_type`/`build_id` blank). |
| `dtm_list_builds(channel="Daily", limit=10)` | Lists available build folder names under `DTP/<channel>` in Artifactory. |
| `dtm_download_health()` | Whether the Artifactory token is set (not its value), resolved `download_path` + existence, base URL/repo. |

## Configuration

`config.json`: `artifactory_base_url`, `repo`, `download_path` (`${repo_root}`-relative by default),
`default_channel`, `default_build_type`, `zip_components` (component name prefixes, e.g.
`["DTPInstallers", "DTPSamples"]` -- matched against Artifactory zip filenames combined with the
requested `arch`/`build_type`), `csv_files`, `html_files`, timeouts. Every key can be overridden via
`DTM_DOWNLOAD_MCP_<KEY>` env vars (see `config.py`).

**Artifactory token**: set the `DTM_DOWNLOAD_ARTIFACTORY_TOKEN` environment variable. It is never read
from `config.json` and never accepted as a tool argument, so it cannot leak into a committed file, an
LLM prompt, or a tool-call log.

## Progress & logging

Downloads emit `[dl] ...` progress lines (every 25 MB per file) to stdout, which lands in
`logs/mcp/dtm_download.stdout.log`. Each build also writes a self-contained `download.log` beside its
artifacts (`<download_path>/<build_id>/download.log`), so you can tail a single build's progress and
per-file/per-CSV/per-doc results without wading through the server's full stdout history.

## Running

```powershell
# one-off, foreground (for testing)
.\start_dtm_download_mcp.ps1

# persist across logons (elevated shell needed to REGISTER the task; the server itself runs unelevated)
.\install_task.ps1
.\uninstall_task.ps1   # remove
```

## Tests

```powershell
pip install -r requirements.txt
python -m pytest tests -q
```
`tests/test_artifactory.py` mocks `requests.get`; no live network calls in the unit suite.
