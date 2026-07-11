# Goose Harness Web — Per-MCP Enable/Disable Toggle

**Date:** 2026-07-12
**Status:** Approved direction; pending spec review
**Component:** `goose_web/` (server.py, server.ps1, index.html) + goose's live `config.yaml`

## Problem

The goose_web UI lists connected MCP extensions read-only. There is no way to turn
an individual MCP off (or back on) from the browser. Today, doing so means hand-editing
goose's `config.yaml` and knowing the YAML schema. The 12 local Windows diagnostic MCPs
(`srum` … `winupdate`) are the ones a user actually wants to flip on/off between
questions — e.g. disable `filterstack`/`memstate` when they're not needed, or disable an
extension whose backend server is down so goose stops failing to connect to it each turn.

## Goal

Add a small on/off toggle to each **Windows diagnostic MCP** card in the web UI. Flipping
it enables/disables that extension for the goose agent, taking effect on the **next chat
turn**, with no server restart.

## Non-goals (YAGNI)

- **Starting/stopping the MCP server process** (the elevated scheduled task). The toggle is
  purely goose's config-level `enabled` flag — "does the agent load this extension." Whether
  the backend server on `127.0.0.1:87NN` is running is independent and out of scope. Enabling
  an extension whose server is down simply shows `offline`, exactly as today.
- Toggling **non-Windows** extensions: `developer`, `memory`, `computercontroller` (builtin)
  and `dtm` (remote) render as they do now, with **no** toggle, and are refused server-side.
- Session-scoped/ephemeral toggles (the `--no-profile` + `--with-*` command-line route). See
  "Approach" for why this was rejected.
- Reordering, adding, or removing extensions from the UI; editing URLs/timeouts.

## Approach (decided)

**Edit the `enabled:` line in goose's live `config.yaml`, written the moment the toggle is
flipped (event-driven, once per flip).** Because every chat turn already spawns a fresh
`goose run` that re-reads `config.yaml`, the flag is honored on the next turn automatically.

Rejected alternatives (compared 2026-07-12):

- **Command-line parameters to `goose run`.** Verified against `goose run --help`: goose's
  extension flags are **additive only** (`--with-extension`, `--with-streamable-http-extension`,
  `--with-builtin`). There is **no flag to disable a single configured extension**; the only
  subtractive control is `--no-profile`, which drops *all* config extensions. Disabling one MCP
  would therefore force goose_web to `--no-profile` and faithfully **reconstruct the entire
  enabled set every turn** (each URL/timeout/builtin/stdio-cmd). More code, more per-turn work,
  and silent-wrong-toolset risk if reconstruction drifts. Rejected.
- **Write config just before each turn (A2)** instead of on-flip (A1). A2 writes `config.yaml`
  on *every* turn even when nothing changed — more file churn, more exposure to a mid-write
  crash or collision with goose's own config rewrites, and it splits the source of truth
  (goose_web memory vs the file). On-flip writing keeps `config.yaml` continuously correct with
  the fewest writes and survives a goose_web restart. Rejected in favor of A1.

## Togglable predicate

An extension is togglable **iff** `type == "streamable_http"` **and** its `uri` host is
loopback (`127.0.0.1`, `localhost`, or `::1`). This selects exactly the 12 Windows diagnostic
MCPs and auto-includes any future `windows_*` MCP (all loopback streamable_http by convention).
It excludes `dtm` (streamable_http but remote `192.168.86.44`) and the builtins. No hardcoded
id list to maintain. The predicate is enforced **server-side** (not just hidden in the UI): a
toggle request for any non-togglable id is refused.

## API

### `POST /api/extensions/toggle` (new — identical contract in server.py and server.ps1)
- **Auth:** same token gate as `/api/chat` (`X-Goose-Token` header or `?token=`).
- **Body:** JSON `{"id": "<extension id>", "enabled": <bool>}`.
- **Validation (all → `400`/`403` JSON `{error}`):**
  - `id` must be present and must match an extension that exists in `config.yaml`.
  - that extension must satisfy the togglable predicate (loopback streamable_http). Refuse
    builtins / remote / unknown ids with `403 {"error":"extension not togglable"}`.
  - `enabled` must be a boolean.
- **Behavior:** surgically set that extension's `enabled:` to the requested value in
  `config.yaml` (see Config writer), then trigger an immediate discovery refresh so the next
  `/api/health` reflects it.
- **Response:** `200 {"ok":true, "id":"<id>", "enabled":<bool>}`. Idempotent — toggling to the
  current value is a successful no-op write-skip.

### `GET /api/health` (changed)
Every extension object gains two fields:
- `enabled` (bool) — from `config.yaml`.
- `togglable` (bool) — the predicate above.

Disabled **togglable** extensions are now **included** in the snapshot (previously filtered
out) with `status:"disabled"`, `count:0`, and **no handshake attempted**. Disabled
**non-togglable** extensions remain hidden (current behavior). New status value `"disabled"`
joins the existing `ok` / `offline` / `checking`.

## Config writer (surgical, both server ports)

Goose's `config.yaml` is only *partially* parsed by goose_web, so the writer must be
line-oriented and touch exactly one line:

1. Read `config.yaml` as text (preserve line endings).
2. Locate the `extensions:` block, then the target extension's key line — indent-2 `  <id>:`.
3. Within that extension's body (until the next indent-≤2 line), find its `enabled:` line
   (indent 4) and replace only the value (`true`/`false`), preserving indentation/spacing.
4. If the block has **no** `enabled:` line, insert `    enabled: <bool>` immediately after the
   `  <id>:` line.
5. If the value already equals the requested one, skip the write (idempotent no-op).
6. **Backup before first write:** if `config.yaml.bak-webtoggle` does not exist, copy the
   current `config.yaml` to it.
7. **Read-only guard:** the durability convention may set the file read-only (`IsReadOnly`).
   If so, clear it, write, then restore it.
8. **Atomic write:** write to `config.yaml.tmp` in the same directory, then replace
   (`os.replace` / `Move-Item -Force`) so a crash mid-write can never leave a partial file.

The writer only ever rewrites the single `enabled:` value; every other byte (provider block,
other extensions, comments, args lists) is passed through unchanged.

## Frontend (`index.html`, shared by both servers)

- In `renderExt`, when `x.togglable` is true, render a small switch in the card header showing
  the current `enabled` state.
- Flipping it: optimistic UI → `POST /api/extensions/toggle` with `{id, enabled}` and the
  stored `X-Goose-Token`. On `200`, refresh `/api/health`. On error, revert the switch and show
  the message; on `401`, clear the token and re-prompt (same pattern as chat/upload).
- Disabled cards render greyed with status "disabled" and the switch off; the tool list area
  shows "disabled" instead of "no tools exposed". Non-togglable cards are unchanged (no switch).
- A one-line hint near the toggle clarifies scope: the change applies to the **next** message
  (config-level; does not start/stop the backend server).

## Safety

- Token-gated identically to `/api/chat`.
- Server-side allowlist (the predicate) is the real gate; UI hiding is cosmetic.
- `config.yaml` backed up to `config.yaml.bak-webtoggle` before the first edit.
- Atomic temp-file replace; read-only bit honored and restored.
- Writer is scoped to a single `enabled:` value — it cannot alter provider config, other
  extensions, or structure.

## Testing

Mirror the existing `goose_web/tests/` pytest pattern with a fixture `config.yaml`:

- **Config writer unit tests** (Python; the PS1 writer shares the same fixtures/cases):
  - flip `true → false → true` round-trips; only the target `enabled:` line changes (diff the
    rest byte-for-byte).
  - target extension with **no** `enabled:` line → line inserted at the right indent/position.
  - idempotent no-op when value already matches (no write / backup untouched on 2nd call).
  - block-boundary correctness: toggling `srum` never touches `eventlog`'s `enabled:` (adjacent
    blocks), and never touches a same-named key outside the `extensions:` tree.
  - read-only file → cleared, written, restored.
- **Predicate tests:** loopback streamable_http → togglable; remote streamable_http (`dtm`) and
  builtins → not togglable → toggle request refused with 403.
- **Health-shape test:** a disabled togglable extension appears with `status:"disabled"`,
  `enabled:false`, `togglable:true`, `count:0`.

## Files touched

| File | Change |
|---|---|
| `goose_web/server.py` | `/api/extensions/toggle` route; config writer; `_build_snapshot` includes disabled togglable + `enabled`/`togglable` fields; predicate helper; refresh-on-write |
| `goose_web/server.ps1` | Same, mirrored (PowerShell / HttpListener) |
| `goose_web/index.html` | Per-card toggle switch, POST + optimistic update, disabled rendering |
| `goose_web/tests/` | New `test_toggle.py` (writer + predicate + health-shape) |
| `goose_web/README.md` | Document the toggle + the `config.yaml.bak-webtoggle` backup |

## Open assumption to verify during implementation

`goose run` reads `config.yaml` fresh on each invocation (so an on-flip edit is honored next
turn without restart). This is goose's documented behavior and matches how goose_web already
relies on per-turn `goose run` processes, but the implementation plan should include a quick
live check: toggle `filterstack` off, confirm its tools vanish from the next turn's toolset,
toggle on, confirm they return.
