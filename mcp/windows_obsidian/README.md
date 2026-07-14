# Obsidian vault MCP (`obsidian`)

A local MCP server that gives the harness **file-level access to an Obsidian vault** — read a note,
search by name/content, list notes, walk the wikilink/backlink graph, and query tags and YAML
frontmatter — plus **confirmation-gated** create/update of markdown notes. Binds
**`127.0.0.1:8790`**, transport `streamable-http`, endpoint `/mcp`. The extension id is **`obsidian`**.

It is **filesystem-based**: it reads and writes the vault's `.md` files directly, so no Obsidian app,
plugin, or running instance is required. This makes it **complementary to the `dtm` RAG**, which does
*semantic* retrieval over an embedded corpus — `obsidian` is *exact, structured* access to the same
notes (open this file, list this folder, who links here, what carries this tag). Use `dtm` to find
notes by meaning; use `obsidian` to read, relate, and edit them precisely.

Three properties define the server, and they hold for every call:

- **Runs unelevated.** The scheduled task is registered `RunLevel Limited` (not `Highest`) and the
  start script has no elevation check — it only reads and writes files the logged-in user already owns,
  so it needs no Administrator rights. This is the only MCP in the suite that runs unelevated.
- **Write-gated.** `obsidian_create` and `obsidian_update` never write on the first call. They return a
  single-use, content-bound confirm token; you must call again with that token to actually write. There
  is **no delete tool** and **no silent overwrite**.
- **Path-confined to the vault.** Every path from every tool funnels through `vault.resolve()`, which
  rejects absolute paths, `..` traversal, non-`.md` files, and `.obsidian/`, and (via `realpath`) refuses
  any symlink that would escape the vault root. Nothing outside the vault's `.md` files is ever read or
  written.

---

## The 10 tools

**Read / query (never write, no token):**

| Tool | What it does |
|---|---|
| `obsidian_read(path)` | Read a note: full content, parsed YAML frontmatter, headings, outgoing wikilinks, and tags. `path` is vault-relative (e.g. `sub/Note.md`). |
| `obsidian_search(query, in_content=True, in_name=True, folder="", max=50)` | Case-insensitive substring search by filename and/or content; content search skips files larger than `max_file_bytes`. Returns matching paths with a snippet. |
| `obsidian_list(folder="", max=200)` | List `.md` notes (optionally under a folder) with size + mtime. |
| `obsidian_tags(tag="")` | No arg: every tag with a note count. With a tag: the notes carrying it (inline `#tag` or frontmatter `tags:`). |
| `obsidian_backlinks(path)` | Notes that link **to** this note via `[[...]]`. |
| `obsidian_links(path)` | This note's **outgoing** wikilinks, each with whether its target resolves to an existing note. |
| `obsidian_find(key, value="")` | Notes whose YAML frontmatter has `key` (optionally `== value`; matches list members too). |
| `obsidian_health()` | Vault path + existence + writability, note count, and the gated-op list. **Check this first.** |

**Write (confirmation-gated):**

| Tool | What it does |
|---|---|
| `obsidian_create(path, content, confirm_token="")` | Create a **new** note (errors if it already exists — never overwrites). Returns a `confirm_token` you must pass back to actually write. |
| `obsidian_update(path, mode, content, confirm_token="", heading="")` | Update an existing note. `mode` = `append` \| `replace_section` (needs `heading`) \| `overwrite`. Gated the same way; never deletes. |

Only these two tools write, and only after a confirm token. Everything else is read-only.

---

## The confirmation flow (worked example)

A write takes **two calls**: a **preview** call (no token) that returns a token, then a **write** call
that passes the token back. The token is a `sha256` digest bound to the exact `op + path + mode +
content`, it is **single-use** (the server pops it on use), and it **expires** after
`confirm_ttl_seconds` (default 120).

**Step 1 — preview.** Call the write tool with an empty `confirm_token`:

```
obsidian_create(path="Ideas/Latency budget.md", content="# Latency budget\n\nDraft…\n")
```

Nothing is written. Instead you get a preview and a token:

```json
{
  "requires_confirmation": true,
  "confirm_token": "9f2a1c7b0e4d6a58",
  "preview": {
    "op": "create",
    "path": "Ideas/Latency budget.md",
    "mode": "",
    "content_preview": "# Latency budget\n\nDraft…\n"
  },
  "expires_in_seconds": 120
}
```

Inspect the `preview`. If it is what you intend, proceed.

**Step 2 — write.** Call the **same** tool with the **same** arguments, passing the token back:

```
obsidian_create(path="Ideas/Latency budget.md", content="# Latency budget\n\nDraft…\n",
                confirm_token="9f2a1c7b0e4d6a58")
```

Now the note is written and you get the result:

```json
{ "ok": true, "op": "create", "path": "Ideas/Latency budget.md", "bytes": 34 }
```

`obsidian_update` works identically. For `mode="replace_section"` you also pass `heading=`, and the
heading is folded into the token binding so the confirmation is exact to that section:

```
obsidian_update(path="Notes/Spec.md", mode="replace_section", heading="Risks",
                content="- new risk\n")                         # -> preview + token
obsidian_update(path="Notes/Spec.md", mode="replace_section", heading="Risks",
                content="- new risk\n", confirm_token="…")      # -> writes
```

**Token rules.**

- **Content-bound.** The token is `sha256(json([op, path, mode, content]))[:16]`. Change the op, path,
  mode, heading, or a single byte of content and the digest differs — a token issued for write A cannot
  authorize write B. The mismatch simply drops through to re-issuing a fresh preview.
- **Single-use.** The token is consumed on the successful write call; reusing it re-issues a preview.
- **Expires.** After `confirm_ttl_seconds` (default 120) the token is dead and you must preview again.
- **Per-process.** The pending-token store is in-process; restarting the server clears all pending
  previews (intended).

---

## Path confinement

Every path a tool receives is resolved through `vault.resolve(vault_root, rel)` before any read or
write. It is the single funnel, and it enforces all of the following — a path must pass **every** check:

- **No absolute paths.** A leading `/` or a drive-letter prefix (`C:`) is rejected. Callers address
  notes only by vault-relative path.
- **No traversal.** Any `..` segment is rejected, so a relative path cannot climb out of the vault.
- **`.md` only.** A path that does not end in `.md` is rejected. Attachments, images, PDFs, and every
  other file type are unaddressable.
- **`.obsidian/` off-limits.** The vault's config/plugin directory is rejected outright, so app settings
  and plugin data can never be read or written.
- **No symlink escape.** The path is resolved with `os.path.realpath` and must share the vault root as a
  common path prefix. A symlink inside the vault that points outside it resolves to an external realpath,
  fails the containment check, and is rejected.

Because reads, writes, and directory walks all go through this one function (and `walk_md` additionally
prunes `.obsidian/` and non-`.md` files while scanning), there is no tool path that can touch a file
outside the vault's markdown notes.

Only `*.md` is ever scanned. `.obsidian/`, attachments, images, and all non-`.md` files are ignored
everywhere — in listing, searching, tag/frontmatter/link queries, and writes.

---

## Configuration

All settings live in **`config.json`**:

```json
{
  "vault_path": "C:/Users/you/vault",
  "max_search_results": 50,
  "max_file_bytes": 1048576,
  "confirm_ttl_seconds": 120
}
```

| Key | Meaning |
|---|---|
| `vault_path` | Absolute path to the Obsidian vault root. This is the only thing you normally change per machine. |
| `max_search_results` | Hard cap on `obsidian_search` results (a caller's `max` is clamped to this). |
| `max_file_bytes` | Content-search size ceiling — files larger than this are skipped when searching content. |
| `confirm_ttl_seconds` | Write-token lifetime, in seconds. |

Each value is resolved in three steps:

1. **`${}` expansion.** `${var}` is substituted from sibling top-level string keys plus a built-in
   `${repo_root}` (the HarnessAgent repo root), iterating until stable — so `vault_path` can be written
   relative to the repo, e.g. `"${repo_root}/vault"`.
2. **Environment override.** An env var `OBSIDIAN_MCP_<KEY>` overrides the matching key
   (`OBSIDIAN_MCP_VAULT_PATH`, `OBSIDIAN_MCP_MAX_FILE_BYTES`, `OBSIDIAN_MCP_CONFIRM_TTL_SECONDS`, …). The
   convenience alias **`OBSIDIAN_VAULT`** also overrides `vault_path`. `OBSIDIAN_MCP_CONFIG` points the
   loader at an alternate config file.
3. **As-is.** Whatever remains is used verbatim.

The loader records a `_resolved` map (`raw` / `resolved` / `exists`) for `vault_path`, and
`obsidian_health()` surfaces existence + writability so a misconfigured vault path is diagnosable
without a stack trace.

**Redeploying to a new machine or a new vault is a one-liner: change `vault_path`** (and redeploy — the
server re-reads config on start).

---

## Runs unelevated

This server needs **no Administrator rights**. It only reads and writes files the logged-in user
already owns, so:

- `start_obsidian_mcp.ps1` has **no elevation check** — it just launches the server.
- The scheduled task is registered with **`RunLevel Limited`** and an `-AtLogOn` trigger as the current
  user, so it comes up unelevated when you log in.

(Registering *any* scheduled task is itself a Windows operation that requires an elevated shell, but the
task it registers — and therefore the running server — is unelevated. Goose reaches it over loopback
HTTP, which has no UAC/UIPI boundary, so an unelevated agent talking to it is fine.)

---

## Install / uninstall

Persist it as a logon Scheduled Task (`Obsidian-MCP`, **`RunLevel Limited`**, at logon, current user).
Registering the task requires an elevated shell (a Windows requirement); the server it runs is
unelevated:

```powershell
cd mcp\windows_obsidian
.\install_task.ps1                       # register the task (needs an elevated shell to REGISTER)
Start-ScheduledTask -TaskName Obsidian-MCP
.\uninstall_task.ps1                     # remove the task
```

Or use the repo-wide one-click installer, which also installs Python deps and registers the extension
into goose's `config.yaml` (it honors the per-server `runlevel`, so `obsidian` is registered Limited):

```powershell
powershell -ExecutionPolicy Bypass -File .\..\..\setup_mcp_servers.ps1   # add -Uninstall to remove
```

To run it standalone in the foreground:

```powershell
.\start_obsidian_mcp.ps1
```

Goose connects over `streamable_http` at `http://127.0.0.1:8790/mcp`.

---

## Tests

The always-on suite runs entirely against a **fake temp vault** — no test touches the real vault. The
one exception is `tests/test_live.py`, which is opt-in (gated behind `OBSIDIAN_MCP_LIVE_TESTS=1`) and
read-only; it skips cleanly otherwise.

```bash
cd mcp/windows_obsidian
python -m pytest tests/ -q --ignore=tests/test_live.py    # always-on suite
python -m pytest tests/test_live.py -q                     # 2 skipped unless OBSIDIAN_MCP_LIVE_TESTS=1
```

See [`DESIGN.md`](DESIGN.md) for the module map, the path-confinement and token-binding arguments, and
why there is no delete and no silent overwrite.

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
