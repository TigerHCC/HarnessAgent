# Loop prompt — HarnessAgent hardening loop

A prompt designed to be run repeatedly (`/loop`), where each iteration finds **one** real defect in
this repo, proves it, fixes it, tests it, and commits it. Distilled from the session that audited the
12 diagnostic MCPs, added the goose_web per-MCP toggle, and found the Big5/UTF-8 request-decoding bug.

## How to run it

```
/loop <paste the prompt below>
```

Omit an interval to let the model self-pace. The loop keeps its own state in
`docs/HARDENING_BACKLOG.md`, so it survives context resets and won't redo finished work.

To stop it: tell it to stop, or let it hit the stop condition (two consecutive dry iterations).

---

## The prompt

> You are hardening the HarnessAgent repo. Each iteration you fix **exactly one** defect, end to end.
>
> **State.** `docs/HARDENING_BACKLOG.md` is your durable memory across iterations — read it **first**,
> every time. It has three sections: `## Open` (candidate defects, newest first), `## Done` (fixed,
> with commit SHA), and `## Rejected` (investigated, not real — with the evidence, so nobody re-opens
> it). If the file doesn't exist, create it. Never work on something already in `Done` or `Rejected`.
>
> **Each iteration, in order:**
>
> 1. **Pick one.** Take the highest-severity item from `## Open`. If `## Open` is empty, hunt for new
>    candidates — audit *one* area you haven't covered recently and append what you find. Good hunting
>    grounds, roughly in priority order:
>    - **Parity drift** between `goose_web/server.py` and `goose_web/server.ps1`. They implement the
>      same HTTP contract twice; every divergence is a latent bug. This is the richest vein in the repo.
>    - **Hand-maintained duplicates of a source of truth** — a list in a script that restates what a
>      config/requirements/registry file already says. These drift silently. (One such bug — a
>      hardcoded `$deps` that had already lost `pytest` — was found this way.)
>    - **Docs that contradict the code.** Check every command, flag, port, path, and task name in a doc
>      against the script that actually implements it. Also flag orphan docs (nothing links to them) and
>      docs whose stated scope no longer matches reality.
>    - **Encoding / locale assumptions** — see Known traps below.
>    - **Error paths and concurrency** in `server.py` / `server.ps1`: what happens on a slow or dead MCP
>      backend, two simultaneous requests, a lock held across I/O.
>    - **Untested surface** in `mcp/windows_*/`: a tool with no test, an ungated privileged call, a
>      failure that crashes instead of returning a structured error.
>
> 2. **Prove it before you fix it.** Do not fix anything you have only reasoned about. Build the
>    smallest thing that demonstrates the defect *actually happening* — a scratch script, a failing
>    test, a real HTTP request against a listener you start yourself. Watch it fail. If you cannot make
>    it fail, the defect is not real: move it to `## Rejected` with what you tried, and pick the next
>    item. **A plausible-sounding bug that you could not reproduce is not a bug.**
>
>    Beware of tests that lie. Verify your harness isn't corrupting the input before it reaches the
>    code under test (see Known traps). If a test would pass even with the bug present, it is worthless —
>    assert that the *pre-fix* path fails, so the test cannot pass vacuously.
>
> 3. **Fix it**, in the smallest change that addresses the root cause rather than the symptom. Match the
>    surrounding code's style. If the same root cause has more than one symptom, fix all of them — but
>    stay inside that one root cause; do not start a second defect.
>
> 4. **Test it.** Add a regression test to `goose_web/tests/` or `mcp/windows_*/tests/` that fails
>    before your fix and passes after. Then run the full suite for the area you touched
>    (`cd goose_web && python -m unittest discover -s tests`) and report the count. A green suite you
>    did not run is not evidence.
>
> 5. **Commit.** One commit, message explaining *why* (the root cause and how you proved it), not just
>    what. **Do not push.** Do not merge.
>
> 6. **Update the backlog**: move the item to `## Done` with its commit SHA, and append any new
>    candidates you noticed in passing (don't act on them — that's the next iteration).
>
> 7. **Report** in ≤6 lines: what was broken, how you proved it, what you changed, test count, commit
>    SHA, and what's next in the backlog.
>
> **Stop** when two consecutive iterations find nothing real (`## Open` empty and a fresh audit turns up
> no reproducible defect). Say so plainly instead of inventing work.
>
> **Guardrails — these override everything above:**
> - **Never push, never merge, never rewrite history.** Commit to `main` and stop there.
> - **Never restart or stop the live goose_web server** on `:8799` — that is the user's to do. If your
>   fix touches `server.ps1`, say so in the report: it takes effect on their next restart.
> - **Never run** `setup_mcp_servers.ps1`, any `install_task.ps1`/`uninstall_task.ps1`, or anything that
>   registers, unregisters, starts, or kills a Scheduled Task or MCP process. You may *read* and *edit*
>   them; you may not execute them.
> - **Back up `config.yaml` before touching it**, and never write a value you did not read first.
> - The 12 diagnostic MCPs are **read-only with respect to the system**. Never add a tool or code path
>   that writes to the registry, filesystem, or any Windows subsystem.
> - Don't install software, kernel drivers, or accept EULAs.
> - If a fix requires a judgment call the code can't settle (delete a doc vs rewrite it; change a public
>   contract; anything irreversible), **stop and ask** instead of guessing.
>
> **Scale your effort to the finding.** A one-line typo fix does not need a subagent fleet. A
> cross-cutting audit (all 12 MCPs, both server backends) should fan out to parallel subagents and then
> verify their claims yourself — subagents overstate. Anything a subagent reports as a bug gets the
> same "prove it before you fix it" treatment as everything else.
>
> **Known traps in this codebase** (each of these has already burned someone — check for more of them):
> - **PowerShell 5.1 reads a BOM-less `.ps1` as ANSI**, so a literal CJK string in the source is
>   corrupted at *parse* time. Build non-ASCII from code points. This silently invalidates tests.
> - **.NET `HttpListenerRequest.ContentEncoding` falls back to the system ANSI codepage** when the
>   request has no `charset` — and `QueryString` %-decodes with it too. Always force UTF-8.
> - **`[System.Uri]("http://[::1]:x").Host` returns the expanded `[0:0:0:0:0:0:0:1]`**, while Python's
>   `urlparse().hostname` returns `::1`. String comparison cannot achieve py↔ps parity; compare parsed
>   `IPAddress` values. (And `IPAddress.IsLoopback` over-matches all of `127.0.0.0/8`.)
> - **PowerShell runspaces do not inherit functions.** Helpers must be injected as text and
>   `Invoke-Expression`'d, or the worker will fail at runtime with no compile-time warning.
> - **Python's `Path.read_text()` strips `\r`** (universal newlines). Use `open(..., newline="")` on
>   both read and write when a file's CRLF must survive.
> - **Never hold a lock across an MCP handshake** — a dead backend's timeout will stall every other
>   request that needs the same lock.
> - **`goose run` re-reads `config.yaml` every turn**, so a config edit takes effect on the next turn
>   with no restart. But `goose run`'s extension flags are **additive only** — there is no CLI flag to
>   disable a single extension.

---

## Why it's shaped this way

Each rule is scar tissue from this session:

| Rule | What it prevents |
|---|---|
| One defect per iteration | Scope sprawl; half-finished work at context boundaries |
| Durable backlog file | Re-litigating the same finding after a context reset |
| Prove it before you fix it | Fixing a bug that was never there. Two "bugs" this session dissolved under a repro |
| Assert the pre-fix path fails | A test that passes vacuously. The first encoding test lied because PS 5.1 corrupted its own literals |
| `## Rejected` with evidence | The loop re-opening something it already dismissed |
| Don't push / don't restart / don't run installers | The loop taking an action the user hasn't seen and can't easily undo |
| Verify subagent claims | A subagent "fixed" the `::1` parity bug by over-matching all of `127.0.0.0/8` — a *new* divergence |
| Stop condition | The loop inventing busywork once the real defects are gone |
