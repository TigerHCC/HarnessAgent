# Profile A/B Results — diag (Plan-B merged) vs perf+sec (Plan-A split)

Run 2026-07-23 on the local model (qwen3.6 via the GB10 vLLM). 20 runs = the 10 questions in
`profile_ab_questions.md`, each under `diag` (the merged 12-family agent) and under its matching
narrow A-profile (`perf` or `sec`). Isolated `GOOSE_CONFIG` snapshots + per-profile `.goosehints`;
`--max-turns 8`; read-only diagnostic tools only. "First-family correct" = the FIRST diagnostic tool
the agent called was in the expected family. "Cites data" = the answer contained tool-derived
figures. This is a single shot per question (no repetition), so treat small gaps as noise.

## Summary

| profile | runs | expected-family hit | FIRST-family correct | cites data | failed | avg tool-calls | avg s |
|---|---|---|---|---|---|---|---|
| diag (merged) | 10 | 5/10 | 5/10 | 10/10 | 0 | 5.0 | 75 |
| perf (narrow) | 6 | **6/6** | **6/6** | 6/6 | 0 | 3.2 | **26** |
| sec (narrow)  | 4 | **0/4** | **0/4** | 4/4 | 0 | 8.5 | 189 |

**Head-to-head on the same 10 questions:** merged `diag` 5/10 first-family-correct vs split
`perf+sec` 6/10. Averages are close (5.0 vs 5.3 tool-calls).

## The real story: routing fails on FORENSICS, not on merge-vs-split

The failures are almost entirely on the forensics-type questions (crash / netconn / drift / exec),
and they fail **under both** the merged `diag` and the narrow `sec` profile — so scoping alone does
not fix them:

| q | expected | diag chose | sec chose | verdict |
|---|---|---|---|---|
| 2 | crash | memstate | procinspect | both wrong (picked process/memory, not crash) |
| 4 | netconn | procinspect,srum | procinspect,srum | both wrong (picked process/usage, not netconn) |
| 6 | drift | (no diagnostic tool) | (no diagnostic tool) | both missed drift entirely |
| 8 | exec | srum | procinspect,srum | both wrong (picked usage, not execution traces) |

The small model systematically confuses adjacent families: "誰執行過" → picks `srum` (usage) instead
of `exec`; "可疑連線" → picks `procinspect` instead of `netconn`; "崩潰" → picks `procinspect`/
`memstate` instead of `crash`; "自啟動項變化" (`drift`) is missed outright. It also over-uses
`procinspect` and `srum` as catch-alls.

By contrast the **performance** families route almost perfectly — `perf` scored 6/6, fast (26 s avg,
3.2 calls). Those questions map cleanly to intuitive family names (perfmon/disk/memstate/winupdate/
srum); the forensics names don't.

Two notable single-run anomalies: Q10 `diag` answered with **0 tool calls** (guessed from priors —
a hallucination risk the merged agent showed but narrow `perf` did not: `perf` got Q10 right with 1
call). `sec` Q6 spent 342 s and still missed drift.

## Recommendation

1. **For performance work, the narrow `Performance` profile is the clear winner** — 6/6 correct, ~3×
   faster than merged, fewer calls. On a small model, scope pays off here.
2. **For forensics, open-ended questions fail regardless of profile** (0/4 even in narrow `sec`). The
   fix is NOT more scoping — it is **prescriptive prompting**: tell the model which family to use for
   each step instead of making it route. This is exactly what the two-stage health prompts do
   (`two_stage_health_prompts.md`): stage 2 lists eventlog→crash→exec→drift→netconn→filterstack
   explicitly, removing the routing decision the model gets wrong.
3. **Merged `diag` is acceptable for performance-type asks but inherits the forensics weakness and
   occasionally skips tools entirely** (Q10). Prefer the split for anything security/forensic.

**Bottom line for a 16 GB local model:** run performance as the narrow `Performance` profile with
open questions; run forensics as the narrow `Forensics` profile but with a **prescriptive family-by-
family prompt**, not an open question. The two-stage health flow packages exactly this.

## Caveats

- Single shot per question; `--max-turns 8`; the "hit"/"first-family" metrics are heuristic parses of
  the `▸ <tool> <ext>` markers, not human-graded answer quality.
- All 20 runs completed (rc=0) and every answer cited data — the agents are *productive*; the issue is
  purely tool-*selection* accuracy on forensics.
- Raw per-run logs + results.jsonl are in the session scratchpad (`scratchpad/ab/`), not committed.
