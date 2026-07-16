# Markdown audit

Audit date: 2026-07-15. Scope: every repository `*.md` file returned by
`rg --files -g '*.md'`, excluding this audit until creation. The final inventory contains 60 files:
28 current operational, 19 component reference, 12 historical spec/plan/result, and 1
generated/vendor reference.

## Findings and policy

- Current local MCP truth is the 14-entry `config/mcp_servers.json`: ports 8777-8790, twelve
  read-only diagnostic servers, confirmation-gated `dtmsdk`, and confirmation-gated `obsidian` whose
  Scheduled Task is `RunLevel Limited`. Its scheduled/logon launches are unelevated; an immediate start
  by elevated suite setup inherits the setup token until restarted through the task or at next logon.
- Current entry points needed batch-test instructions. They now document the safe MCP handshake,
  unelevated client, default `reports/mcp/` artifacts, exit codes 0/1/2, and degraded health versus
  transport/protocol/tool-call failure.
- The link-check RED run found one real broken link: `docs/install_results.md` referenced the removed
  root `docker-compose.yaml`. It now targets `config/docker-compose.yaml`. Focused parser coverage now
  includes angle-bracket destinations with spaces/titles, encoded paths, and brace/angle-containing
  missing paths rather than broadly excluding those characters.
- The watchdog audit found a broken current workflow: it still regex-parsed the setup script after
  setup moved to the JSON manifest, producing zero targets. It now validates
  `config/mcp_servers.json`, covers all 14 servers, and has a non-probing inventory test mode.
- Apparent links inside fenced and inline code are examples, not Markdown links; the checker ignores
  those syntactic regions. HTTP, mail, same-document anchors, and exact `{placeholder}` targets are
  outside local-file validation; angle brackets are parsed as standard Markdown destination syntax.
- Dated plans, specs, spike notes, and results retain authored-at-the-time counts and decisions.
  They are classified as historical rather than rewritten as current operations. The batch-test plan's
  component-suite verification command was corrected so its executable evidence remains reproducible.

## Current operational (28)

- `README.md` - Primary entry point; current 14-server overview was correct, and batch-test safety,
  outputs, exits, manifest, and architecture links were added; its stale 12-only table label was fixed.
- `RUN.md` - Live launch guide; added an upfront 14-server protocol batch-test section.
- `docs/DIAGNOSTIC_PLAYBOOK.md` - Current symptom-to-tool guide for the twelve diagnostic servers;
  diagnostic-only scope remains accurate and no change was required.
- `docs/HARDENING_BACKLOG.md` - Active risk backlog; references tests and operational hardening rather
  than claiming the current server inventory, so no count correction was required.
- `docs/inventory_and_debug_for_windows.md` - Current Windows inventory/debug reference; inspected for
  stale port/count/setup claims and found no concrete batch-test correction.
- `docs/LOOP_PROMPT.md` - Current hardening-loop prompt; references the twelve read-only diagnostics as
  its audit boundary, not the full local suite, so that wording remains valid.
- `docs/MARKDOWN_AUDIT.md` - This complete categorized inventory and findings record.
- `docs/MODULE_RELATIONSHIPS.md` - Current architecture; added manifest-driven setup and batch-test
  relationships, all 14 servers, and JSON/Markdown report outputs.
- `docs/SETUP_GUIDE.md` - Authoritative setup guide; Step 5 installation/config counts were corrected
  from 12 to 14 and protocol batch verification replaced setup reruns as the verification path.
- `docs/windows-diagnostic-mcp-candidates.md` - Current roadmap for twelve diagnostic MCPs; its scope
  intentionally excludes `dtmsdk` and `obsidian`, so no rewrite was needed.
- `goose_web/README.md` - Current browser UI reference; toggle scope was corrected from twelve
  diagnostics to all 14 loopback local MCPs, including `dtmsdk` and `obsidian`.
- `mcp/README.md` - Central MCP reference; setup wording now covers all 14 and central batch-test
  instructions link the manifest and architecture.
- `mcp/dtm_sdk/README.md` - Current `dtmsdk` operations; added a backlink to central batch testing.
- `mcp/windows_crash/README.md` - Current crash MCP usage; added a central batch-test backlink.
- `mcp/windows_disk/README.md` - Current disk MCP usage; added a central batch-test backlink.
- `mcp/windows_drift/README.md` - Current drift MCP usage; added a central batch-test backlink.
- `mcp/windows_eventlog/README.md` - Current Event Log MCP usage; added a central batch-test backlink.
- `mcp/windows_exec/README.md` - Current execution-evidence MCP usage; added a central batch-test backlink.
- `mcp/windows_filterstack/README.md` - Current filter-stack MCP usage; added a central batch-test backlink.
- `mcp/windows_memstate/README.md` - Current memory-state MCP usage; added a central batch-test backlink.
- `mcp/windows_netconn/README.md` - Current network MCP usage; added a central batch-test backlink.
- `mcp/windows_obsidian/README.md` - Current Limited-task vault MCP usage; added a central batch-test backlink.
- `mcp/windows_perfmon/README.md` - Current performance MCP usage; added a central batch-test backlink.
- `mcp/windows_procinspect/README.md` - Current process MCP usage; added a central batch-test backlink.
- `mcp/windows_srum/README.md` - Current SRUM MCP usage; added a central batch-test backlink.
- `mcp/windows_winupdate/README.md` - Current Windows Update MCP usage; added a central batch-test backlink.
- `tools/mcp_watchdog/README.md` - Current optional watchdog reference; its stale setup-script registry
  claim exposed a zero-target workflow and was corrected to the validated 14-entry JSON manifest.
- `tools/sysmon/README.md` - Current optional Sysmon integration for Event Log; no stale local link,
  count, or batch command was found.

## Component reference (19)

- `mcp/dtm_sdk/DESIGN.md` - `dtmsdk` module/security design; current component rationale, no broken link.
- `mcp/dtm_sdk/TODO_PHASE2.md` - Deferred DTP command inventory; future work, not operational setup.
- `mcp/windows_crash/DESIGN.md` - Crash MCP design boundary; no stale full-suite claim.
- `mcp/windows_disk/DESIGN.md` - Disk MCP design boundary; no stale full-suite claim.
- `mcp/windows_drift/DESIGN.md` - Drift MCP design boundary; no stale full-suite claim.
- `mcp/windows_eventlog/DESIGN.md` - Event Log MCP design; its component-local port remains correct.
- `mcp/windows_eventlog/PLAN.md` - Component implementation plan; retained as authored context.
- `mcp/windows_eventlog/SPIKE_NOTES.md` - Dated API spike evidence; retained without present-tense rewrite.
- `mcp/windows_exec/DESIGN.md` - Execution-evidence design; no stale full-suite claim.
- `mcp/windows_filterstack/DESIGN.md` - Filter-stack design; no stale full-suite claim.
- `mcp/windows_memstate/DESIGN.md` - Memory-state design; no stale full-suite claim.
- `mcp/windows_netconn/DESIGN.md` - Network-connection design; no stale full-suite claim.
- `mcp/windows_obsidian/DESIGN.md` - Obsidian confinement/write-gating design; current port 8790 is correct.
- `mcp/windows_perfmon/DESIGN.md` - Perfmon design; no stale full-suite claim.
- `mcp/windows_procinspect/DESIGN.md` - Process-inspection design; no stale full-suite claim.
- `mcp/windows_srum/DESIGN.md` - SRUM design; no stale full-suite claim.
- `mcp/windows_srum/PLAN.md` - Component implementation plan; retained as authored context.
- `mcp/windows_srum/SCHEMA.md` - Machine-confirmed SRUM schema notes; data reference, not setup guidance.
- `mcp/windows_winupdate/DESIGN.md` - Windows Update design; component-local port 8788 remains correct.

## Historical spec, plan, or result (12)

- `docs/install_goose_harness_plan.md` - Original Goose installation plan; preserved as historical.
- `docs/install_results.md` - Dated install/smoke-test results; corrected its concrete compose-file link
  while retaining historical observations and test counts.
- `docs/superpowers/plans/2026-06-30-goose-web-file-attach.md` - Completed file-attach plan; examples
  inside code fences are not live Markdown links.
- `docs/superpowers/plans/2026-07-12-goose-web-mcp-toggle.md` - Completed toggle plan; its code examples
  remain historical and are excluded from prose-link parsing.
- `docs/superpowers/plans/2026-07-13-dtm-sdk-mcp.md` - Completed `dtmsdk` plan; dated implementation
  counts and fenced example paths were retained.
- `docs/superpowers/plans/2026-07-14-mcp-batch-test.md` - Controller-owned batch-test plan; its
  component-suite verification command was corrected while historical design decisions were retained.
- `docs/superpowers/plans/2026-07-14-obsidian-mcp.md` - Completed Obsidian plan; fenced example paths
  are historical, not local prose links.
- `docs/superpowers/specs/2026-06-30-goose-web-file-attach-design.md` - Dated file-attach design;
  preserved without rewriting decisions.
- `docs/superpowers/specs/2026-07-12-goose-web-mcp-toggle-design.md` - Dated MCP toggle design;
  preserves the then-current twelve diagnostic toggle scope.
- `docs/superpowers/specs/2026-07-13-dtm-sdk-mcp-design.md` - Dated `dtmsdk` design; current references
  remain component-scoped.
- `docs/superpowers/specs/2026-07-14-mcp-batch-test-design.md` - Dated design for manifest-driven
  testing and this audit; preserved as decision history.
- `docs/superpowers/specs/2026-07-14-obsidian-mcp-design.md` - Dated Obsidian design; current port and
  limited-run-level assumptions remain consistent.

## Generated or vendor reference (1)

- `docs/dtm_sdk_doc/Sample_Utilities_HowTo.md` - Imported/generated DTP utility reference; treated as
  vendor-style command documentation and left unchanged.
