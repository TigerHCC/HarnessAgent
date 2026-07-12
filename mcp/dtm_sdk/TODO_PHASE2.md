# dtmsdk — deferred / untested command inventory

Phase 1 delivered the server, all 5 runners, lookup tools, config, policy, and the always-on suite
(48 tests, fake-util — no real DTP). Live tests so far ran against **instrumentation + analytics only**,
and within those only `metadata` + a local `collect` (through the confirm flow).

**What "tested" means here:** proving our wrapper invokes the util correctly (argv, injected `--id`,
output parsing) and that the command *really executed* — not just that it exited 0. Every live test
asserts no `Unrecognized command or argument` and real content, so it can't pass on a util that merely
printed help (the trap the `--json` bug hid behind).

**Hard exclusion rule for automated tests:** never transmit telemetry to Dell, never upload a file,
never `unregister`, never mutate DTP config, never run a non-terminating command. Live tests stay gated
behind `DTM_SDK_LIVE_TESTS=1` + admin + Dell TechHub running.

Legend: 🟢 automate a live test · 🟡 manual-only / needs a decision · 🔴 do not test (excluded).

---

## Phase 1 — finish instrumentation + analytics coverage

### instrumentation (15 commands)
- [x] 🟢 `metadata` — done (live)
- [x] 🟢 `collect` — done (BatteryStaticData, via confirm flow; local, no egress)
- [ ] 🟢 `retrieve`, `client-retrieve`, `retrieve-requests`, `get-commodity` — read-only queries, still to run
- [ ] 🟡 `retrieve-file` — writes a file as admin to a caller path; needs a real `--fileId`. Manual only,
      with a temp output path that the test cleans up.
- 🔴 `periodic-collect`, `subscribe`, `subscribe-commodity` — do not terminate.
- 🔴 `set-commodity`, `enable-datatype`, `reset-datatype-state` — mutate DTP config.
- 🔴 `emit-custom-software-telemetry-event` — transmits to Dell.
- 🔴 `unregister` — destructive.

### analytics (19 commands)
- [x] 🟢 `metadata` — done (live)
- [ ] 🟢 `retrieve-analysis`, `retrieve-alert`, `retrieve-alerts`, `retrieve-client-alerts`,
      `retrieve-custom`, `retrieve-alert-subscriptions`, `retrieve-temporary-enabling-requests` —
      read-only queries, still to run
- [ ] 🟢 `custom-analysis`, `daily-analysis`, `weekly-analysis` — local on-demand analysis (no egress),
      via confirm flow
- [ ] 🟡 `default-alert`, `custom-alert` — **one-shot evaluation mode only** (never the subscribe mode,
      which does not terminate). Confirm they don't egress before automating.
- 🔴 `subscribe`, `listen-alert-subscriptions` — do not terminate.
- 🔴 `register-alert`, `create-alert-subscriptions`, `temporary-enable` — mutate DTP config.
- 🔴 `unregister` — destructive.

---

## Phase 2 — dtmutil, transmission, platinum

### dtmutil (17 commands)
- [ ] 🟢 `validate-app-configuration`, `workflow status`, `workflow retrieve collection`,
      `workflow retrieve analysis`, `workflow retrieve alert`, `workflow history`,
      `bundle-transmission-status`, `bundle-transmission-date-range`, `retrieve-bundle-id` —
      the 9 read-only queries
- [ ] 🟡 `workflow start`, `workflow cancel` — **needs a decision.** `start` needs a JSON recipe and
      actually triggers collection/analysis (and possibly transmission); `cancel` needs a running
      workflow. Side-effectful — only automate if you approve a known-safe recipe.
- 🔴 `configure-orchestrator`, `apply-app-configuration`, `clear-app-configuration`,
      `configure-proxy`, `reset-proxy` — mutate config.
- 🔴 `invoke-emergency` — emergency workflow, transmits to Dell.

### transmission (7 commands)
- [ ] 🟢 `transmission-status` — the only read-only command
- 🔴 `collect-transmit`, `retrieve-transmit`, `periodic-transmit` — transmit to Dell.
- 🔴 `file-upload` — **upload API (explicitly excluded).**
- 🔴 `cancel` — needs an in-flight transmission.
- 🔴 `unregister` — destructive.

> transmission is transmit-by-nature: only `transmission-status` is live-testable. The rest of the
> wrapper's correctness is covered by the always-on fake-util tests (argv, confirm flow, parsing).

### platinum (7 commands)
- [ ] 🟢 `transmission-status` — the only read-only command
- [ ] 🟡 `platinum-ping`, `platinum-heartbeat` — **needs a decision.** Contact Dell's Platinum
      transmitter but carry no user-telemetry payload (connectivity checks). Suggest running these
      manually rather than in the automated suite.
- 🔴 `platinum-event` — logs/transmits an event to Dell.
- 🔴 `platinum-upload` — **upload API (explicitly excluded).**
- 🔴 `configure-proxy`, `reset-proxy` — mutate proxy config.

---

## Open decisions (need your call before automating)

1. **dtmutil `workflow start` / `workflow cancel`** — approve a known-safe recipe to automate, or leave manual?
2. **platinum `ping` / `heartbeat`** — run manually as connectivity checks, or attempt to automate (they
   reach Dell but send no payload)?

## Coverage summary

| Util | 🟢 live-testable | 🟡 manual/decision | 🔴 excluded |
|---|---|---|---|
| instrumentation | 6 (metadata, collect, 4 queries) | 1 (retrieve-file) | 8 |
| analytics | ~11 (metadata, 7 queries, 3 analysis) | 2 (default/custom-alert one-shot) | 6 |
| dtmutil | 9 queries | 2 (workflow start/cancel) | 6 |
| transmission | 1 (status) | — | 6 |
| platinum | 1 (status) | 2 (ping/heartbeat) | 4 |
