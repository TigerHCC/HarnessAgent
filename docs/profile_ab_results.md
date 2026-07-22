# Profile A/B Results — tool-scoping accuracy on a 16 GB local model (qwen3.6 / GB10)

**Headline (corrected, 2026-07-23): with real tool scoping, forensics routing is 10/10 and overall
first-family accuracy is 17/18 (94%), stable across two independent runs.** The narrow profiles work;
the earlier "forensics fails 0/4" number was a broken-harness artifact (see the correction at the
bottom). The lever that matters is **scoping the toolset to the task**, not the recipe wording.

## How this was measured (the corrected harness)

Each question runs under its narrow profile with the toolset scoped to EXACTLY that profile's
extensions, via `goose run --no-profile --with-streamable-http-extension <uri>` per MCP (the goose
CLI ignores `GOOSE_CONFIG`; it honors only `--no-profile`+explicit extensions, or the live config's
`enabled:false` — which plain `goose run` **does** respect, so an applied goose_web profile scopes real
chats). `--max-turns 8`, temperature 0, read-only diagnostic tools. "First-family correct" = the first
diagnostic tool called was in the expected family; "any-family hit" = the expected family was used at
some point. 18 questions (10 original + 8 added for stability, weighted toward the previously-suspect
forensics families).

## Results — two runs

| iteration | sec (Forensics) first-family | perf (Performance) first-family | overall first | overall any-hit | failed |
|---|---|---|---|---|---|
| baseline recipes  | **10/10** | 7/8 | 17/18 | 17/18 | 0 |
| recipe-v2 (＋"must use a tool") | **10/10** | 6/8 | 16/18 | 17/18 | 0 |

- **Forensics is rock-solid: 10/10 in both runs**, including the added stability variants (netconn ×2,
  exec ×2, drift ×2, crash ×2, eventlog, filterstack). Fast too (avg 52–76 s, ~3 tool calls).
- **Performance ~7/8.** The perf "first-family" wobble (7 vs 6) is single-shot noise on Q1 (the model
  opened with `srum` vs `perfmon` — both defensible; any-hit stayed correct).
- **recipe-v2 did not help.** Adding a verbose "always call a tool first" rule left forensics
  unchanged, did NOT fix the one real miss (Q10), and added first-tool noise on Q1. Reverted; the
  **original recipes are the shipping version.** The win is scoping, not recipe verbosity.

The single miss (both runs): **Q10** "有沒有哪個行程鎖住了*某個*檔案導致無法刪除？" — the model
answered with 0 tool calls (~7 s). This question is genuinely underspecified: procinspect's
"who-locks-a-file" needs a target file, and with none named the model deferring ("which file?") is
defensible rather than a routing failure. Kept in the set as a known borderline.

## Conclusions

1. **Scoping is the fix, not scoping-vs-merging semantics.** When the toolset is narrowed to the task,
   the small model can't reach for the catch-all tools (`procinspect`/`srum`) it otherwise over-picks,
   so forensics families (crash/exec/drift/netconn/eventlog/filterstack) route correctly — 10/10.
2. **goose_web already delivers this.** Applying the `Forensics` (or any) profile sets `enabled:false`
   on the other extensions in the live config, and plain `goose run` honors that — so a real chat under
   an applied profile is properly scoped. No code change was needed; the feature works.
3. **Prefer the narrow profiles over merged `diag` for anything forensic.** Merged `diag` keeps the
   catch-all tools in play and showed the same Q10 skip-the-tool risk. The two-stage health flow
   (`two_stage_health_prompts.md`) already routes performance through `Performance` and forensics
   through `Forensics`.
4. **Recipe wording is a weak lever here; toolset scope is the strong one.** Don't add verbose rules —
   they add noise. Keep recipes tight and let scope do the work.

## Stability note

Forensics routing was 10/10 on two independent runs across 10 distinct forensics questions (6 families,
2 phrasings each for the weak ones) — strong evidence it is stable, not a lucky single shot. The only
run-to-run variation was first-vs-later tool ordering on one performance question, which never changed
the answer's correctness (any-hit was 17/18 both times).

## Caveats

- Metrics are heuristic parses of the `▸ <tool> <ext>` markers, not human-graded answer quality; every
  run cited data and completed (rc=0). `--max-turns 8`. Two runs per configuration.
- Raw logs + `results2.jsonl` live in the session scratchpad (`scratchpad/ab/`), not committed.

---

## Appendix — the original (broken) run, for the record

> **⚠ The first A/B used a broken harness.** It set `GOOSE_CONFIG` to per-profile snapshots, but the
> goose CLI **ignores `GOOSE_CONFIG`** and read the live config (`diag`, all 12 tools enabled) for
> every run — so nothing was actually scoped, and "sec" could and did call `procinspect`/`srum` (not
> even in its profile), producing a bogus "forensics 0/4". Under true scoping (above) the same
> forensics questions score 10/10. The lesson: to scope a `goose run`, use
> `--no-profile --with-streamable-http-extension`, or set `enabled:false` in the config it actually
> reads (the live one).
