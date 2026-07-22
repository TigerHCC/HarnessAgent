# Agent Profiles Design (role-scoped tool sets for a 16GB local model)

## Goal

Raise agent accuracy on a 16GB-single-GPU local model by scoping each goose session to a role: a
named preset of enabled extensions plus a role recipe, switchable in one click from goose_web. The
default diagnostic role follows Plan B — one merged 12-MCP diagnostic agent guided by a tool-family
index — with the split A-plan presets kept alongside for an A/B accuracy comparison.

## Why (decision record)

- 19 extensions ≈ 100+ tools ≈ 10-15k tokens of schema in every session — a third of a 32k context
  gone before the conversation starts, and tool-selection confusion rises sharply for 9-14B models.
  Live tool counts (batch report 2026-07-19): the 12 diagnostic MCPs alone total 77 tools.
- The user chose Plan B for diagnostics (one merged agent + tool-family recipe) over Plan A (split
  performance/forensics agents), with an explicit A/B test to validate; A presets are kept so
  fallback is zero-cost.
- One GPU = one model: profiles scope tools and prompts, never models. MCP servers stay running
  regardless of profile (watchdog unaffected); a profile only changes what goose sees.

## Mechanism

### profiles.json (`config/profiles.json`)

Flat JSON array; each entry: `name` (id), `label` (UI display), `description` (one line), `enable`
(list of extension ids to enable — every other extension in the MANAGED set is disabled on apply),
`recipe` (repo-relative path to the role recipe markdown).

Six presets:

| name | label | enable | ~tools |
|---|---|---|---|
| diag | 系統診斷 | srum eventlog crash exec drift netconn perfmon disk procinspect memstate filterstack winupdate + memory developer | ~90 |
| perf | 效能健康 | srum perfmon memstate disk procinspect winupdate + memory developer | ~45 |
| sec | 安全鑑識 | eventlog crash exec drift netconn filterstack + developer | ~40 |
| dtm | DTM 工程 | dtm_download dtm_deploy dtmsdk + developer | ~25 |
| docs | 文件知識 | markitdown docstruct obsidian + memory developer | ~25 |
| ops | 總管排程 | scheduler + developer computercontroller memory | ~25 |

MANAGED set = the union of all ids appearing in any preset's `enable` (plus `scheduler`,
`markitdown`, `docstruct`): applying a profile enables its list and disables the rest of the managed
set. Extensions outside the managed set (unknown/user-added) are left untouched. `goose_web`'s own
non-extension config is never touched.

### Role recipes (`config/recipes/<name>.md`)

One markdown file per profile: role statement, tool-family index (diag's core content — 慢/卡→
perfmon,disk,memstate; 崩潰→crash; 誰執行過→exec; 設定變了→drift; 連線→netconn; 行程/鎖檔→
procinspect; 更新→winupdate; 用量歸因→srum), and the operating rules (one family per turn; health
first; conclusions must cite tool output; answer in the user's language). Applying a profile copies
the recipe to `workspace/.goosehints` (goose reads it at session start). A header comment in the
generated .goosehints names the source profile so staleness is detectable.

### Apply path (goose_web `/api/profiles`)

- `GET /api/profiles` → `{profiles: [...], active: <name|"custom">}`. Active = the unique preset
  whose enable-set exactly matches the current enabled state of the managed set, else `"custom"`.
- `POST /api/profiles {action:"apply", name}` →
  1. read profiles.json; validate the name;
  2. backup config.yaml to `config.yaml.bak-profile`;
  3. flip `enabled:` for every managed extension via the EXISTING `Set-ExtensionEnabled`
     (serialized under the existing `cfgWriteLock`);
  4. write `workspace/.goosehints` from the recipe file;
  5. set the existing discovery `refreshSignal` so the sidebar re-handshakes immediately;
  6. return the new active state.
- Token auth mirrors the existing `Handle-Toggle` pattern exactly.
- Policy: the profile-apply path MAY flip builtins (developer/memory/computercontroller) because a
  profile apply is an explicit human UI action; the per-card manual toggle keeps its existing
  restriction (builtins remain 403 there). Implementation must not weaken `Test-Togglable` for the
  toggle endpoint.

### WebUI (index.html)

- Sidebar gains a 角色 section above the MCP list: current profile label + tool/MCP count, a
  dropdown listing the six presets (+ "自訂" shown when active=custom), one click applies.
- Cards for disabled extensions keep the existing gray/disabled styling — the batch flip reuses the
  existing per-card visual states (checking → count fill-in).
- New-chat hero shows the active profile label + its description line.
- Poll: active profile refreshes with the existing health/schedules polling cadence.

## After this ships (operating model)

- Daily: open goose_web → pick the role → sidebar shows only that role's cards → new sessions carry
  the role recipe via .goosehints → ask. Switching roles is one click (~2s).
- All 19 MCP servers stay up; watchdog/batch-test behavior unchanged.
- Scheduler (phase 1) runs under whatever profile is active; per-schedule profiles are Phase 3 in a
  separate spec (GOOSE_CONFIG snapshot approach sketched there, out of scope here).
- A/B test (after Phase 2): 10 fixed diagnostic questions run under `diag` vs `perf`+`sec`;
  compare tool-selection errors and answer quality (scheduler runs history + manual scoring). If B
  loses, switch the default to the A presets — zero migration cost since both ship.

## Error Handling

- Unknown profile name → 404-style JSON error, config untouched.
- profiles.json unreadable/invalid or a recipe file missing → clear 500-style error naming the file;
  config untouched (validate BEFORE the backup/write phase).
- A failed mid-apply flip: config.yaml backup exists (`.bak-profile`); the response reports which
  extensions were flipped; the discovery refresh still fires so the UI shows the true state.
- .goosehints write failure does not roll back the toggle flips; the response carries a warning
  (recipe and toggles are independently useful).

## Security

- No new capability: the endpoint only batch-drives the existing config-edit path plus writes one
  file inside the workspace. Same token gate as existing endpoints. Builtin-flip is allowed only on
  this human-initiated path.

## Tests

- PS (sandboxed — never the live config/workspace): profiles.json validation (names unique, managed
  set computed, recipe paths exist); apply flips exactly the expected enabled set on a temp
  config.yaml copy; unknown name → error, file untouched; .goosehints written with the profile
  header; active-detection returns the applied name, and "custom" after a manual out-of-band flip.
- JS-free UI checks (static, as in prior features): new ids present exactly once, dropdown wiring
  references defined functions.
- Manual acceptance: click through all six profiles in goose_web; sidebar card states and hero text
  follow; run one diag question and confirm the recipe is honored (family-first tool selection).

## Documentation

- `goose_web/README.md`: the profiles endpoint + UI section.
- `mcp/README.md`: a short "agent profiles" note pointing at config/profiles.json + recipes.
- Recipes themselves double as the role documentation.
