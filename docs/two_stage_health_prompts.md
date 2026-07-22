# 兩段式健康檢查 Prompt（小模型用）

Diagnostics（diag）合併模式一次載入 12 個家族、~90 工具，對小模型（16GB 單卡、qwen3.6 級）負擔大，
且 A/B 對測顯示**開放式問題在鑑識類會系統性選錯家族**（見 `profile_ab_results.md`）。因此把完整健康
檢查拆成兩段、各用一個窄範圍角色跑，並用**逐家族的處方式 prompt**（直接指定每步用哪個家族，不讓模型自己
路由）——這正是繞過小模型鑑識路由弱點的關鍵。

## 為什麼這樣拆

- 兩個 profile 的工具剛好互補，且**都有 `developer`**（可讀寫檔案）→ 用 workspace 檔案交棒。
- 第一段（效能）發現的可疑項目寫進 `workspace\health_stage1.md`，第二段（鑑識）讀回來針對性深查。
- 交棒必須走檔案而非 memory：`Forensics` profile 沒有 memory extension，但兩者都有 developer。

| 段 | 角色 (profile) | 涵蓋家族 |
|---|---|---|
| 第一段：效能 | **Performance** | perfmon · disk · memstate · procinspect · winupdate · srum |
| 第二段：鑑識 | **Forensics** | eventlog · crash · exec · drift · netconn · filterstack |

## 操作流程

1. 側欄角色下拉切 **Performance** → 貼「第一段」prompt → 跑完產出 `health_stage1.md`
2. 側欄角色下拉切 **Forensics** → 貼「第二段」prompt → 讀回檔案、接力鑑識、給最終綜合結論

---

## 第一段 — 貼在 Performance 角色

```
執行完整的效能健康檢查。依序檢查每個家族，每個家族先呼叫該 MCP 的 health 工具確認在線再查：

1. 即時效能 (perfmon)：CPU、磁碟延遲、記憶體壓力有無瓶頸
2. 磁碟健康 (disk)：C 槽 SMART 狀態、有無警告、近期大量檔案變更
3. 記憶體歸因 (memstate)：使用是否正常、有無 pool 洩漏跡象
4. 行程 (procinspect)：有無異常吃資源、hang、或 handle 洩漏的行程
5. 更新 (winupdate)：有無失敗的更新或待重開機
6. 用量歸因 (srum)：近期 CPU／網路／耗電的主要來源

每一項給「正常／注意／異常」判定，並引用具體數據（數字、行程名、時間戳）。

最後把「需要進一步鑑識追查的項目」寫進 workspace\health_stage1.md，格式：
- 可疑行程：<名稱/PID>（原因）
- 異常時間點：<時間>（現象）
- 待查連線／來源：<描述>
若一切正常就寫「無異常，無需鑑識追查」。
```

## 第二段 — 貼在 Forensics 角色

```
先讀取 workspace\health_stage1.md 取得第一階段（效能）標記的待查項目，接著執行安全鑑識檢查，並針對那些待查項目深入追查。依序檢查（各家族先 health 再查）：

1. 事件記錄 (eventlog)：近期系統／應用錯誤，特別對照 stage1 標記的異常時間點
2. 崩潰分析 (crash)：有無應用崩潰／BSOD，對照可疑行程
3. 執行痕跡 (exec)：stage1 標記的可疑行程近期執行歷史（Prefetch／BAM／UserAssist）
4. 設定漂移 (drift)：自啟動項／服務近期有無異動
5. 對外連線 (netconn)：有無可疑連線及其擁有者行程，對照 stage1 的待查來源
6. 濾網疊層 (filterstack)：有無異常的防毒／VPN 驅動干擾

每一項給判定並引用證據（時間戳＋路徑＋行程）。最後綜合 stage1＋stage2 給一份完整健康結論：系統整體狀態、已確認的問題、建議處理步驟。
```

---

## 設計要點

- **處方式 > 開放式**：A/B 顯示小模型對「查一下有沒有可疑連線」這種開放問句會選錯家族（挑到 procinspect/
  srum 而非 netconn）。上面的 prompt 直接編號指定每步用哪個家族，把路由決策從模型手上拿掉。
- **每段 ~40 工具**（合併模式的一半），選錯率大幅下降；效能段在 A/B 已驗證窄範圍 6/6 全對。
- **health-first**：每家族先 health，符合各角色 recipe 的規則，也能及早發現離線的 MCP。
- **檔案交棒**讓兩段組成一份完整報告，不漏跨領域關聯（效能段的可疑行程 → 鑑識段查它的執行痕跡）。
