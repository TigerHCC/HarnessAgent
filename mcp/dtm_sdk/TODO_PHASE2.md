# dtmsdk — Phase 2 TODO

Phase 1 delivered the server, all 5 runners, lookup tools, config, policy, and tests. Live tests
this phase covered **instrumentation + analytics only** (safe/local commands).

## Deferred to Phase 2

- [ ] Live tests for **dtmutil** — workflow status/history/retrieve, bundle queries, validate-app-configuration.
- [ ] Live tests for **transmission** — `transmission-status`, and `collect-transmit`/`retrieve-transmit`
      through the confirm flow **only if** a safe test target exists.
- [ ] Live tests for **platinum** — `transmission-status`, `platinum-heartbeat`/`platinum-ping` through
      the confirm flow.

## Excluded from Phase 2 testing (do NOT automate)

- **All upload APIs:** `transmission file-upload`, `platinum platinum-upload`.
- Any egress command that sends real telemetry to Dell, and any `unregister` / config-mutating command.

Rationale: an automated test must not transmit telemetry, upload files, unregister the application, or
change DTP configuration on the user's machine.
