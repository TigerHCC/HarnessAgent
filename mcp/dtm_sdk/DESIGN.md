# dtmsdk — Design

`dtmsdk` is a thin FastMCP server that delegates to five small, mostly-pure modules. The
security-critical logic (`policy`) is pure and exhaustively unit-tested; the subprocess runner is tested
against a fake echo exe so **no real util — and no data egress — is ever triggered by the always-on test
suite**.

## Module map

| Module | Responsibility |
|---|---|
| `config.py` | Path resolution only. Loads `config.json`, does `${}` expansion → env override (`DTM_SDK_*`) → as-is, and records a `_resolved` existence map (`raw`/`resolved`/`exists`/`source`). Also exposes `default_client_id` / `default_client_name` (the default `--id`/`--appName`). No subprocess, no util knowledge. |
| `policy.py` | Command classification + confirm-token logic. **Pure: no I/O, no subprocess.** Owns the five util→command lists (65), the 24-command safe allowlist, the egress/state category maps, `classify`/`is_safe`, `validate_command`, and `make_token`/`verify_token`. The source of truth for what is safe. |
| `runner.py` | Subprocess execution. Builds an **argv list (never a shell)**, runs with a timeout, and parses stdout `json → yaml → text` with the raw text always preserved. Requests JSON via the `DTPUTIL_JSON_OUTPUT=true` env var (NOT a `--json` CLI flag — the real utils reject it per-subcommand). Returns a uniform result dict. |
| `datatypes.py` | CSV lookup over the three datatype tables: case-insensitive substring search, exact `find_one`, and `difflib`-based near-miss `suggest`. |
| `howto.py` | Section extraction from `Sample_Utilities_HowTo.md` — a util's section and a single command's block — so options are read from the doc rather than hard-coded. |
| `dtm_sdk_mcp_server.py` | The FastMCP server: 9 tools, lazy config/table/HowTo loading, the in-process confirm-token store, and `_dispatch` (the shared validate → classify → issue-preview/verify-token → run body behind the five `dtm_run_*` tools). |

At runtime the modules import each other by bare name (`import policy`); this works because they share a
directory. `conftest.py` puts that directory on `sys.path` for tests, and the start script launches
FastMCP with the directory as cwd.

## Why classification is by SDK method, not command name

A command's *name* is a poor guide to whether it is dangerous. Classification is derived from each
command's **documented SDK method**, so a "retrieve"-sounding command can be dangerous and a
"transmission"-sounding command can be perfectly safe. Two worked examples that motivate the design:

- **`instrumentation retrieve-file` is GATED despite the "retrieve" in its name.** It performs an
  **elevated write to a caller-chosen path** — a genuine mutation of the filesystem — so it is dangerous
  regardless of the friendly verb. (See `test_retrieve_file_is_not_safe`.)
- **`dtmutil bundle-transmission-date-range` is SAFE despite "transmission" in its name.** It calls
  `RetrieveBundleTransmissionStatusItemsAsync` — a **query** that returns status items and transmits
  nothing. (See `test_bundle_date_range_is_safe_despite_its_name`.)

Because names lie in both directions, the safe allowlist is curated per documented behaviour, and anything
**not** on the allowlist — dangerous *or simply unrecognised* — is gated. An unknown/new command
classifies as `unknown` and is treated as gated, so the server fails safe when the util set grows.

## Why `retrieve-file` is gated

`retrieve-file` writes a file to a path the caller chooses, and the server runs elevated — so it is an
**arbitrary elevated write**. That is exactly the kind of side effect the confirmation gate exists to
guard. The "retrieve" prefix groups it with genuinely read-only commands like `retrieve`,
`client-retrieve`, and `retrieve-requests` (all safe), but its SDK behaviour is a write, so it is
deliberately kept off the safe allowlist.

## The token-binding argument (an A-token cannot run B)

A confirm token is `sha256("<util>|<command>|<json-argv>")[:16]`. The argv is JSON-serialised with fixed
separators so the binding is exact and stable. On preview, `_dispatch` stores
`{token: (util, command, args, issued_at)}`. On the execute call it consumes the token **only if all of**
these hold:

1. the stored record's util matches,
2. the stored record's command matches,
3. the stored record's args match, **and**
4. `verify_token` recomputes the digest from the *incoming* util/command/args and it equals the token,
   **and** `now - issued_at <= 120`.

Recomputing the digest from the incoming arguments is what makes the binding tamper-evident: a token
issued for `collect-transmit --datatype-name X` produces a different digest for `--datatype-name Y` or for
`cancel`, so **the token for command/args A cannot authorise command/args B** — the mismatch drops through
to re-issuing a fresh preview instead of executing. The token is deleted on use (single-use) and dies after
the 120-second TTL. This is verified by `test_token_bound_to_args`, `test_token_bound_to_command`,
`test_token_expires`, and the server-level single-use / wrong-command tests.

**What the token is and is not.** It is a *binding* mechanism, not a human-approval gate. The digest is
deterministic (a pure function of util+command+args, no nonce or server secret), so the agent obtains a
valid token simply by calling the tool once to receive the preview, then calling again with it. That is by
design: nothing at the MCP layer can pause for a human — the tool returns to Goose, not to a person. So the
gate delivers three concrete things: (a) it makes a mutating/egress action impossible to trigger in a
*single* call, forcing a deliberate second step; (b) it binds that confirmation to the exact argv, so a
confirmation can never be replayed onto a different command; and (c) the preview surfaces the full
`command_line` + `reason` + `category`, so a human watching Goose's output sees exactly what is about to be
transmitted or changed. It does **not** stop a fully autonomous agent that has decided to proceed. A real
human-in-the-loop gate would need enforcement on the Goose/client side, or per-machine authentication on the
MCP (the shared open item in `docs/HARDENING_BACKLOG.md`).

## The 24 / 41 split

Of the 65 commands, **24 are safe** (run directly) and **41 are gated** (need a confirm token). The 41
gated commands carry a category used only to word the preview's `reason`:

- **egress** — "transmits data from this machine to Dell"
- **state** — "changes DTP/system configuration"
- **action** — "triggers work or does not terminate on its own"
- (**unknown** — an unrecognised command; also gated, worded "is not on the safe allowlist")

| Util | Commands | Safe | Gated | ↳ egress | ↳ state | ↳ action |
|---|---:|---:|---:|---:|---:|---:|
| `dtmutil` | 17 | 9 | 8 | 1 | 5 | 2 |
| `instrumentation` | 15 | 5 | 10 | 1 | 4 | 5 |
| `analytics` | 19 | 8 | 11 | 0 | 4 | 7 |
| `transmission` | 7 | 1 | 6 | 4 | 1 | 1 |
| `platinum` | 7 | 1 | 6 | 4 | 2 | 0 |
| **Total** | **65** | **24** | **41** | **10** | **16** | **15** |

The safe commands are the read-only/query verbs: `dtmutil`'s workflow-status/history/retrieve and bundle
queries plus `validate-app-configuration` and `retrieve-bundle-id`; `instrumentation`'s
`retrieve`/`client-retrieve`/`retrieve-requests`/`get-commodity`/`metadata`; `analytics`'s
`retrieve-*` family plus `metadata`; and `transmission` + `platinum` `transmission-status`. Everything
else — collection/transmission, config/proxy changes, alert registration, enables, unregisters, uploads,
heartbeats, emergencies — is gated.

## Runtime notes

- For the four `DtpUtilHelper` utils (`dtmutil`, `instrumentation`, `analytics`, `transmission`) the
  runner requests JSON via the `DTPUTIL_JSON_OUTPUT=true` **env var only**. It does **not** pass a
  `--json` CLI flag: phase-1 live testing showed the real utils reject `--json` as a per-subcommand
  argument (System.CommandLine emits `Unrecognized command or argument '--json'`, prints the command's
  help, and exits 0 — so the command silently does nothing). `DTMPlatinumUtil` gets neither (no
  `DtpUtilHelper`). Note some commands (e.g. `metadata`) emit human-readable text regardless, which the
  `json → yaml → text` fallback handles.
- Output parsing is `json.loads → yaml.safe_load → raw text`. A parse failure never turns a successful
  command (exit 0) into a failure; the raw stdout is always preserved.
- Commands are validated against `^[a-z0-9][a-z0-9 -]*$` and executed as an argv list, never via a shell,
  so shell-injection strings like `cancel; whoami` are rejected before anything runs.
- Config, tables, and HowTo are loaded lazily so a bad `config.json` never breaks import; `dtm_health()`
  surfaces the specific unresolved path.
