# Obsidian MCP — design

**Date:** 2026-07-14
**Status:** approved (brainstorming → plan)

## Summary

A new MCP server, `obsidian`, giving the Goose harness file-level access to an Obsidian vault (a folder
of markdown notes): read, search, list, follow wikilinks/backlinks, query tags and frontmatter, and —
behind a per-write confirmation gate — create and update notes. Filesystem-based: it operates directly on
the vault's `.md` files, so it needs neither the Obsidian app running nor any Obsidian plugin.

The default vault is `C:/Users/a9027/source/Agentic/doc/DTMKnowledge/Telemetry` (587 notes, ~73 MB) — the
same DTM knowledge base the `dtm` RAG agent serves. The two are **complementary**: `dtm` does semantic
retrieval; `obsidian` does exact, structured, file-level operations (open a specific note, search by
name/content/tag, walk the link graph, write a note).

## Why filesystem, not the Local REST API plugin

An Obsidian vault is just markdown files. A filesystem MCP is self-contained, always available (no app,
no plugin, no API key), and matches the repo's existing MCP pattern (like `dtmsdk` reading its CSVs). The
Local REST API plugin would add a hard dependency on Obsidian being open plus an auth key, for the sole
benefit of seeing unsaved edits and triggering Obsidian commands — not worth it here.

## Deployment

Same pattern as the other MCPs, with one deliberate difference — **no elevation**:

Deployment is **Windows-only** (PowerShell scheduled task + a Windows vault path), so it lives in the
`windows_*` family as `mcp/windows_obsidian/`. The Python core (config/vault/index/tokens/server) is
itself portable; only the install scripts and default path are Windows-specific. Directory-minus-`windows_`
gives the extension id, matching the twelve diagnostic MCPs.

| Property | Value |
|---|---|
| Directory | `mcp/windows_obsidian/` |
| Extension id | `obsidian` |
| Transport | `streamable_http`, `http://127.0.0.1:8790/mcp` |
| Port | **8790** (next free after dtmsdk's 8789) |
| Scheduled Task | `Obsidian-MCP`, **`RunLevel Limited`** (NOT Highest), `-AtLogOn`, current user |
| Files | `install_task.ps1` / `uninstall_task.ps1` / `start_obsidian_mcp.ps1` |
| One-click | registered in `setup_mcp_servers.ps1`'s `$MCPS` (and `-Uninstall`) |
| goose_web | automatically togglable (loopback + `streamable_http`) |

This is the **only MCP that does not need Administrator at runtime** — it just reads/writes files in a
user directory. Registering the Scheduled Task still needs admin (that is a Windows requirement for task
registration), but the task *runs* unelevated (`RunLevel Limited`), and `start_obsidian_mcp.ps1` has no
elevation check. `setup_mcp_servers.ps1`'s `$MCPS` gains an optional `runlevel` field (default `Highest`;
`obsidian` sets `Limited`).

## Tools

Ten tools in two layers.

### Read / query — safe, no token

| Tool | Purpose |
|---|---|
| `obsidian_read(path)` | Full note: `content`, parsed `frontmatter`, `headings`, `wikilinks`, `tags`. |
| `obsidian_search(query, in_content=True, in_name=True, folder="", max=50)` | Case-insensitive substring over `.md` names and/or content; content search skips files larger than `max_file_bytes`. Returns path + a matching snippet. |
| `obsidian_list(folder="", max=200)` | List `.md` notes (optionally under a folder) with size + mtime. |
| `obsidian_tags(tag="")` | No arg → all tags with counts; with a tag → notes carrying it (inline `#tag` **and** frontmatter `tags:`). |
| `obsidian_backlinks(path)` | Notes that link to this note via `[[…]]`. |
| `obsidian_links(path)` | This note's outgoing wikilinks + whether each target resolves. |
| `obsidian_find(key, value="")` | Notes whose YAML frontmatter has `key` (optionally `== value`). |

### Write — gated by a confirmation token

| Tool | Purpose |
|---|---|
| `obsidian_create(path, content, confirm_token="")` | Create a **new** note. **Errors if the path already exists** (never a silent overwrite). |
| `obsidian_update(path, mode, content, heading="", confirm_token="")` | `mode ∈ append \| replace_section \| overwrite`. `append` adds to the end; `replace_section` replaces the body under the `## <heading>` line; `overwrite` replaces the whole file. The note must already exist (else → error suggesting `create`). |

There is **no delete tool** (never delete). `overwrite` is a write like any other and is gated (never a
silent overwrite).

### Health

`obsidian_health()` — resolved `vault_path`, exists, writable, note count, what is ignored (`.obsidian/`,
non-`.md`), the gated-op list, and the effective config.

## Two safety mechanisms

### 1. Path confinement (the load-bearing one for a write-capable, LLM-facing MCP)

Every tool that takes a `path` runs it through one helper, `vault.resolve(rel) -> abs | error`:

- Reject absolute paths and any path containing `..` (after normalization).
- Resolve against the vault root and verify the **realpath** is inside the vault realpath (defeats
  symlink escape).
- Require a `.md` extension.
- Reject anything under `.obsidian/`.

Nothing outside the vault's `.md` files is ever read or written. This is enforced server-side, in one
place, for both reads and writes.

### 2. Confirmation token (writes only)

`create` and `update` follow the `dtmsdk` pattern. An unconfirmed write does not execute; it returns a
preview:

```json
{
  "requires_confirmation": true,
  "confirm_token": "<sha256(op|path|mode|content)[:16]>",
  "op": "update", "path": "issue_investigations/foo.md", "mode": "append",
  "preview": "…the content to be written / a short diff summary…",
  "expires_in_seconds": 120
}
```

The agent calls again with the token. The server recomputes the hash from the *incoming*
op/path/mode/content; a mismatch is refused. Tokens are **single-use** and expire after
`confirm_ttl_seconds`. A token issued for one write cannot authorize a different one.

## Configuration

`mcp/windows_obsidian/config.json`, resolved `${var}` → env override → as-is (same loader shape as `dtmsdk`):

```json
{
  "vault_path": "C:/Users/a9027/source/Agentic/doc/DTMKnowledge/Telemetry",
  "max_search_results": 50,
  "max_file_bytes": 1048576,
  "confirm_ttl_seconds": 120
}
```

- Env overrides: `OBSIDIAN_MCP_VAULT_PATH` (and the generic `OBSIDIAN_VAULT`), `OBSIDIAN_MCP_*` for the
  others. Redeploy to another machine or vault by changing `vault_path`.
- `max_file_bytes` bounds content search (skip very large notes); `max_search_results` caps result size.
- YAML frontmatter parsing uses `pyyaml` (already in the MCP dep union via `dtmsdk`).

## Scanning rule

Only `*.md` files are scanned. `.obsidian/` (vault config), attachments, images, and every other
non-`.md` file are ignored everywhere — search, list, tags, links, find.

## File structure

```
mcp/windows_obsidian/
  obsidian_mcp_server.py   # FastMCP; the 10 tools + confirm-token store; thin
  config.py                # load, ${} expansion, env override, _resolved map
  vault.py                 # path confinement + read/write primitives + .md-only walk
  index.py                 # frontmatter/heading/wikilink/tag parse; search/backlinks/links/find
  tokens.py                # make/verify confirm token (pure, no I/O)
  config.json              # the deployable config
  requirements.txt         # mcp, pyyaml, pytest
  start_obsidian_mcp.ps1   # foreground; NO elevation check
  install_task.ps1 / uninstall_task.ps1   # task uses RunLevel Limited
  conftest.py
  README.md / DESIGN.md
  tests/
```

Each module has one job and is testable without the real vault. `vault.py` (path confinement) and
`tokens.py` (the write gate) are the security-critical parts and are pure/near-pure, so they get
exhaustive unit tests.

## Testing

### Always-on (a fake vault fixture — never the real vault)

`tests/` builds a temp vault: a few `.md` notes (with frontmatter, inline `#tags`, `[[wikilinks]]`,
`## headings`), a `.obsidian/` dir, and a non-`.md` file, so scans can be verified to ignore the last two.

- **`vault` (path confinement)** — reject `../…`, absolute paths, non-`.md`, `.obsidian/…`; accept a
  normal in-vault note. read/write primitives; atomic write; create-on-existing errors.
- **`tokens`** — token bound to op+path+mode+content; single-use; expiry; wrong-content refused; a token
  for op A refused for op B.
- **`index`** — frontmatter parse; tag extraction (inline + frontmatter); wikilink extraction (`[[N]]`,
  `[[N|alias]]`, `[[N#h]]`); backlinks; outgoing links + resolution; find-by-frontmatter.
- **`search`** — name + content; folder filter; `max`; skips files over `max_file_bytes`; ignores
  `.obsidian/` and non-`.md`.
- **`server`** — a safe tool runs; a write without a token returns a preview; a token executes; a token
  is single-use and op/path-bound; `create` on an existing path errors; `obsidian_health` shape. Config
  is monkeypatched to the temp vault, so **no test ever touches the real vault**.

### Optional live read test

One gated test (`OBSIDIAN_MCP_LIVE_TESTS=1`) reads the real vault read-only (e.g. `obsidian_health` +
a `search`), skipped cleanly otherwise. No live test ever writes to the real vault.

## Security summary

- LLM-facing and write-capable, so: all paths funnel through one confinement check (no traversal, no
  symlink escape, `.md`-only, vault-only); writes are gated by single-use, content-bound confirm tokens;
  there is no delete; overwrite is never silent.
- Like the other loopback MCPs, `obsidian` binds `127.0.0.1` with **no authentication** — any local
  process can reach it. Here that means a local process could read or (via the confirm flow) write the
  vault. This is the same open item tracked in `docs/HARDENING_BACKLOG.md` (MCP authentication); as a
  write-capable MCP, `obsidian` reinforces it. Not solved here.

## Open items

1. MCP authentication (backlog) — now also relevant because `obsidian` can write files, not just read.
