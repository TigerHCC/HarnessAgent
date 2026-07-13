# obsidian — Design

`obsidian` is a thin FastMCP server that delegates to four focused modules. The two security-critical
pieces — `vault` (path confinement) and `tokens` (the write gate) — are pure or near-pure and
exhaustively unit-tested. Every test runs against a **fake temp vault**; the real vault is never touched
except by one opt-in, read-only live test.

## Module map

| Module | Responsibility |
|---|---|
| `tokens.py` | The write gate. **Pure: no I/O.** Computes a content-bound digest `sha256(json([op, path, mode, content]))[:16]` and verifies it against `(now, issued_at, ttl)`. Knows nothing about the vault or FastMCP — it only decides whether a given (op, path, mode, content, token) is a valid, unexpired confirmation. |
| `config.py` | Path/settings resolution only. Loads `config.json`, does `${}` expansion → env override (`OBSIDIAN_MCP_<KEY>`, plus the `OBSIDIAN_VAULT` alias for `vault_path`) → as-is, coerces the int settings, and records a `_resolved` existence map for `vault_path`. No vault knowledge, no tools. |
| `vault.py` | Path confinement + file primitives. `resolve()` is the single funnel every path passes through (see below); `read_note` / `write_note` (atomic temp + `os.replace`, `must_be_new` for create) / `note_exists` / `walk_md` all go through it, and `walk_md` additionally prunes `.obsidian/` and non-`.md` files. **The only module that touches the filesystem for note bytes.** |
| `index.py` | Markdown parsing + vault queries. Pure parse functions over text — `parse_frontmatter` (YAML), `parse_headings`, `parse_wikilinks` (target/heading/alias), `parse_tags` (inline `#tag` + frontmatter), `replace_section` — plus query functions (`search`, `backlinks`, `outlinks`, `find`) that read the vault via `vault.py`. |
| `obsidian_mcp_server.py` | The FastMCP server: the 10 tools, lazy config loading (`cfg()`), the in-process confirm-token store, and `_gated_write` (the shared resolve → issue-preview / verify-token → write body behind `obsidian_create` and `obsidian_update`). |

At runtime the modules import each other by bare name (`import vault`); this works because they share a
directory. `conftest.py` puts that directory on `sys.path` for tests, and the start script launches
FastMCP with the directory as cwd.

## The path-confinement argument (why one funnel)

Every tool that names a file — read, list, search, tag/link/frontmatter query, create, update — resolves
that name through **`vault.resolve(vault_root, rel)`** before any byte is read or written. Having exactly
one funnel is the whole design: there is no second code path that opens a note, so there is no gap where
an unchecked path could slip through. Each check inside `resolve` defends a specific escape:

- **Empty / non-string → `VaultError`.** A missing or malformed path can never be coerced into a
  surprising filesystem operation.
- **Absolute path (`/…` or `C:…`) → rejected.** Callers may only address notes *inside* the vault by
  relative path; an absolute path could otherwise name any file on the machine.
- **`..` segment → rejected.** Blocks the classic traversal (`../../secret`) that would climb out of the
  vault using only relative components.
- **Non-`.md` → rejected.** Confines the surface to markdown notes. Attachments, images, and — critically
  — executables, dotfiles, and config are unaddressable, so the tool cannot be turned into a
  general-purpose file reader/writer.
- **`.obsidian/` → rejected.** Even though it lives inside the vault, the app's config/plugin directory
  is off-limits, so vault settings and plugin data are neither leaked nor mutable.
- **`realpath` outside the vault → rejected.** The path is canonicalized with `os.path.realpath` and must
  share the vault root as a common prefix (`os.path.commonpath`). This is what defeats **symlink escape**:
  a link inside the vault pointing at `C:\Windows\…` resolves to an external realpath, fails containment,
  and is rejected. The string checks above cannot catch this — only resolving the real target can.

`walk_md` mirrors the same policy while enumerating: it prunes `.obsidian/` from the walk and yields only
`.md` files, so listing/search/graph/tag queries operate on the identical confined set. The net
guarantee: **nothing outside the vault's `.md` files is ever read or written**, by any tool, on any path.

## The token-binding argument (an A-token cannot run B)

Writes are gated by a confirm token, and the binding is what makes the gate meaningful. A token is
`sha256(json([op, path, mode, content]))[:16]` — a deterministic function of the exact write. On the
preview call, `_gated_write` stores `{token: (op, path, mode, eff_content, issued_at)}` and returns the
token. On the confirm call it consumes the token **only if all of** these hold:

1. the stored record's `(op, path, mode, eff_content)` matches the incoming request, **and**
2. `verify_token` recomputes the digest from the *incoming* op/path/mode/content and it equals the token,
   **and**
3. `now - issued_at <= confirm_ttl_seconds`.

Recomputing the digest from the incoming arguments is what makes the binding tamper-evident: a token
issued for `create Ideas/X.md "…"` produces a different digest for `Ideas/Y.md`, for `overwrite` instead
of `append`, or for content that differs by a byte. So **a token for write A cannot authorize write B** —
the mismatch drops through to re-issuing a fresh preview rather than writing. The token is deleted on use
(**single-use**) and dies after the TTL (**time-limited**). The store is in-process, so a server restart
clears all pending previews.

For `replace_section`, the heading is folded into the binding via `eff_content = "##<heading>##\n" +
content`, so the confirmation is exact to `(op, path, mode, heading, content)` — a token confirmed for
replacing the "Risks" section cannot be replayed to replace "Design".

**What the token is and is not.** It is a *binding* mechanism, not a human-approval gate. The digest is
deterministic (no nonce, no server secret), so an agent obtains a valid token simply by calling once to
get the preview and calling again with it. That is by design — nothing at the MCP layer can pause for a
human; the tool returns to Goose, not to a person. What the gate concretely buys: (a) a write is
impossible in a *single* call, forcing a deliberate second step; (b) that confirmation is bound to the
exact op/path/mode/heading/content, so it can never be replayed onto a different write; and (c) the
preview surfaces `op`, `path`, `mode`, and a `content_preview`, so a human watching Goose sees exactly
what is about to be written. It does **not** stop a fully autonomous agent that has decided to proceed;
a true human-in-the-loop gate would need enforcement on the Goose/client side, or per-machine
authentication on the MCP (the shared open item in `docs/HARDENING_BACKLOG.md`).

## Why no delete, and no silent overwrite

The server exposes **no delete tool at all.** Destroying a note is the one operation the confirmation
gate cannot make safe after the fact — a mistaken write can be reverted from the previous content, but a
deleted file's bytes are gone. Leaving delete out entirely means the harness can never lose a note
through this MCP; if the user wants a note gone, they remove it in Obsidian.

Likewise there is **no silent overwrite.** The two write paths are deliberately asymmetric:

- `obsidian_create` opens the note with `must_be_new=True` and **errors if the path already exists** — it
  can only ever bring a *new* note into being, never clobber an existing one.
- Overwriting an existing note is possible only through `obsidian_update(mode="overwrite")`, which is
  gated exactly like every other write: it returns a preview + token first, so replacing a note's entire
  contents is always a deliberate, confirmed, two-step action — never an accident of calling "create" on
  a name that happened to be taken.

So the only ways to change bytes on disk are: create a genuinely new note, append to one, replace a named
section of one, or overwrite one — and every one of the last three requires the note to already exist and
requires a confirm token. Nothing here can delete.

## `replace_section` semantics

`index.replace_section(text, heading, new_content)` swaps the body under a heading while keeping the
structure intact:

- It finds the **first** line matching `#{1,6} <heading>` (the heading text compared trimmed), and notes
  that heading's level.
- It replaces everything from just after that heading line **up to the next heading of the same or
  higher level** (a `##` section ends at the next `##` or `#`, not at a nested `###`), or to end-of-file
  if there is none.
- The **heading line itself is kept**; only its body is replaced. `new_content` is normalized to end with
  a newline so the following heading stays on its own line.
- If the heading is not found it raises `KeyError`, which the server turns into a
  `"heading … not found"` error — the note is left untouched.

This lets the harness update one section of a note (e.g. rewrite "## Risks") without rewriting the file,
and — because the heading is part of the token binding — the confirmation is exact to the section being
replaced.

## Runtime notes

- **Unelevated by design.** The server only reads/writes user-owned files, so the scheduled task is
  `RunLevel Limited` and the start script has no elevation check. It is the only MCP in the suite that
  runs unelevated.
- **Atomic writes.** `write_note` writes to a `*.tmp` sibling with `encoding="utf-8", newline=""` and
  `os.replace`s it into place, so a note is never left half-written.
- **Lazy config.** `cfg()` loads `config.json` on first use, so a bad config surfaces through
  `obsidian_health()` rather than breaking import.
- **`yaml` optional.** `index.parse_frontmatter` degrades to an empty frontmatter dict if `pyyaml` is
  missing or the block is malformed, so a stray `---` fence never crashes a read.
