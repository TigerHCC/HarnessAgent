# 安裝計畫 — Goose Agent Harness(Windows 11 本機)

> 目標:在**這台 Windows 11 開發機**安裝 [Goose](https://github.com/aaif-goose/goose)(Block 開源、Apache-2.0 的 on-machine AI agent harness),模型後端指向 **GB10 `192.168.86.44`** 的 Ollama(`:11434`)/ vLLM(`:8000`),完成冒煙測試即算交付。
> GB10 / DGX Spark 是另一台 **Linux-only(aarch64)** 機器,作為**本地模型伺服器**(已實測可達);Goose 只裝在 Windows,透過區網指向它。
>
> **專案邊界**:這是一個獨立的新嘗試,所有檔案與 code 都放在 **`HarnessAgent/`** 資料夾。**不修改** `PersonalKnowledge-GB10`;對該 repo 的任何引用(`config.yaml`、`harness_eng_plan.md`、`src/tools/` 等)僅為**唯讀參考 / 未來選配整合**,不會動到它。
>
> 研究 + 獨立查證日期:**2026-06-28**。所有版本/URL 以當日核對為準;Goose 治理近期由 `block/goose` 搬到 `aaif-goose/goose`(Linux Foundation / Agentic AI Foundation),安裝前請再核對 [Releases](https://github.com/aaif-goose/goose/releases) 最新 URL。

---

## 0. 決策摘要(已與你確認)

| 項目 | 決定 | 備註 |
|---|---|---|
| Harness | **Goose** | 即裝即用、MCP 原生、`goose bench` 內建評估、recipes/subagents |
| 目標機器 | **這台 Windows 11** | 非 GB10(GB10 無 Windows 支援) |
| 安裝面 | **CLI 為主**,Desktop 選配 | CLI 才能 recipes / bench / 腳本化,貼合你的 harness engineering |
| 安裝方式 | **Windows 原生**(官方建議),WSL2 為備援 | 原生若 shell/MCP 工具不穩再退 WSL2 |
| 模型後端 | **GB10 上的模型 `192.168.86.44`**(同時有 Ollama `:11434` 與 vLLM `:8000`) | 解耦架構:Goose 在 Windows、模型在 GB10。預設先用 **Ollama** 驗通,再視需要切 **vLLM**(高並發);雲端備援 `claude-haiku-4-5` |

---

## 1. 範圍

**範圍內(本次交付)**
1. 安裝 Goose CLI(+ 選配 Desktop)於 Windows 11
2. 設定模型後端指向現有 Ollama 端點與模型
3. 確認內建 `developer` extension 可用,並掛一個範例 MCP extension 驗證 MCP 串接
4. 冒煙測試(版本、互動問答、執行一個小任務),選配跑一次 `goose bench`

**範圍外(列為後續,見 §6,不在本次安裝)**
- 把 PK/DTM 的 `kb_search` / `dtm_search` 等工具包成 MCP server 深度整合
- 用 recipes/subrecipes 實作 sprint-contract、把 `goose bench` 接進 ratchet 流程
- 在 GB10 上部署任何東西

---

## 2. 前置需求 / 預檢

> 每一步先「檢查」,過了才動作。

- [ ] **OS**:Windows 11(✓ 已確認)
- [ ] **PowerShell** 可用(以系統管理員或一般使用者皆可,安裝到使用者目錄不需提權)
- [ ] **能連到 GB10 模型後端**(`192.168.86.44`):從這台 Windows 確認兩個端點都通,並記下實際的模型名稱
  ```powershell
  # Ollama:預期 JSON,記下 models[].name(例如 qwen3.5-rag-8g-dell)
  Invoke-RestMethod http://192.168.86.44:11434/api/tags | ConvertTo-Json -Depth 5

  # vLLM(OpenAI 相容):預期 JSON,記下 data[].id(served-model-name)
  Invoke-RestMethod http://192.168.86.44:8000/v1/models | ConvertTo-Json -Depth 5
  ```
  - ✅ **2026-06-28 實測可達**。GB10 Ollama 現有:`qwen3.6:35b`、`nemotron3:33b`、`qwen3.5:122b`、`granite4.1:30b`、`gemma4:31b`、`qwen3.6:latest`、`qwen3.5:9b`、`qwen3-embedding:latest`;vLLM:`qwen-3.6-chat`。(註:`config.yaml` 的 `qwen3.5-rag-8g-dell` 是舊本機別名,GB10 上無此名。)
  - 若連不到:GB10 端 (a) Ollama 需以 `OLLAMA_HOST=0.0.0.0` 啟動才接受區網連線;(b) vLLM 需綁 `--host 0.0.0.0`;(c) GB10 防火牆需放行 `11434`/`8000`;(d) 兩台需在同一網段(`192.168.86.x`)。
- [ ] **網路**:能連到 `github.com` / `raw.githubusercontent.com`(下載安裝腳本)
- [ ] **風險旗標確認**:到 [Releases](https://github.com/aaif-goose/goose/releases) 確認 `stable` 標籤與下方 URL 仍有效(治理搬遷後 URL 曾變動)

---

## 3. 安裝步驟

### 3a. CLI(主要)— Windows 原生

**方式 A:Git Bash / MSYS2(官方建議,若已裝 Git for Windows 最順)**
```bash
curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash
```

**方式 B:純 PowerShell**
```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/aaif-goose/goose/main/download_cli.ps1" -OutFile "download_cli.ps1"
.\download_cli.ps1
```

**驗證**
```powershell
goose --version    # 應印出版本號(預期 ~1.39.x,2026-06 當週版本)
```
- 若 `goose` 找不到 → 把安裝目錄加入 `PATH`(安裝腳本通常會提示路徑),重開終端再試。

### 3b. Desktop(選配,圖形介面試用)
- 瀏覽器下載:`https://github.com/aaif-goose/goose/releases/download/stable/Goose-win32-x64.zip`
- 解壓 → 執行其中的 Goose 執行檔。

### 3c. 備援:WSL2(僅在原生 CLI 的 shell/MCP 工具出問題時)
```bash
# 在 WSL2 (Ubuntu) 內:
curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh | bash
```
- 注意:WSL2 內的 `localhost` 不一定等於 Windows 主機;指向 Ollama 時可能需用 Windows 主機 IP(`/etc/resolv.conf` 的 nameserver 或 `host.docker.internal`)。

---

## 4. 設定模型後端(指向 GB10 `192.168.86.44`)

> 架構:Goose 在 Windows,模型在 GB10。用 §2 預檢拿到的實際模型名稱填入下面。
> 預設先用 **Ollama** 驗通(Goose 原生 provider、最少摩擦);要高並發(多 subagent 平行)再切 **vLLM**。

### 方式 A:Ollama(預設,先驗通)
```powershell
goose configure
```
互動選單依序:
1. **Configure Providers** → 選 **Ollama**
2. **Host**:`http://192.168.86.44:11434`
3. **Model**:填 §2 `api/tags` 看到的名稱(需完全一致)。建議:
   - 預設驗通 → **`qwen3.6:35b`**(新、tool-calling 穩、22 GB)或 `nemotron3:33b`(偏 agentic)
   - 最高品質 → **`qwen3.5:122b`**(122B MoE,GB10 統一記憶體甜蜜點,decode 較慢)

### 方式 B:vLLM(OpenAI 相容,高並發 / 大模型)
```powershell
goose configure
```
1. **Configure Providers** → 選 **OpenAI**(相容端點)
2. **Base URL / Host**:`http://192.168.86.44:8000/v1`
3. **API Key**:本地服務可填任意 dummy 值(例如 `sk-local`)
4. **Model**:填 §2 `v1/models` 回傳的 `id`(served-model-name,例如 `qwen-3.6-chat`)

> vLLM 在 GB10 上對 agentic 平行呼叫吞吐較佳(研究結論);若改用 vLLM 當主力,建議之後把 Goose subagents 走這條。

**關鍵 gotcha — context length(設定在 GB10 端,不是 Windows)**
Goose 重度依賴 tool-calling,context 太小會讓 tool loop 中斷。確保有效 context ≥ **32768**:
- **Ollama(GB10 端)**:以 `OLLAMA_CONTEXT_LENGTH=32768` 啟動 Ollama,或在該模型的 Modelfile 設 `PARAMETER num_ctx 32768`。
- **vLLM(GB10 端)**:啟動參數 `--max-model-len 32768`(或更高,視顯存/統一記憶體)。

**雲端備援(GB10 離線或除錯時)**
- `goose configure` → Provider 改 **Anthropic** → model `claude-haiku-4-5-20251001`(你 `config.yaml` 已有);需設定 API key。

---

## 5. 設定第一個工具層(extension)

- [ ] 確認內建 **`developer`** extension 已啟用(預設開啟):提供 shell 執行與檔案編輯能力。
  ```powershell
  goose configure   # → Add/Toggle Extensions → 確認 developer 為 enabled
  ```
- [ ] 掛一個**範例 MCP extension** 驗證 MCP 串接可動(擇一,先求能跑通,不求功能):
  - 例如 filesystem / fetch 類的社群 MCP server(在 `goose configure` → Add Extension → Command-line (stdio) 加入)。
- [ ] (後續,非本次)把 PK/DTM 的檢索工具包成 MCP server 再掛進來 —— 見 §6。

---

## 6. 冒煙測試(本次的驗收標準)

全部通過 = 安裝完成:

- [ ] **版本**:`goose --version` 成功印出版本
- [ ] **互動問答**:`goose session`(或 Desktop 開新對話)問一題,確認用**本地模型**回應且不報連線錯誤
- [ ] **工具執行**:給一個小任務驗證 `developer` extension 能動,例如:
  > 「在 `./.goose-smoketest/` 建一個 `hello.txt`,內容寫入目前時間。」

  確認 Goose 真的建立了檔案(到該資料夾檢查)。
- [ ] **MCP**:確認 §5 掛的範例 MCP extension 能被 Goose 列出並呼叫
- [ ] (選配)**評估管線**:`goose bench --help` 可執行;有餘力跑一個最小 benchmark,確認 `goose bench` 流程能動(為 §6 的 ratchet 對接鋪路)

---

## 7. 與你的 Harness Engineering 對接(後續藍圖,非本次)

對照 `tasks/harness_eng_plan.md` 的五桶模型,Goose 能補上的對應關係:

| 你的 harness 概念 | Goose 對應機制 |
|---|---|
| Sprint Contract(寫死 definition-of-done) | **recipes / subrecipes**(YAML,可版本控管、帶參數) |
| Evaluator agent / `scripts/eval_quality.py` | **`goose bench`**(跨模型/設定基準測試) |
| Ratchet(失敗 → 永久規則) | 失敗後把規則寫進 **`.goosehints` / `AGENTS.md`**,而非改 prompt |
| 工具層(`src/tools/` 9 個工具) | 把 PK/DTM 工具包成 **MCP server**,讓 Goose 當 orchestration 層呼叫你現有的 ChromaDB RAG |
| Lifecycle hooks | Goose extension + recipe 前後置步驟 |

> 原則不變(引自 harness_eng_plan):**讓 harness 吸收教訓,prompt 保持精簡**。Goose 是「執行/編排層」,你現有的 ChromaDB 多代理 RAG 仍是知識來源層 —— 兩者透過 MCP 串接,而不是互相取代。

---

## 8. 風險與回滾

| 風險 | 緩解 |
|---|---|
| Windows 原生 CLI 的 shell/MCP 工具不穩 | 退到 **WSL2**(§3c) |
| **連不到 GB10 `192.168.86.44`** | GB10 端 Ollama 用 `OLLAMA_HOST=0.0.0.0`、vLLM 用 `--host 0.0.0.0`、防火牆放行 `11434`/`8000`、確認同網段(§2) |
| context 太小 → tool loop 壞 | **在 GB10 端**設 `OLLAMA_CONTEXT_LENGTH=32768` / Modelfile `num_ctx`,或 vLLM `--max-model-len`(§4) |
| 小模型 tool-calling 不可靠 | 用 `qwen3.6:35b` / `nemotron3:33b` / `qwen3.5:122b`(避免 `qwen3.5:9b`) |
| 安裝 URL 因治理搬遷失效 | 安裝前先核對 [Releases](https://github.com/aaif-goose/goose/releases) |
| 與既有系統衝突 | **零侵入**:Goose 是獨立 binary / Desktop app,不改動 PK 系統;移除只需刪安裝目錄與 `~/.config/goose`(或 Windows 對應設定路徑) |

**回滾**:刪除 Goose 安裝目錄 + 設定檔即可,PK/DTM 系統不受影響。

---

## 9. 來源(2026-06-28 核對)

- Goose repo(現址):https://github.com/aaif-goose/goose
- Releases:https://github.com/aaif-goose/goose/releases
- 官方文件:https://goose-docs.ai/
- Windows Desktop ZIP:https://github.com/aaif-goose/goose/releases/download/stable/Goose-win32-x64.zip
- CLI 安裝腳本:`download_cli.sh`(bash)、`download_cli.ps1`(PowerShell,raw.githubusercontent.com/aaif-goose/goose/main/)
- 本機既有模型設定來源:`config.yaml`(generation/embedding/reranker 皆 Ollama `localhost:11434`)

---

## 10. 待你確認的開放項(寫進計畫即定,可改)

1. **GB10 後端先用 Ollama 還是 vLLM?**(預設:Ollama 先驗通,再切 vLLM 跑高並發)
2. **是否也裝 Desktop?**(預設:只裝 CLI,Desktop 選配)
3. **§5 的範例 MCP extension 要用哪個?**(預設:隨意挑一個輕量的驗證即可)
4. **要不要在本次就跑 `goose bench`?**(預設:列為選配)

> 確認上述後即可開始執行;或直接告訴我「開始安裝」,我會依本計畫逐步進行並回報每步結果。
