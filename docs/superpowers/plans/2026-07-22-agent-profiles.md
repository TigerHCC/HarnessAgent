# Agent Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Role-scoped tool sets for goose: `config/profiles.json` (6 presets) + role recipes → `.goosehints`, a goose_web `/api/profiles` endpoint that batch-applies a preset via the existing `Set-ExtensionEnabled`, and a sidebar role switcher.

**Architecture:** Data (profiles.json + recipes) is Task 1. One new dot-sourceable helper file `goose_web/profiles_helpers.ps1` (folded into `$DiscoveryFns` exactly like `mcp_toggle.ps1` — single copy, testable standalone) carries parse/active-detect/apply; `server.ps1` adds a thin `Handle-Profiles` + routes. UI is a sidebar section + hero line. Docs + the A/B question set close it out.

**Tech Stack:** JSON + markdown (data), PowerShell 5.1 (helpers/endpoint/tests), vanilla JS (index.html), pytest (profiles.json contract test).

## Global Constraints

- Profile apply MAY flip builtins (developer/memory/computercontroller); the per-card `/api/extensions/toggle` endpoint's `Test-Togglable` restriction is NOT weakened — do not touch `mcp_toggle.ps1`.
- Apply order: validate EVERYTHING first (profile exists, recipe file readable) → backup `config.yaml` to `.bak-profile` (once per apply) → flip managed set under the existing `cfgWriteLock` → write `workspace/.goosehints` (failure = warning, not rollback) → set the existing `refreshSignal`.
- MANAGED set = union of all presets' `enable` lists. Extensions outside it are never flipped.
- Tests are sandboxed: temp copies of config.yaml/profiles.json/recipes/workspace — NEVER the live config or live `workspace/.goosehints`.
- Token auth on both routes mirrors `Handle-Toggle` exactly.
- Zero changes to suite files (manifest/setup/batch test/watchdog) and `mcp_toggle.ps1`.
- Branch `feature/agent-profiles`; commit there; do not push.
- Every commit body ends with the repo trailers:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013Wm8BeurMKFZK6TgvFhLjE`. (Omitted below — add them.)

---

## File Structure

- Create `config/profiles.json`, `config/recipes/{diag,perf,sec,dtm,docs,ops}.md`
- Create `tests/test_profiles.py` (root pytest, beside test_mcp_manifest.py)
- Create `goose_web/profiles_helpers.ps1`, `goose_web/tests/test_profiles.ps1`
- Modify `goose_web/server.ps1` (fold helpers, `Handle-Profiles`, routes, `$S.profilesPath`/`$S.repoRoot`)
- Modify `goose_web/index.html` (角色 section + JS + hero)
- Modify `goose_web/README.md`, `mcp/README.md`; Create `docs/profile_ab_questions.md`

---

## Task 1: profiles.json + recipes + contract test

**Files:**
- Create: `config/profiles.json`, `config/recipes/diag.md`, `config/recipes/perf.md`, `config/recipes/sec.md`, `config/recipes/dtm.md`, `config/recipes/docs.md`, `config/recipes/ops.md`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Produces the profiles.json contract consumed by Task 2: flat array of `{name, label, description, enable: [ids], recipe: "config/recipes/<name>.md"}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profiles.py
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWN_IDS = {
    "srum", "eventlog", "crash", "exec", "drift", "netconn", "perfmon", "disk",
    "procinspect", "memstate", "filterstack", "winupdate", "dtmsdk", "obsidian",
    "dtm_download", "dtm_deploy", "scheduler", "markitdown", "docstruct",
    "developer", "memory", "computercontroller",
}


def load():
    return json.loads((ROOT / "config" / "profiles.json").read_text(encoding="utf-8"))


def test_profiles_shape_and_names():
    profiles = load()
    assert len(profiles) == 6
    names = [p["name"] for p in profiles]
    assert names == ["diag", "perf", "sec", "dtm", "docs", "ops"]
    assert len(set(names)) == 6
    for p in profiles:
        assert p["label"] and p["description"]
        assert isinstance(p["enable"], list) and p["enable"]


def test_enable_ids_are_known():
    for p in load():
        unknown = set(p["enable"]) - KNOWN_IDS
        assert not unknown, f"{p['name']}: unknown ids {unknown}"


def test_recipe_files_exist_and_mention_rules():
    for p in load():
        rp = ROOT / p["recipe"]
        assert rp.is_file(), f"missing recipe {p['recipe']}"
        text = rp.read_text(encoding="utf-8")
        assert len(text) > 100


def test_diag_is_plan_b_superset_of_perf_and_sec():
    by = {p["name"]: set(p["enable"]) for p in load()}
    diag_diagnostics = by["diag"] - {"memory", "developer"}
    ab = (by["perf"] | by["sec"]) - {"memory", "developer"}
    assert diag_diagnostics == ab           # diag = merged A-plan halves
    assert len(diag_diagnostics) == 12
```

- [ ] **Step 2: Run to verify FAIL** — `python -m pytest tests/test_profiles.py -v` from repo root → FileNotFoundError/ENOENT for profiles.json.

- [ ] **Step 3: Write `config/profiles.json`**

```json
[
  {
    "name": "diag", "label": "系統診斷",
    "description": "全面系統診斷（Plan B）：效能、崩潰、執行痕跡、設定漂移、連線、更新，一個 agent 全包，靠工具家族索引選路。",
    "enable": ["srum", "eventlog", "crash", "exec", "drift", "netconn", "perfmon", "disk",
               "procinspect", "memstate", "filterstack", "winupdate", "memory", "developer"],
    "recipe": "config/recipes/diag.md"
  },
  {
    "name": "perf", "label": "效能健康",
    "description": "慢/卡/漏水/更新失敗診斷與每日健康巡檢（Plan A 前半）。",
    "enable": ["srum", "perfmon", "memstate", "disk", "procinspect", "winupdate",
               "memory", "developer"],
    "recipe": "config/recipes/perf.md"
  },
  {
    "name": "sec", "label": "安全鑑識",
    "description": "崩潰分析、執行痕跡、設定漂移、可疑連線追查（Plan A 後半）。",
    "enable": ["eventlog", "crash", "exec", "drift", "netconn", "filterstack", "developer"],
    "recipe": "config/recipes/sec.md"
  },
  {
    "name": "dtm", "label": "DTM 工程",
    "description": "DTP build 下載 → 安裝 → consent → 傳輸驗證全流程（確認閘門保護不變）。",
    "enable": ["dtm_download", "dtm_deploy", "dtmsdk", "developer"],
    "recipe": "config/recipes/dtm.md"
  },
  {
    "name": "docs", "label": "文件知識",
    "description": "文件轉 Markdown、掃描件 OCR 欄位抽取、寫入 Obsidian 筆記庫。",
    "enable": ["markitdown", "docstruct", "obsidian", "memory", "developer"],
    "recipe": "config/recipes/docs.md"
  },
  {
    "name": "ops", "label": "總管排程",
    "description": "建立與管理排程任務、輕量電腦操作、任務分派入口。",
    "enable": ["scheduler", "computercontroller", "memory", "developer"],
    "recipe": "config/recipes/ops.md"
  }
]
```

- [ ] **Step 4: Write the six recipes.** `config/recipes/diag.md` (the Plan-B core, full text):

```markdown
# 角色：系統診斷 agent（profile: diag）

你是 Windows 系統診斷 agent。回答一律使用使用者的語言（預設繁體中文）。

## 工具家族索引 —— 選工具前先選家族
- 慢／卡／資源吃緊 → perfmon（即時計數器）、disk（磁碟健康/USN）、memstate（記憶體歸因）
- 崩潰／藍屏／應用程式當掉 → crash
- 「誰執行過什麼」／惡意程式痕跡 → exec（Prefetch/BAM/UserAssist/ShimCache）
- 設定被改了／自啟動項變化 → drift（快照＋diff）
- 可疑連線／誰在連外 → netconn（連線＋擁有者行程）
- 行程檢查／誰鎖住檔案／hang → procinspect
- Windows Update 失敗／待重開機 → winupdate
- 歷史用量歸因（CPU/網路/耗電，誰用的） → srum
- 系統/應用事件記錄 → eventlog
- 濾網驅動疊層（防毒/VPN 干擾） → filterstack

## 規則
1. 每一輪只使用一個家族的工具；需要跨家族時，先總結目前發現再進下一個家族。
2. 進入任何家族前，先呼叫該 MCP 的 health 工具確認在線。
3. 結論必須引用工具輸出的具體數據（數字、路徑、時間戳），不可臆測。
4. 找不到答案時，明說查了哪些家族、排除了什麼，並建議下一步。
5. 破壞性操作（刪除、修改設定）一律先徵求使用者同意。
```

`perf.md`（diag 的效能子集）：

```markdown
# 角色：效能健康 agent（profile: perf）

你是 Windows 效能健康 agent。回答使用使用者的語言（預設繁體中文）。

## 工具選路
- 即時 CPU/磁碟延遲/記憶體 → perfmon ｜ 磁碟健康/SMART/檔案變更 → disk
- 記憶體歸因/pool 洩漏 → memstate ｜ 行程/鎖檔/hang → procinspect
- 更新失敗/待重開機 → winupdate ｜ 歷史用量歸因 → srum

## 規則
先 health 再查詢；結論引用具體數據；每輪一個主題；破壞性操作先徵求同意。
```

`sec.md`：

```markdown
# 角色：安全鑑識 agent（profile: sec）

你是 Windows 安全鑑識 agent。回答使用使用者的語言（預設繁體中文）。

## 工具選路
- 事件記錄 → eventlog ｜ 崩潰/BSOD → crash ｜ 執行痕跡 → exec
- 設定/自啟動漂移 → drift ｜ 連線與擁有者 → netconn ｜ 濾網疊層 → filterstack

## 規則
先 health 再查詢；證據鏈完整（時間戳＋路徑＋行程）；只讀不改；每輪一個主題。
```

`dtm.md`：

```markdown
# 角色：DTM 工程 agent（profile: dtm）

你是 DTP/DTM 工程流程 agent。回答使用使用者的語言（預設繁體中文）。

## 流程與工具
1. 下載 build → dtm_download（dtm_download_build；token 由環境變數提供，絕不索取）
2. 安裝/反安裝/consent/plugin/傳輸 → dtm_deploy（mutating 工具都有 confirm_token 閘門：
   先呼叫拿 token，向使用者確認後帶 token 重呼叫）
3. SDK 工具/資料型別查詢 → dtmsdk

## 規則
安裝類操作務必轉述 confirm 預覽給使用者、取得同意後才確認執行；失敗先看回傳的 log_tail。
```

`docs.md`：

```markdown
# 角色：文件知識 agent（profile: docs）

你是文件處理與知識庫 agent。回答使用使用者的語言（預設繁體中文）。

## 工具選路
- 一般文件（Office/HTML/EPub/音訊）轉 Markdown → markitdown（convert_to_markdown）
- 掃描 PDF／要「欄位→值」結構化 JSON → docstruct（doc_extract；帳單用 template cht_bill）
- 讀寫 Obsidian 筆記 → obsidian（寫入有逐篇確認閘門）

## 規則
純文字 PDF 先試 markitdown；空結果或掃描件改用 docstruct。OCR 來源的金額務必提醒使用者複核。
```

`ops.md`：

```markdown
# 角色：總管排程 agent（profile: ops）

你是排程與總務 agent。回答使用使用者的語言（預設繁體中文）。

## 工具選路
- 排程建立/查詢/暫停/立即執行/歷史 → scheduler（mutating 工具有 confirm_token 閘門）
- 輕量電腦操作 → computercontroller ｜ 檔案/shell → developer

## 規則
建排程時與使用者確認 cron/at 時間、session 名、mode（auto 會無人值守跑工具，要明示風險）；
先用 sched_list 查現況避免重複排程。
```

- [ ] **Step 5: Run to verify PASS** — `python -m pytest tests/test_profiles.py -v` → 4 passed.
- [ ] **Step 6: Commit** — `git add config/profiles.json config/recipes tests/test_profiles.py && git commit -m "feat(profiles): six role presets + recipes + contract test"`

---

## Task 2: profiles_helpers.ps1 + /api/profiles endpoint

**Files:**
- Create: `goose_web/profiles_helpers.ps1`
- Test: `goose_web/tests/test_profiles.ps1`
- Modify: `goose_web/server.ps1`

**Interfaces:**
- Consumes: `Set-ExtensionEnabled` (mcp_toggle.ps1), `Parse-GooseExtensions`, `Send-Json`, `Read-Utf8Body`, `Get-QueryValue`, `$Shared.cfgWriteLock`, `refreshSignal`.
- Produces (in profiles_helpers.ps1, all path-parameterized for tests):
  - `Get-AgentProfiles($path)` → validated array (throws on missing/dup names/missing fields).
  - `Get-ManagedExtIds($profiles)` → string[] union of all `enable`.
  - `Get-ActiveProfileName($profiles, $extStates)` → name whose enable-set exactly matches the enabled managed ids in `$extStates` (hashtable id→[bool]), else `"custom"`.
  - `Invoke-ProfileApply($profilesPath, $configPath, $workspaceDir, $repoRoot, $name)` → `@{ ok; name; changed=@(); warnings=@() }`; throws on unknown name / unreadable recipe BEFORE any write.

- [ ] **Step 1: Write the failing test**

```powershell
# goose_web/tests/test_profiles.ps1 -- run: powershell -NoProfile -File goose_web/tests/test_profiles.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\mcp_toggle.ps1')          # Set-ExtensionEnabled dependency
. (Join-Path $here '..\profiles_helpers.ps1')
$tmp = Join-Path $env:TEMP ("prof_test_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $tmp 'recipes') | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tmp 'ws') | Out-Null
try {
    # fixture: 2 profiles over 3 managed extensions (a,b,c) + unmanaged (z)
    'RECIPE-ALPHA' | Set-Content (Join-Path $tmp 'recipes\alpha.md') -Encoding UTF8
    'RECIPE-BETA'  | Set-Content (Join-Path $tmp 'recipes\beta.md') -Encoding UTF8
    $profJson = Join-Path $tmp 'profiles.json'
    @'
[ {"name":"alpha","label":"A","description":"d","enable":["a","b"],"recipe":"recipes/alpha.md"},
  {"name":"beta","label":"B","description":"d","enable":["b","c"],"recipe":"recipes/beta.md"} ]
'@ | Set-Content $profJson -Encoding UTF8
    $cfg = Join-Path $tmp 'config.yaml'
    @'
GOOSE_PROVIDER: openai
extensions:
  a:
    type: builtin
    enabled: false
  b:
    type: builtin
    enabled: true
  c:
    type: builtin
    enabled: true
  z:
    type: builtin
    enabled: true
'@ | Set-Content $cfg -Encoding UTF8

    # 1) parse + managed set
    $profiles = Get-AgentProfiles $profJson
    if ($profiles.Count -ne 2) { throw 'parse failed' }
    $managed = Get-ManagedExtIds $profiles
    if (($managed | Sort-Object) -join ',' -ne 'a,b,c') { throw "managed set wrong: $managed" }

    # 2) active detection: current enabled managed = b,c -> beta
    $states = @{ a = $false; b = $true; c = $true; z = $true }
    if ((Get-ActiveProfileName $profiles $states) -ne 'beta') { throw 'active should be beta' }
    $states.c = $false
    if ((Get-ActiveProfileName $profiles $states) -ne 'custom') { throw 'active should be custom' }

    # 3) apply alpha: a,b enabled; c disabled; z untouched; goosehints written; backup exists
    $r = Invoke-ProfileApply $profJson $cfg (Join-Path $tmp 'ws') $tmp 'alpha'
    if (-not $r.ok) { throw 'apply failed' }
    $out = Get-Content -Raw $cfg
    if ($out -notmatch '(?ms)^  a:.*?enabled: true') { throw 'a not enabled' }
    if ($out -notmatch '(?ms)^  c:.*?enabled: false') { throw 'c not disabled' }
    if ($out -notmatch '(?ms)^  z:.*?enabled: true') { throw 'z was touched' }
    $gh = Get-Content -Raw (Join-Path $tmp 'ws\.goosehints')
    if ($gh -notmatch 'profile: alpha' -or $gh -notmatch 'RECIPE-ALPHA') { throw 'goosehints wrong' }
    if (-not (Test-Path "$cfg.bak-profile")) { throw 'backup missing' }

    # 4) unknown profile: throws, config untouched
    $before = Get-Content -Raw $cfg
    $threw = $false
    try { [void](Invoke-ProfileApply $profJson $cfg (Join-Path $tmp 'ws') $tmp 'nope') } catch { $threw = $true }
    if (-not $threw) { throw 'unknown name should throw' }
    if ((Get-Content -Raw $cfg) -ne $before) { throw 'config modified on unknown name' }

    Write-Host '[OK] profiles helpers pass' -ForegroundColor Green
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
```

- [ ] **Step 2: Run to verify FAIL** — `powershell -NoProfile -File goose_web/tests/test_profiles.ps1` → profiles_helpers.ps1 not found.

- [ ] **Step 3: Write `goose_web/profiles_helpers.ps1`**

```powershell
# profiles_helpers.ps1 -- agent-profile helpers shared by server.ps1 (folded into $DiscoveryFns like
# mcp_toggle.ps1) and the sandboxed test. Path-parameterized: no live-path constants here.
# Depends on Set-ExtensionEnabled (mcp_toggle.ps1) being in scope for Invoke-ProfileApply.

function Get-AgentProfiles($path) {
    if (-not (Test-Path -LiteralPath $path)) { throw "profiles.json not found: $path" }
    $arr = @((Get-Content -Raw -LiteralPath $path -Encoding UTF8 | ConvertFrom-Json))
    $seen = @{}
    foreach ($p in $arr) {
        foreach ($f in 'name', 'label', 'description', 'recipe') {
            if (-not $p.$f) { throw "profiles.json: entry missing '$f'" }
        }
        if (-not $p.enable -or @($p.enable).Count -eq 0) { throw "profile '$($p.name)': empty enable list" }
        if ($seen.ContainsKey($p.name)) { throw "profiles.json: duplicate name '$($p.name)'" }
        $seen[$p.name] = $true
    }
    return $arr
}

function Get-ManagedExtIds($profiles) {
    $ids = @{}
    foreach ($p in $profiles) { foreach ($i in $p.enable) { $ids[[string]$i] = $true } }
    return @($ids.Keys)
}

function Get-ActiveProfileName($profiles, $extStates) {
    # $extStates: hashtable id -> [bool]enabled (only managed ids are compared)
    $managed = Get-ManagedExtIds $profiles
    $enabledManaged = @($managed | Where-Object { $extStates.ContainsKey($_) -and $extStates[$_] }) | Sort-Object
    foreach ($p in $profiles) {
        $want = @($p.enable | Sort-Object)
        if (($enabledManaged -join "`n") -eq ($want -join "`n")) { return [string]$p.name }
    }
    return 'custom'
}

function Invoke-ProfileApply($profilesPath, $configPath, $workspaceDir, $repoRoot, $name) {
    # validate EVERYTHING before any write
    $profiles = Get-AgentProfiles $profilesPath
    $prof = $profiles | Where-Object { $_.name -eq $name } | Select-Object -First 1
    if ($null -eq $prof) { throw "unknown profile: $name" }
    $recipePath = Join-Path $repoRoot ([string]$prof.recipe -replace '/', '\')
    if (-not (Test-Path -LiteralPath $recipePath)) { throw "recipe not found: $($prof.recipe)" }
    $recipe = Get-Content -Raw -LiteralPath $recipePath -Encoding UTF8
    if (-not (Test-Path -LiteralPath $configPath)) { throw "goose config not found: $configPath" }

    Copy-Item -LiteralPath $configPath -Destination "$configPath.bak-profile" -Force

    $enable = @{}; foreach ($i in $prof.enable) { $enable[[string]$i] = $true }
    $changed = @(); $warnings = @()
    foreach ($id in (Get-ManagedExtIds $profiles)) {
        $want = $enable.ContainsKey($id)
        try {
            if (Set-ExtensionEnabled $configPath $id $want) { $changed += $id }
        } catch {
            $warnings += "skip ${id}: $_"     # extension absent from this config -- not fatal
        }
    }

    try {
        $header = "# profile: $name -- generated by goose_web /api/profiles; do not edit (reapply the profile instead)"
        $utf8 = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllText((Join-Path $workspaceDir '.goosehints'),
                                       ($header + "`n`n" + $recipe), $utf8)
    } catch {
        $warnings += "goosehints write failed: $_"
    }
    return @{ ok = $true; name = [string]$name; changed = $changed; warnings = $warnings }
}
```

- [ ] **Step 4: Run to verify PASS** — `[OK] profiles helpers pass`.

- [ ] **Step 5: Wire into `server.ps1`.**
  1. Fold the helpers into `$DiscoveryFns` beside the existing folds (after the `$ToggleFns`/`$EncodingFns` block):

```powershell
$ProfilesFns = ''
try { $ProfilesFns = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $Here 'profiles_helpers.ps1') } catch { Write-Warning "[goose_web] could not load profiles_helpers.ps1: $_" }
$DiscoveryFns = $DiscoveryFns + "`n" + $ProfilesFns
```

  2. Add to the `$S` state bundle: `profilesPath = (Join-Path $Here '..\config\profiles.json'); repoRoot = (Split-Path -Parent $Here)`.
  3. Add `Handle-Profiles` in the worker block (near `Handle-Toggle`):

```powershell
function Handle-Profiles($ctx, $S) {
    if ($S.token) {
        $sup = $ctx.Request.Headers['X-Goose-Token']; if (-not $sup) { $sup = Get-QueryValue $ctx.Request.Url.Query 'token' }
        if ($sup -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
    }
    try { $profiles = Get-AgentProfiles $S.profilesPath }
    catch { Send-Json $ctx @{ error = [string]$_ } 500; return }

    if ($ctx.Request.HttpMethod -eq 'GET') {
        $states = @{}
        foreach ($e in (Parse-GooseExtensions $S.gooseConfig)) { $states[$e.id] = [bool]$e.enabled }
        $list = @($profiles | ForEach-Object {
            @{ name = $_.name; label = $_.label; description = $_.description; enable = @($_.enable) } })
        Send-Json $ctx @{ ok = $true; profiles = $list; active = (Get-ActiveProfileName $profiles $states) }
        return
    }
    $req = $null; $bt = Read-Utf8Body $ctx
    try { if ($bt.Trim()) { $req = $bt | ConvertFrom-Json } } catch {}
    if ($null -eq $req -or $req.action -ne 'apply' -or -not $req.name) {
        Send-Json $ctx @{ error = 'action "apply" and name required' } 400; return
    }
    $result = $null; $err = $null
    [System.Threading.Monitor]::Enter($S.shared.cfgWriteLock)
    try { $result = Invoke-ProfileApply $S.profilesPath $S.gooseConfig $S.workspace $S.repoRoot ([string]$req.name) }
    catch { $err = [string]$_ }
    finally { [System.Threading.Monitor]::Exit($S.shared.cfgWriteLock) }
    if ($err) { Send-Json $ctx @{ error = $err } 400; return }
    # update the sidebar snapshot in place (parity with Handle-Toggle) then wake the discoverer
    $shared = $S.shared
    $enable = @{}
    $prof = $profiles | Where-Object { $_.name -eq $req.name } | Select-Object -First 1
    foreach ($i in $prof.enable) { $enable[[string]$i] = $true }
    [System.Threading.Monitor]::Enter($shared.SyncRoot)
    try {
        foreach ($x in $shared.exts) {
            if ($result.changed -notcontains $x.id) { continue }
            $x.enabled = $enable.ContainsKey($x.id)
            $x.count = 0
            $x.status = if ($x.enabled) { 'checking' } else { 'disabled' }
        }
        $shared.tools = @($shared.tools | Where-Object { $result.changed -notcontains $_.group })
    } finally { [System.Threading.Monitor]::Exit($shared.SyncRoot) }
    [void]$shared.refreshSignal.Set()
    Send-Json $ctx @{ ok = $true; name = $result.name; changed = @($result.changed); warnings = @($result.warnings) }
}
```

  4. Routes in `Handle-Request`: GET branch `elseif ($path -eq '/api/profiles') { Handle-Profiles $ctx $S }`; POST branch `elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/profiles') { Handle-Profiles $ctx $S }`.

- [ ] **Step 6: Parse check + rerun tests** — `[ScriptBlock]::Create` on server.ps1 → parses; helper test still green; scheduler helper test (`goose_web/tests/test_schedules.ps1`) still green (no regression in the fold area).

- [ ] **Step 7: Commit** — `git add goose_web/profiles_helpers.ps1 goose_web/tests/test_profiles.ps1 goose_web/server.ps1 && git commit -m "feat(goose_web): /api/profiles batch role apply"`

---

## Task 3: sidebar role switcher UI

**Files:**
- Modify: `goose_web/index.html`

**Interfaces:**
- Consumes: `GET/POST /api/profiles` (Task 2), existing `$`, `el`, `esc`, `authHeaders`, `loadHealth`, `loadSchedules` polling section.
- Produces JS: `PROF` (state), `loadProfilesUI()`, `renderProfilesUI()`, `applyProfile(name)`; hero shows the active label+description.

- [ ] **Step 1: Sidebar section.** BEFORE the `MCP 伺服器`/`#servers` section (inside `aside.nav`), add:

```html
<div class="sect" id="profSect">
  <div class="sect-h">角色 <span class="badge" id="profBadge">—</span></div>
  <select id="profSelect" class="prof-select"></select>
  <div class="prof-desc" id="profDesc"></div>
</div>
```

- [ ] **Step 2: JS.** Near `loadSchedules()` add (ALL inside the existing `<script>` scope — remember the drawer lesson: every referenced element must appear in the DOM before `<script>`):

```javascript
let PROF={profiles:[],active:""};
async function loadProfilesUI(){
  try{const r=await fetch("/api/profiles",{headers:authHeaders()});const j=await r.json();
      if(j.ok){PROF={profiles:j.profiles||[],active:j.active||""};}}
  catch(e){}
  renderProfilesUI();
}
function renderProfilesUI(){
  const sel=$("#profSelect");if(!sel)return;
  const cur=PROF.active;
  sel.innerHTML=PROF.profiles.map(p=>'<option value="'+esc(p.name)+'"'+(p.name===cur?" selected":"")+'>'+esc(p.label)+'</option>').join("")+
    (cur==="custom"?'<option value="custom" selected>自訂</option>':"");
  const p=PROF.profiles.find(x=>x.name===cur);
  $("#profBadge").textContent=p?p.enable.length+" MCP":"—";
  $("#profDesc").textContent=p?p.description:(cur==="custom"?"目前啟用組合不符任何預設":"");
}
async function applyProfile(name){
  if(name==="custom")return;
  const sel=$("#profSelect");sel.disabled=true;
  try{const r=await fetch("/api/profiles",{method:"POST",headers:authHeaders({"Content-Type":"application/json"}),
        body:JSON.stringify({action:"apply",name})});
      const j=await r.json();if(!r.ok||j.error)alert("套用失敗："+(j.error||r.status));}
  finally{sel.disabled=false;await loadProfilesUI();await loadHealth();}
}
```

- [ ] **Step 3: Wiring + polling.** In the init/binding section: `$("#profSelect").onchange=()=>applyProfile($("#profSelect").value);` and add `loadProfilesUI();` beside the existing `loadHealth();` call plus `setInterval(loadProfilesUI,30000);`.

- [ ] **Step 4: Hero.** In `showHero()`, append the active role to the hero paragraph: after the existing `<p>…</p>` construction, add

```javascript
const hp=PROF.profiles.find(x=>x.name===PROF.active);
if(hp) h.querySelector("p").innerHTML+=' · <b>'+esc(hp.label)+'</b>：'+esc(hp.description);
```

- [ ] **Step 5: CSS.** In the `<style>` block (match dark theme):

```css
.prof-select{width:100%;background:var(--panel,#1b1b1b);color:inherit;border:1px solid var(--line,#2a2a2a);border-radius:8px;padding:6px 8px;font-size:13px}
.prof-desc{font-size:11px;opacity:.75;margin-top:4px;line-height:1.5}
```

- [ ] **Step 6: Static checks** — every new id (`profSect profSelect profBadge profDesc`) appears exactly once and BEFORE `<script>`; functions `loadProfilesUI renderProfilesUI applyProfile` each defined once; script scope intact.

- [ ] **Step 7: Commit** — `git add goose_web/index.html && git commit -m "feat(goose_web): sidebar role switcher + hero role line"`

---

## Task 4: docs + A/B question set

**Files:**
- Modify: `goose_web/README.md`, `mcp/README.md`
- Create: `docs/profile_ab_questions.md`

- [ ] **Step 1: `goose_web/README.md`** — new section: `/api/profiles` (GET shape, POST apply, token auth, builtin-flip policy: allowed here / still 403 on per-card toggle), the sidebar switcher, and the `.goosehints` generation (header line, do-not-edit note).
- [ ] **Step 2: `mcp/README.md`** — short "Agent profiles" note pointing at `config/profiles.json` + `config/recipes/` and the fact that profiles scope goose's view only (servers/watchdog untouched).
- [ ] **Step 3: `docs/profile_ab_questions.md`** — the 10 fixed diagnostic questions for the B-vs-A test, each tagged with its expected tool family, e.g.:

```markdown
# Profile A/B 對測題組（diag vs perf+sec）

每題在 `diag` 下跑一次、在對應的 A profile（perf 或 sec）下跑一次；記錄：選用工具、
是否選對家族、答案是否引用工具數據、總輪數。10 題全跑完後統計選錯率與品質差。

1. 這台電腦最近開機後特別慢，找出最可能的原因。（期望：perfmon/disk/memstate；A=perf）
2. 昨天有沒有發生過應用程式崩潰？哪個程式？（期望：crash；A=sec）
3. 最近一週誰用掉最多網路流量？（期望：srum；A=perf）
4. 檢查有沒有可疑的對外連線正在進行。（期望：netconn；A=sec）
5. C 槽最近健康狀況如何，SMART 有沒有警告？（期望：disk；A=perf）
6. 系統的自啟動項最近有沒有變化？（期望：drift；A=sec）
7. 記憶體使用是否正常，有沒有洩漏跡象？（期望：memstate/perfmon；A=perf）
8. 查一下 notepad.exe（或任一程式）最近有沒有被執行過。（期望：exec；A=sec）
9. Windows Update 有沒有失敗的更新或待重開機？（期望：winupdate；A=perf）
10. 有沒有哪個行程鎖住了某個檔案導致無法刪除？（期望：procinspect；A=perf）
```

- [ ] **Step 4: Commit** — `git add goose_web/README.md mcp/README.md docs/profile_ab_questions.md && git commit -m "docs(profiles): endpoint/UI docs + A/B question set"`

---

## Post-merge deployment (manual)

1. Merge to main; restart goose_web (`:8799`) so the new endpoint/UI load — **requires explicit user confirmation** (standing rule: never restart the live goose_web unasked).
2. Click through the six profiles; confirm sidebar card states, hero line, and `workspace/.goosehints` content follow.
3. Run the A/B question set (user participates for quality judgment); tally per `docs/profile_ab_questions.md`.

## Self-Review Notes

- Spec coverage: profiles.json contract + 6 presets (Task 1), recipes → .goosehints with header (Tasks 1-2), apply path validate-first/backup/lock/refresh + builtin policy + toggle endpoint untouched (Task 2), active="custom" detection (Task 2), sidebar switcher + gray cards + hero (Task 3), token auth parity (Task 2), sandboxed tests (Tasks 1-2), docs + A/B set (Task 4), error handling (unknown name → throw before write; goosehints failure → warning not rollback — both tested).
- Type consistency: `Invoke-ProfileApply($profilesPath,$configPath,$workspaceDir,$repoRoot,$name)` matches test invocation and Handle-Profiles call (`$S.profilesPath $S.gooseConfig $S.workspace $S.repoRoot`); `Get-ActiveProfileName($profiles,$extStates)` hashtable contract matches both call sites; JS `PROF.profiles[].{name,label,description,enable}` matches the GET payload built in Handle-Profiles.
- The A-plan fallback ships in the same data file (perf/sec presets), satisfying the zero-cost-fallback requirement.
