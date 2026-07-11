# Windows 診斷 Playbook — 症狀 → 工具 → prompt

> 這台機器掛了 **12 個本機診斷 MCP**(`mcp/windows_*/`,port 8777–8788),goose 以 `GOOSE_MODE=auto`
> 執行,可自行呼叫這些工具。本手冊把「常見症狀 ↔ 該用哪個工具 ↔ 實際呼叫 ↔ 給 goose 的自然語言
> prompt」對應清楚,直接複製 prompt 到 goose_web(`:8799`)或 `goose run` 即可。
>
> 工具全貌見 [`../mcp/README.md`](../mcp/README.md);每個 MCP 的細節見各自的 `README.md` / `DESIGN.md`。

---

## 0. 快速開始

```powershell
# 確認 12 個 server 都在(或用 setup 一鍵起)
powershell -ExecutionPolicy Bypass -File ..\setup_mcp_servers.ps1   # elevated;冪等
```
然後在 goose_web 或 `goose run --no-session -t "<prompt>"` 下 prompt。agent 會自己挑工具。

### 一鍵綜合健檢(最常用)
> **幫這台 Windows 做一次系統健檢:①handle/記憶體有沒有洩漏(哪個 process/driver)、②哪些第三方
> AV/VPN driver 掛在檔案與網路路徑上、③最近有沒有更新失敗或當機、④哪些目錄在狂寫、⑤現在的效能
> 瓶頸是什麼。用你有的診斷工具查,最後列出可疑項目與對應的 process/driver。**

agent 會依序打 `top_handle_users` / `pool_tags` / `minifilters` / `update_history` / `directory_churn` /
`bottleneck` 等再彙整。

---

## 1. 症狀 → 工具 → prompt 對照表

### 🧠 記憶體 / handle 洩漏
| 症狀 | 工具(呼叫) | goose prompt |
|---|---|---|
| 某程序 handle 越來越多 | `procinspect.top_handle_users(n=10)` | 「哪些 process 的 handle 數異常高?列前 10 名,判斷有沒有 handle 洩漏。」 |
| 「32GB 記憶體去哪了」 | `memstate.memory_overview()` + `memstate.pool_tags()` | 「實體記憶體、commit、kernel pool 現況?哪個 pool tag 吃最多 nonpaged?」 |
| kernel pool 一直長 | `memstate.pool_tags(sort_by="nonpaged")` → `memstate.tag_driver("<tag>")` | 「哪個 pool tag 佔最多 nonpaged pool?它是哪個 driver 佔用的?」 |
| 是不是真的在漏(趨勢) | `memstate.baseline_save` → 隔天 `memstate.baseline_diff` | 「先存一個 pool tag 基線。」→(過一陣子)「跟基線比,哪些 tag 長最多?」 |
| 實體記憶體組成(standby/free) | `memstate.memory_composition()` | 「實體記憶體的 standby / modified / free / zeroed 各多少 GB?」 |

### ⚙️ CPU / 即時效能 / 瓶頸
| 症狀 | 工具 | prompt |
|---|---|---|
| 「現在為什麼卡」 | `perfmon.bottleneck()` | 「現在的效能瓶頸是 CPU、磁碟延遲、記憶體還是換頁?」 |
| 磁碟很慢 | `perfmon.snapshot()`(看 `disk_sec_per_transfer`) | 「現在磁碟延遲(Avg Disk sec/Transfer)是多少?正常應 <25ms。」 |
| 歷史上哪個 app 吃資源 | `srum.srum_app_usage(hours=48)` | 「過去 48 小時哪些 app 吃最多 CPU / 讀寫最多?列前 5。」 |
| 現在誰在吃 CPU/記憶體 | `srum.top_processes(by="cpu")` | 「現在 CPU / 記憶體用最多的前 10 個 process?」 |

### 💾 磁碟 / 檔案
| 症狀 | 工具 | prompt |
|---|---|---|
| 「當機前哪些檔案變了」 | `disk.recent_file_changes(minutes=60)` | 「過去 60 分鐘哪些檔案被建立/刪除/改寫?」 |
| 哪個目錄在狂寫 | `disk.directory_churn(minutes=30)` | 「最近 30 分鐘哪些目錄檔案變動最頻繁?列前幾名。」 |
| 硬碟是不是要壞了 | `disk.disk_health()` | 「硬碟健康如何?wear %、溫度、read/write error、通電時數。」 |
| Volume 是否 dirty / 待 chkdsk | `disk.volume_state()` | 「C: 有沒有 dirty bit(待 chkdsk)?VSS 影本幾份?剩多少空間?」 |

### 🌐 網路
| 症狀 | 工具 | prompt |
|---|---|---|
| 每個連線是誰的 | `netconn.connections()` / `netconn.listeners()` | 「現在有哪些監聽的 port,各是哪個 process/service?」 |
| svchost 裡是哪個服務在連外 | `netconn.connections(process="svchost")` | 「svchost 開的連線各屬於哪個 hosted service?」 |
| port 耗盡 / 連線洩漏 | `netconn.connection_stats()` | 「連線狀態統計:哪個 process 有大量 TIME_WAIT / CLOSE_WAIT?ephemeral port 用了多少?」 |
| 有沒有可疑連線(beaconing) | `netconn.baseline_save` → `netconn.baseline_diff` | 「存一個連線基線。」→「跟基線比,多了哪些新的監聽或外連?」 |

### 💥 當機 / 掛死
| 症狀 | 工具 | prompt |
|---|---|---|
| 最近什麼在當 | `crash.crash_summary(days=30)` | 「過去 30 天有什麼在當機/沒回應?按 app+模組分組。」 |
| BSOD 是哪個 driver | `crash.list_dumps()` → `crash.analyze_dump(path)` | 「有沒有 BSOD dump?分析最新的一個,是哪個 bugcheck / driver?」 |
| 某程式凍住在等什麼 | `procinspect.wait_chain(pid=<pid>)` | 「PID X 為什麼沒回應?它在等什麼?有沒有 deadlock?」 |
| 檔案刪不掉/更新不了 | `procinspect.who_locks(path)` | 「哪個 process 鎖住了 `C:\...\某檔案`?」 |

### 🔄 Windows Update / 設定變更
| 症狀 | 工具 | prompt |
|---|---|---|
| 問題是不是某次更新後開始的 | `winupdate.update_history(max=20)` | 「最近裝了哪些 Windows Update?列日期/KB/結果。」 |
| 更新一直失敗 | `winupdate.update_history(failures_only=True)` | 「有哪些更新失敗?失敗代碼(HRESULT)是什麼意思?」 |
| 卡在待重開機 | `winupdate.pending_state()` | 「有沒有卡住的更新(待重開機 / pending file rename)?目前 build 版本?」 |
| 開機後多了什麼自啟項/服務 | `drift.what_changed_since("<日期>")` | 「跟 <某日期> 比,autoruns/服務/程式/排程有什麼變化?」 |
| 建立「好的時候」基線 | `drift.snapshot_now(note="clean")` | 「現在做一個系統設定快照,備註 clean baseline。」 |

### 🛡️ AV / VPN / filter driver(整台機器很慢的頭號原因)
| 症狀 | 工具 | prompt |
|---|---|---|
| 什麼掛在每次檔案操作上 | `filterstack.minifilters(third_party_only=True)` | 「有哪些第三方 AV/VPN 的 filesystem minifilter 掛在檔案操作上?名稱、altitude 分類、廠商。」 |
| 網路過濾器 | `filterstack.network_filters()` | 「有哪些第三方 NDIS / Winsock 網路過濾器?」 |
| 程式被注入了什麼 DLL | `procinspect.loaded_modules(pid, filter=".dll")` | 「PID X 載入了哪些 DLL?有沒有被 AV/第三方注入的模組?」 |

### 🕵️ 執行痕跡(「這東西到底跑過沒 / 何時跑的」)
| 症狀 | 工具 | prompt |
|---|---|---|
| 某 exe 何時啟動、跑幾次 | `exec.prefetch_list(filter="<name>")` | 「<某程式> 最近的啟動時間和執行次數?」 |
| 凌晨某時段跑了什麼 | `exec.exec_timeline(hours=24)` | 「過去 24 小時的執行時間軸,某時段跑了哪些程式?」 |
| 使用者實際用了什麼 | `exec.userassist_list()` / `exec.bam_list()` | 「各使用者最近執行過哪些程式?」 |

---

## 2. 本機實測範例(這台機器真的抓到的東西)

這些**不是靠某個 prompt 一次抓出來的** —— 是逐一驗證每個 MCP 時,資料自己浮出來的。可照下面重現:

1. **CGServiSign 系列 ~197 萬 handle/程序(全系統 627 萬)**
   `procinspect.top_handle_users(n=4)` → `CGServiSignMonitor.exe` / `CGServiSign.exe` /
   `CGServiSignKeeper.exe` 各 ~1.97M handle。全系統 `memstate.memory_overview().handles ≈ 6.27M`。
   → 這個數字**明顯不合理**,是本機唯一比較確定的異常。

2. **Intel 儲存驅動 `ismc` 佔 350MB nonpaged pool**
   `memstate.pool_tags(sort_by="nonpaged")` → `ismc` 350.9MB 居冠;`memstate.tag_driver("ismc")`
   → `iaStorAVC.sys` / `iaStorVD.sys`(Intel RST/VMD 儲存驅動)。可能是正常用量,需看趨勢。

3. **Trend Micro + NordVPN 的 Anti-Virus 類 minifilter**
   `filterstack.minifilters(third_party_only=True)` → `tmeyes`(Trend Micro,altitude 328520)、
   `mshield`(NordVPN,323850)—— 兩個都在 **FSFilter Anti-Virus** altitude 帶,坐在每次檔案開啟上
   (檔案 IO 變慢的常見來源)。NordVPN 竟裝了 AV 類檔案掃描過濾器。

4. **tmmon64.dll 注入 + 目錄 churning**
   `procinspect.loaded_modules(pid, filter=".dll")` → 一般 process 內出現 Trend Micro 的 `tmmon64.dll`。
   `disk.directory_churn(minutes=30)` → `Trend Micro\AMSP\temp\virus`(889 次)、NordVPN、Claude sentry、
   Chrome cache、Prefetch 在狂寫。

---

## 3. 方法論:現象 ≠ 故障 —— 趨勢才是鐵證

單一快照看到的多半是「現象」,不一定是「故障」:
- AV minifilter 掛在 Anti-Virus altitude 是**正常設計**(AV 本來就該在那);
- `ismc` 350MB pool 可能是 Intel RST 的正常用量;
- 某 process 的 CLOSE_WAIT 可能只是短暫的。

**判斷「是不是在漏 / 在惡化」要看趨勢** —— 這就是每個 MCP 都內建 `baseline_save` / `baseline_diff`
的原因:
```
現在存基線 → 過一段時間再 diff → 「handle / pool / filter / 連線 一直單調成長」才是洩漏鐵證
```
支援基線的工具:`memstate`(pool tag)、`netconn`(連線/監聽)、`perfmon`(計數器)、
`disk`(SMART 健康)、`drift`(整個系統設定)、`filterstack`(minifilter 集合)。

> prompt 範例:「現在存一個 pool tag 基線和連線基線。」→(隔天)「跟昨天的基線比,pool tag 和連線
> 各長了什麼?有沒有單調成長的洩漏跡象?」

---

## 4. 跨工具關聯 —— 根因定位

單一工具看現象,**跨工具對照**才定位根因。幾條常用路徑:

**A. 「某 app 一直吃資源」**
```
srum.srum_app_usage(找出吃 CPU 的 app)
  → exec.prefetch_list / bam_list(它何時啟動、跑幾次)
  → procinspect.process_detail(它的 handle/thread/記憶體)
  → filterstack.minifilters(是不是 AV 在掃它的檔案拖慢)
  → winupdate.update_history(問題是不是某次更新後開始的)
```

**B. 「整台機器變慢」**
```
perfmon.bottleneck(先定位是 CPU / 磁碟 / 記憶體)
  → 若磁碟:disk.directory_churn(誰在狂寫)+ filterstack.minifilters(AV 掃描拖慢 IO)
  → 若記憶體:memstate.pool_tags + tag_driver(哪個 driver 漏)
  → 若 handle/資源:procinspect.top_handle_users
```

**C. 「開機/更新後出問題」**
```
winupdate.update_history(問題時間點附近裝了什麼)
  → drift.what_changed_since(那天前後 服務/driver/自啟項 變了什麼)
  → crash.crash_summary(當機是不是同時開始)
  → eventlog.error_summary(系統錯誤是不是同時出現)
```

**D. 「檔案/程式行為可疑」**
```
disk.recent_file_changes(改了什麼)
  → exec.exec_timeline(那時段跑了什麼)
  → procinspect.loaded_modules(有沒有被注入 DLL)
  → netconn.by_remote(有沒有可疑外連)
```

---

## 5. 12 個 MCP 速查

| MCP | port | 主力工具 |
|---|---|---|
| `srum` | 8777 | `srum_app_usage` `srum_network_usage` `top_processes` `live_snapshot` |
| `eventlog` | 8778 | `error_summary` `query_events`(可查 Sysmon 通道)`user_activity` |
| `crash` | 8779 | `crash_summary` `analyze_dump` `list_dumps` |
| `exec` | 8780 | `prefetch_list` `exec_timeline` `bam_list` `userassist_list` |
| `drift` | 8781 | `snapshot_now` `what_changed_since` `diff` `current` |
| `netconn` | 8782 | `connections` `listeners` `connection_stats` `by_remote` `baseline_diff` |
| `perfmon` | 8783 | `bottleneck` `snapshot` `baseline_diff` |
| `disk` | 8784 | `recent_file_changes` `directory_churn` `disk_health` `volume_state` |
| `procinspect` | 8785 | `who_locks` `wait_chain` `top_handle_users` `loaded_modules` `process_detail` |
| `memstate` | 8786 | `pool_tags` `tag_driver` `memory_overview` `memory_composition` `baseline_diff` |
| `filterstack` | 8787 | `minifilters` `network_filters` `filter_instances` |
| `winupdate` | 8788 | `update_history` `pending_state` `hresult_decode` `installed_updates` |

> Sysmon 已裝,增強了 `eventlog`:`query_events(channel="Microsoft-Windows-Sysmon/Operational",
> event_ids=[1])`(process 建立含 hash/命令列/父程序)、`[3]`(網路)、`[6]`(driver load)。

---

## 6. 附註

- 全部工具**唯讀**:不會 kill process、不改設定、不裝/移除更新;唯一寫入是各自的 JSON/SQLite 基線。
- 多數 server 需 **admin**(讀 SYSTEM hive / kernel 資料);開機排程已用 elevated 啟動。
- 這些工具**輔助判斷**,不自動下結論 —— 尤其「現象 vs 故障」要靠趨勢與跨工具對照(見 §3、§4)。
