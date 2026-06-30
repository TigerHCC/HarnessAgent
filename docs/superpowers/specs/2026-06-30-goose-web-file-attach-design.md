# Goose Harness Web — File Attach Design

**Date:** 2026-06-30
**Status:** Approved direction; pending spec review
**Component:** `goose_web/` (server.py, server.ps1, index.html, config.json, README.md)

## Problem

The goose_web chat UI cannot attach files. The composer only sends
`{session, message, mode}` to `POST /api/chat`; there is no upload endpoint and no
file UI. The goose agent itself *can* read files from its working directory
(`developer` → `shell`/`text_editor`; `computercontroller` → `pdf_tool`/`docx_tool`/
`xlsx_tool`), but the browser has no way to put a file into that working directory.

## Goal

Let a user attach one or more files in the web UI and have the goose agent read
them when answering — for **any** file type (text, code, logs, PDF/Office, binary).

## Non-goals (YAGNI)

- Automatic cleanup/expiry of uploaded files (kept under `workspace/uploads/`).
- Image *understanding* / vision (the qwen-3.6 CLI path treats images as files only).
- Multipart or base64 transports; multi-file in a single request.
- Streaming/resumable uploads, dedup, virus scanning.

## Verified assumption (tested 2026-06-30)

Saving a file into the goose `workspace` (the `goose run` cwd) and naming its
**relative path** in the message is sufficient for goose to read it. Test: wrote
`workspace/uploads/testsess/hello.txt` containing a secret phrase, sent a message
ending with the exact injected block below; goose called `shell` (`type …`) and
returned the correct phrase. So the delivery mechanism is the path injection — no
goose-side change is needed.

## Architecture & flow

1. User attaches file(s) (📎 button, drag-drop, or paste) and types a message; hits send.
2. Frontend uploads each file first: `POST /api/upload?session=<id>&name=<filename>`
   with the raw file bytes as the request body (**approach A**). Server saves it and
   returns `{ok, name, size}`.
3. Frontend then calls `POST /api/chat` with the existing body plus
   `attachments: ["<savedName>", ...]` (filenames only, in upload order).
4. Server builds the goose prompt = user text + an injected block (below), then runs
   `goose run` as today (cwd = workspace). goose reads the files with its own tools.

### Injected block (server-side, appended to the user message)

```
<user message>

[附加檔案 (相對於工作目錄):]
- uploads/<session>/<name1> (<size1>)
- uploads/<session>/<name2> (<size2>)
```

Only files that actually exist under the session's upload dir are listed. If
`attachments` is empty/absent, nothing is appended (behavior unchanged).

## API

### `POST /api/upload` (new — identical contract in server.py and server.ps1)
- **Auth:** same token gate as `/api/chat` (`X-Goose-Token` header or `?token=`).
- **Query:** `session` (sanitized `[A-Za-z0-9_.-]`, same rule as chat), `name` (raw filename).
- **Body:** the file bytes (`Content-Type` ignored; `Content-Length` required).
- **Behavior:** save to `WORKSPACE/<uploads_subdir>/<safeSession>/<safeName>`, creating
  dirs as needed. On name collision, append ` (n)` before the extension.
- **Limits:** reject `Content-Length` > `max_upload_mb` with **413**; empty/oversize/bad
  name → **400/413** JSON `{error}`.
- **Response:** `200 {ok:true, name:<safeName>, size:<bytes>}`.

### `POST /api/chat` (extended)
- Body gains optional `attachments: string[]` (filenames). Server resolves each to
  `<uploads_subdir>/<safeSession>/<safeName>`, keeps only those that exist under that
  exact dir (re-sanitized; never trusts a client path), and injects the block. Unknown
  names are silently dropped. Everything else (streaming NDJSON, modes, resume) unchanged.

## Storage layout & filename safety

- Root: `WORKSPACE/uploads/<safeSession>/`.
- `safeName`: take basename only (strip any `/`, `\`, drive, and `..`); allow
  `[A-Za-z0-9._ -]`, replace the rest with `_`; collapse leading dots; cap length (~150).
  Empty result → `file`. This prevents path traversal and absolute-path escapes.
- The resolved absolute path MUST stay within `WORKSPACE/uploads/` (verify after
  resolving); otherwise reject. Same check on the chat-side resolution.

## Security

- Upload writes are confined to `WORKSPACE/uploads/` via sanitization + containment check.
- Token gate applies to `/api/upload` exactly like `/api/chat`.
- Size cap (`max_upload_mb`, default 25) protects the box; enforced from `Content-Length`
  before reading the body where possible, and while streaming to disk as a backstop.
- `/api/chat` injects only server-resolved, existing paths — a client cannot make goose
  read arbitrary files by passing a crafted `attachments` entry.
- Reminder (already in README): with `GOOSE_MODE=auto` + computercontroller, the agent can
  act on uploaded content (run scripts, read Office files) — keep the UI behind a token /
  `127.0.0.1`.

## Config (config.json + env)

- `max_upload_mb` (default **25**) — per-file cap; env `GOOSE_WEB_MAX_UPLOAD_MB`.
- `uploads_subdir` (default `uploads`) — folder under workspace.
- Documented in README + the `config.json` `_comment`.

## UI (index.html — fits the current premium composer)

Current: `.composer > .cbox > [textarea#input, button#btnSend]` + `.chint#hint`;
`send()` at ~L434 posts to `/api/chat`.

- Add a 📎 **attach button** inside `.cbox` (left of the textarea) wired to a hidden
  `<input type=file multiple>`.
- **Drag-drop** onto `.composer` and **paste** (`paste` event) also add files.
- Selected files render as removable **chips** (name + size + ✕) in a row directly above
  the `.cbox` (inside `.composer`). State held in a JS array `pending=[]`.
- On send: if `pending` has files, first `POST /api/upload` for each (await all),
  collect saved names, then call `/api/chat` with `attachments`. Disable send + show a
  small "uploading…" state; on any upload error, show it inline and abort the send.
- The user bubble in the transcript shows the typed text plus a compact list of attached
  filenames (so the conversation reflects what was sent).
- Empty text **with** attachments is allowed: the server uses a default prompt
  (`請查看我附加的檔案。`) before the injected block. Empty text **and** no files is a
  no-op as today.

## Error handling

- Upload: oversize → 413 + chip shows error; network error → inline error, send aborted.
- Chat: unchanged streaming/error path; missing attachment files are skipped silently.
- Server creates `uploads/` lazily; disk/write errors → 500 JSON `{error}`.

## Both servers

`server.py` (BaseHTTPRequestHandler `do_POST`) and `server.ps1` (HttpListener worker)
implement `/api/upload` with the identical contract and the same sanitization helper
behavior, and both apply the injection in their chat handler. Raw-body read is trivial
in both (read `Content-Length` bytes → write file).

## Verification plan

- Unit-ish: filename sanitizer rejects `../`, absolute paths, drive letters; containment
  check holds.
- Live (both servers, 127.0.0.1): upload a .txt and a .csv, send a message, confirm goose
  reads them (echo a known token) — mirrors the passing manual test. Oversize file → 413.
  A second file with a colliding name → ` (1)` suffix. Path-traversal name → contained.
- UI: attach via button, drag-drop, paste; chips add/remove; send disabled during upload.

## Open items defaulted (confirm at spec review)

- Per-file size cap = **25 MB** (configurable).
- Paste-to-attach **included** (cheap, expected).
