# Windows 系統診斷:值得做成 MCP 的資料來源目錄

> 日期:2026-07-11 · 針對 goose harness(Windows 11 Pro)診斷系統問題(效能 / 當機 / 不穩 / 資源占用)
> 現有能力:`srum`(SRUM ESE db，歷史 per-app CPU/網路/能耗)、`eventlog`(Windows Event Log)、
> goose builtins:`developer`(任意 PowerShell/cmd + 檔案編輯)、`memory`、`computercontroller`
> 產生方式:6 視角平行掃描 + completeness critic(見 §附錄)

## 建置狀態(2026-07-11 更新)

**已建置並上線**(全部本機 elevated、goose_web 探索到、裝了開機排程):
- ✅ **`crash`**(8779)— WER + BSOD dump 分析(候選 #1)
- ✅ **`exec`**(8780)— Prefetch+BAM+UserAssist+ShimCache+timeline(候選 #2)
- ✅ **`perfmon`**(8783)— PDH 即時計數器 + bottleneck + 基線(候選 #3)
- ✅ **`config-drift`**(8781)— autoruns/services/programs/tasks 快照+diff(候選 #4)
- ✅ **`netconn`**(8782)— 連線 + 擁有者 process/service + 基線 diff(候選 #5)
- ✅ **`disk`**(8784)— USN 檔案變更 journal + SMART 健康 + volume 狀態(候選 #6)**← 第一梯隊完成**
- ✅ **Sysmon** — starter config 備妥(`tools/sysmon/`),餵給現有 eventlog MCP(安裝待使用者執行)

**新電腦一鍵架設**:`setup_goose.ps1`(裝 goose 本體)→ 再跑 **`setup_mcp_servers.ps1`**(elevated):裝
Python 依賴、註冊+啟動 7 個 MCP 排程、把 extension 註冊進 goose config。冪等、可重跑。Sysmon 另外手動裝
(kernel driver + EULA,見 `tools/sysmon/README.md`)。

**第一梯隊已全部建置。** (`disk` v1 涵蓋 USN journal + SMART + volume state;MFT 空間掃描留待 v2。)
**第二梯隊候選**(未建):procinspect、etwtrace、memstate、wheadecode、powerdiag、filterstack、winupdate-history。
每個新 MCP 都經 4 維度對抗式 review + verify(crash 修 18、exec+drift 修 9、netconn 修 7、perfmon 修 7 個確認問題)。

## 篩選準則

agent 已能透過 `developer` 擴充執行任何 shell/PowerShell。所以一個 MCP 包裝**只有在贏過臨時 shell 時才值得做**:

| 代號 | 準則 |
|---|---|
| (a) | 解析 shell 讀不了的二進位／專有格式(如 SRUM 的 ESE db) |
| (b) | 回傳乾淨結構化 JSON,省下模型從 console 文字硬解的 token |
| (c) | 提供策展、唯讀、安全、知道權限狀態的查詢 |
| (d) | 跨呼叫維護狀態(基線 / diff / 歷史) |
| (e) | 比每回合 spawn 重量級命令快很多、可靠很多 |

**凡是一行 PowerShell 就能搞定的,一律否決或評 value 1–2。**

---

## 🥇 第一梯隊(value 5,建議優先實作)

### 1. `crash` — WER 報告庫 + 崩潰 dump 分析
- **資料來源**:Windows Error Reporting 報告庫(每個 crash/hang/kernel-fault 報告,連沒有 Event Log 痕跡的 app 也有)
- **存取**:`C:\ProgramData\Microsoft\Windows\WER\ReportArchive` 與 `\ReportQueue`(+ per-user `%LOCALAPPDATA%\Microsoft\Windows\WER`);每個資料夾內 `Report.wer`(UTF-16 key=value:EventType、例外碼、faulting module+offset、版本、時間戳)加上附掛的 `.dmp`/`WERInternalMetadata.xml`。崩潰 dump:`C:\Windows\Minidump\*.dmp`、`MEMORY.DMP`、`LiveKernelReports\**\*.dmp`(二進位 MDMP/DUMP_HEADER64)。裝了 WinDbg/cdb 時可 `cdb -z file.dmp -c "!analyze -v; q"`。
- **回答**:什麼在崩潰/多常崩?哪個 module+offset 是 faulting frame(bucket)?BSOD 的 bugcheck code 指向哪個 driver?有沒有默默發生的 GPU TDR(LiveKernelEvent)?崩潰模式從哪個更新後開始?
- **為什麼贏 shell**:幾百個資料夾散落兩個庫 + per-user 路徑、UTF-16 格式;**二進位 dump 格式**(和 SRUM 同一理由,準則 a)。tier-1 純 Python header 解析就能給 bugcheck + 載入 driver 清單;裝了 cdb 則跑一次快取結果回傳 10 欄 JSON 結論(b, e)。內建 bugcheck 碼對照表加上 shell 沒有的解讀。
- **effort**:low(WER 解析純 stdlib)– medium(含 dump header)
- **注意**:讀 `Minidump`/`MEMORY.DMP`/System WER 需 admin;kernel dump 是 `DUMP_HEADER64` 不是 MDMP(bugcheck 欄位在固定 offset 0x38+,自己寫 ~100 行 struct);`minidump` PyPI 只吃 user-mode dump;cdb 要符號(`_NT_SYMBOL_PATH=srv*C:\symbols*https://msdl.microsoft.com/download/symbols`)首次慢、要積極快取。

### 2. `exec-forensics` — Prefetch + Amcache + BAM/UserAssist/ShimCache(含 `timeline()`)
- **資料來源**:執行證據 —— Prefetch(`.pf`,Win8+ MAM/Xpress-Huffman 壓縮)、Amcache.hve(registry hive)、BAM/DAM、UserAssist、ShimCache(AppCompatCache)
- **存取**:`C:\Windows\Prefetch\*.pf`(magic `MAM\x04`,`RtlDecompressBufferEx(COMPRESSION_FORMAT_XPRESS_HUFF)` 解壓或 `windowsprefetch`/`libscca`,再解 SCCA v30/31);`C:\Windows\appcompat\Programs\Amcache.hve` + `.LOG1/.LOG2`(`regipy`/`yarp` 可 replay dirty log);BAM:`HKLM\SYSTEM\CurrentControlSet\Services\bam\State\UserSettings\<SID>`(REG_BINARY:exe 路徑 + 前 8 bytes 為 last-exec FILETIME);UserAssist(ROT13 名稱 + run count);ShimCache(`...\Session Manager\AppCompatCache`,`10ts` 簽名)。
- **回答**:「凌晨 2:00–2:15 機器凍住時到底跑了什麼?」每個 exe 的實際啟動時間(Win10/11 保留最後 8 次)、run count、載入的 DLL、來源磁碟(USB/temp?)、SHA1。
- **為什麼贏 shell**:**三種 shell 完全讀不了的格式**(a);合成一條正規化時間軸。跟 SRUM 天作之合 —— SRUM 說吃多少資源,這個說何時啟動、載入了什麼。`timeline(start,end)` 是 capstone,把五種時間戳語意正規化成一個 schema(d)。
- **effort**:medium
- **注意**:多數來源要 admin;Prefetch 在部分 SSD-tuned/VM 映像被停用(EnablePrefetcher);Win11 24H2 是 SCCA v31;ShimCache 時間戳是**檔案 last-modified 不是執行時間**(標成「存在證據」)。三者建議做成**同一個** Python 服務、`timeline` 是其中一個工具。

### 3. `perfmon` — PDH 即時效能計數器 + 命名基線
- **資料來源**:Windows Performance Data Helper(PDH)即時計數器(和 Perfmon/工作管理員同源)
- **存取**:`pdh.dll`(ctypes 或 pywin32 `win32pdh`:`PdhOpenQuery`/`PdhAddEnglishCounter`/`PdhCollectQueryData`)。關鍵路徑:`\Processor Information(_Total)\% Processor Utility`、`\Process(*)\% Processor Time / IO Data Bytes/sec / Working Set - Private / Handle Count / Thread Count`、`\PhysicalDisk(*)\Avg. Disk sec/Transfer`、`\Memory\Available MBytes / Pool Nonpaged Bytes`、`\GPU Engine(*)\Utilization Percentage`。
- **回答**:**現在**瓶頸是什麼(CPU vs 磁碟延遲 vs 記憶體壓力 vs GPU)?哪個 process 這一秒在吃資源?磁碟延遲異常嗎(>25ms)?nonpaged pool / handle 在漲嗎(kernel/handle 洩漏)?在猛換頁嗎?
- **為什麼贏 shell**:`Get-Counter` 慢(多秒 spawn)、本地化計數器名在非英文系統會壞(`PdhAddEnglishCounter` 解決)、console 輸出是 token 噪音。常駐 PDH query 讓取樣近乎即時、rate 計數器一次呼叫就對;能存命名基線回傳 diff(b, d, e)。SRUM 的即時互補。
- **effort**:medium
- **注意**:多數計數器不需 admin;用 `% Processor Utility` 才和工作管理員對得上;`Process(*)` 實例名會撞(chrome#1/#2)要靠 `ID Process` 計數器辨 PID。

### 4. `config-drift` — ASEP autoruns + 服務/工作/driver/更新 → SQLite 快照 diff
- **資料來源**:時間點系統設定快照,存 SQLite 後跨時間 diff:ASEP、服務 + 啟動類型、driver store、已安裝程式(Uninstall keys)、排程工作、已安裝更新、環境變數
- **存取**:`winreg`(30+ 個 autostart 位置:Run/RunOnce ± Wow6432Node、Winlogon、Services、IFEO、AppInit_DLLs、LSA、shell extensions、Startup 資料夾、Active Setup);WMI `root\subscription`(永久事件訂閱);`C:\Windows\System32\Tasks`;WU COM API。工具:`snapshot_now()`、`diff(a,b)`、`what_changed_since(date)`。
- **回答**:**殺手級問題 ——「問題發生前,機器上什麼變了?」** 哪個服務被改成自動?last-good 到 first-bad 之間裝了哪個程式/driver/更新?裝了 app X 後冒出哪個 autostart?
- **為什麼贏 shell**:準則 (d) 最純粹的體現 —— shell 只看得到現在,無法跟上個月比。基線/diff 引擎把每次列舉變成時間序列、回傳精簡 diff 而非兩份要模型逐字比對的 dump。可用排程每天自動快照。
- **effort**:medium
- **注意**:SQLite 是 stdlib;collectors 和 `asep-autoruns`/`taskcache`/`winupdate-history`/`driver-inventory` **大量重疊 —— 應合併成同一個服務、drift 是其上一層**。價值隨時間累積,**要早做**才有「好的時候」基線。

### 5. `netconn` — TCP/UDP 表 + 擁有者 process/**service** + 基線 diff
- **資料來源**:IP Helper API 擴充連線表 + Service Control Manager 的 service-tag 對應
- **存取**:`GetExtendedTcpTable`/`GetExtendedUdpTable`(iphlpapi.dll via ctypes,或 psutil 抄近路);service-tag→服務名靠 `I_QueryTagInformation`(advapi32)或 match svchost PID;exe 路徑靠 `QueryFullProcessImageName`;可選 TCP ESTATS 看重傳。
- **回答**:每個 socket 是誰的(svchost 裡**哪個服務**)?跟基線比多了什麼連線/監聽(beaconing、rogue listener)?port 耗盡診斷(per-process TIME_WAIT/CLOSE_WAIT 計數)?哪個 app 正在灌爆網路(配 SRUM 歷史)?
- **為什麼贏 shell**:`netstat -abo` 要 admin、輸出多行不對齊耗 token、**解不出 svchost 裡的服務名**;`Get-NetTCPConnection` 缺 UDP 擁有者與服務名又慢。MCP 每個 socket 一列乾淨 JSON(pid/exe/serviceName/state),伺服器端維護命名快照做 diff(d)、預先聚合 state 計數(b, e)。網路版的 SRUM。
- **effort**:medium
- **注意**:完整擁有者資訊要 admin(protected process 的 exe 拿不到 → exe=null);psutil 覆蓋 80% 但沒服務名/ESTATS;嚴格唯讀。

### 6. `disk` — USN journal + MFT 級空間掃描 + SMART 趨勢
> 三個 storage 工具建議合成一個服務。
- **USN journal reader(value 5)**:`FSCTL_QUERY/READ_USN_JOURNAL`(DeviceIoControl on `\\.\C:`,解 USN_RECORD_V2/V3,FRN→路徑,存 per-volume cursor)。回答「當機前幾分鐘哪些檔案被建立/刪除/改寫」「哪個目錄在暴衝」。`fsutil` 輸出是 MB 級 FRN 而非路徑(a, b, d)。
- **diskspace MFT 掃描(value 5)**:`FSCTL_ENUM_USN_DATA` 幾秒走完整個 MFT(百萬檔),快照存 SQLite。回答「什麼在吃磁碟」「哪個目錄比上週多了 40GB(WinSxS/Docker vhdx/browser cache/dump)」。`gci -Recurse` 要好幾分鐘 + 一牆文字(b, d, e)。注意 OneDrive 雲端占位檔回報大小但占 ~0 bytes。
- **diskhealth SMART/NVMe(value 4)**:`IOCTL_STORAGE_QUERY_PROPERTY`(NVMe health log page 0x02)、`MSFT_StorageReliabilityCounter`,讀數存 SQLite 做趨勢。回答「硬碟在死嗎 —— reallocated/pending sector、NVMe percentage_used、media error」。**趨勢方向**才是訊號(a, d)。USB 外接盒/RAID 常擋 SMART passthrough → 優雅降級。
- **effort**:medium · **注意**:全部要 admin(raw volume handle / IOCTL);USN_RECORD_V3 在新 NTFS/ReFS 是 128-bit FRN;journal 會 wrap(回報涵蓋範圍)。

---

## 🥈 第二梯隊(value 3–4,進階,等第一批用順再做)

### `procinspect`(value 5,effort high)— Process Explorer 級深度檢查
`NtQuerySystemInformation(SystemExtendedHandleInformation)` + `NtQueryObject` 列舉 handle(**誰鎖住這個檔案** —— 最常見的「刪不掉/更新不了」問題)、Wait Chain Traversal API 分析掛死、loaded DLL 簽章、handle/GDI 洩漏 diff。shell 根本無法列舉別的 process 的 handle 或走 wait chain(a, b, c, d)。要 admin/SeDebugPrivilege;`NtQueryObject` 對某些 pipe handle 會 hang → 在可 kill 的 worker thread 上加 timeout。hang-dump(符號化 per-thread stack)併進來當 v2 工具即可,不必獨立。

### `etwtrace`(value 4,effort high)— 短時 ETW 抓取 + 伺服器端摘要
`wpr -start CPU -start DiskIO` / `wpr -stop`,伺服器端解 ETL 只回 top-N 聚合(CPU% by module、DPC μs by driver、IO by file)。ETL 是不透明二進位、30 秒 trace 幾百 MB —— 價值全在伺服器端縮減 + session 生命週期管理(a, b, e)。**critic 校正**:純 Python 別想做符號化 stack(tracerpt XML 不解 stack-walk);務實交付是 **module 級歸因 + DPC/ISR by driver**(LatencyMon 那題,不需符號)。要 admin + SeSystemProfilePrivilege;`wpr -cancel` 收尾否則 session 洩漏(上限 64)。

### `memstate`(value 4,effort medium)— poolmon/RamMap 級記憶體歸因 【critic 補漏】
`NtQuerySystemInformation`:`SystemPoolTagInformation`(per-tag paged/nonpaged pool)+ `SystemMemoryListInformation`(standby/modified/free 頁清單)。回答「32GB 去哪了」「是不是 driver nonpaged-pool 洩漏、**哪個 tag/driver**」—— PDH 的 Pool Nonpaged Bytes 只說 pool 在漲,不說哪個 driver。**PowerShell 完全沒有等價物**(poolmon 只在 WDK、是互動式 console UI)。跨快照 diff pool tag(d)。memory-list class 要 admin;tag→driver 對照掃 `drivers\*.sys` + 內建 WDK `pooltag.txt`(啟發式)。

### `wheadecode`(value 3,effort medium)— WHEA/CPER 硬體錯誤解碼 【critic 補漏】
你的 eventlog MCP 抓得到 WHEA-Logger 事件(System log ID 1/17-20/46/47)但 payload 是**不透明二進位 CPER blob**,解不了。struct 解 UEFI spec Appendix N:Machine Check(MCA bank、MCi_STATUS 位元)、PCIe AER(device BDF、correctable/fatal)、memory error(node/channel/DIMM)。**回答 BSOD 調查最重要的分岔:換硬體 vs 追 driver**。配 bugcheck 0x124 WHEA_UNCORRECTABLE_ERROR。純 stdlib struct,無額外依賴;讀 `HKLM\...\WHEA\Errors` 要 admin。只在真的有記 WHEA 事件的機器上才有用,但有的時候整個 stack 只有它讀得懂。

### `powerdiag`(value 4,effort medium)— powercfg 報告解析器
`powercfg /energy` `/sleepstudy` `/batteryreport(/XML)`(幾百 KB HTML → findings JSON)、`/requests` `/waketimers`。回答「為何不睡/什麼喚醒它」「Modern Standby 誰在耗電」「電池老化(design vs full-charge 趨勢)」。存過去報告做趨勢(b, c, d)。**critic 校正**:別靠 `MSAcpi_ThermalZoneTemperature`(多數消費級主機不支援,回 "Not supported")—— 溫度/節流走 perfmon 的 PDH `Thermal Zone Information` / `% Performance Limit`。`/energy` `/sleepstudy` 要 admin;`/batteryreport` 不用;sleepstudy 只在 Modern Standby 機器上有。

### `filterstack`(value 3,effort medium)— 第三方 filter driver 地圖 【critic 補漏】
AV/VPN/EDR/backup 產品插入的三個過濾層:filesystem minifilter(`fltlib` FilterFind / `fltmc`)、WFP filter/callout(`FwpmFilterEnum0` / `netsh wfp show filters` 的多 MB XML)、NDIS LWF + Winsock LSP。回答「哪些第三方 driver 坐在每次檔案操作與每個封包路徑上」—— 真實世界「整台機器很慢」「檔案 IO 高延遲」「連線莫名失敗」的頭號原因(卸載殘留的 VPN/AV WFP callout 是經典)。填補真空:`fwaudit` 只管防火牆規則、明確不含 WFP callout 層;沒有候選涵蓋 minifilter。altitude 範圍辨識廠商類別。要 admin。

### `winupdate-history`(value 4,effort medium)— WU 歷史 + CBS.log
WUA COM API `QueryHistory`(完整含失敗 HRESULT)、CBS 套件狀態、`CBS.log`(幾百 MB → 只回時間窗內的錯誤叢集)。回答「crash 前裝了哪個 KB」「更新為何反覆失敗(HRESULT 解碼)」「CU 卡在 pending/rollback」。完整 WU 歷史只有 COM API 拿得到(b);內建 HRESULT 對照表。要 pywin32;CBS.log 讀要 admin、rotated 是 `.cab`。

---

## ❌ Critic 否決 / 應併入而非獨立

| 候選 | 問題 |
|---|---|
| **Reliability Monitor** | `Get-CimInstance Win32_ReliabilityRecords \| ConvertTo-Json` 一行搞定、已是結構化物件。無二進位、無解析負擔、無狀態。value 應為 1–2,或折進 config-drift/wer-store 當一個工具。 |
| **driver-inventory** | 多半 one-liner(`Win32_PnPSignedDriver`);唯一值得的是 `setupapi.dev.log` 時間軸解析 —— 應在 config-drift 內。standalone 只值 2。 |
| **taskcache-deep** | 工作執行事件已由 eventlog MCP 覆蓋;只保留 on-disk/registry 的 XML-vs-TaskCache-vs-API 差異偵測,併進 asep-autoruns。 |
| **mftmeta($SI/$FN timestomping)** | 入侵鑑識題,非效能/當機/穩定性。「什麼何時變了」USN journal 已更便宜地覆蓋。高工程量、niche → value 2 或否決。 |
| **hangdump** | 大幅重疊 procinspect(已含 WCT / thread 狀態)。唯一殘留(符號化 stack)靠 dbghelp+符號,最難。應是 procinspect 內的工具。(小勘誤:MiniDumpWriteDump 只需 `PROCESS_QUERY_INFORMATION\|PROCESS_VM_READ`,非 `PROCESS_ALL_ACCESS`。) |
| **wlandiag** | 一半(connect/disconnect/roam 歷史 8001/8002/8003)是 eventlog MCP 已覆蓋的事件查詢。只保留 live wlanapi RSSI 輪詢 + reason-code 解碼表。value 3 較實。 |
| **exectimeline** | 不是資料來源,是 Prefetch/Amcache/BAM 之上的關聯層 → 應是 exec-forensics 內的 `timeline()` 工具,不獨立計分。 |
| 防火牆規則 / DNS cache / proxyview / volstate 單獨包 | 近乎 one-liner 或 niche;併進 netconn / disk 當附屬工具。proxyview 的 WinINET 二進位 blob 解析(a)是唯一亮點,可留作 netconn 的附加工具。 |

---

## 落地順序建議

1. **先做 `crash`(#1)**:effort 最低(WER 純 stdlib)、與 eventlog 互補最強,完全複製 SRUM 的成功模式(二進位 → 結構化 JSON)。
2. **再做 `exec-forensics`(#2)+ `config-drift`(#4)**:drift **要早做** —— 基線靠時間累積,下次出事前要先有「好的時候」快照。
3. 架構照現有模式:每主題一個 Python `streamable_http` 服務,127.0.0.1:8779↑ 往上排,elevated 啟動(多數來源要 admin),`<name>_reader.py`(無 MCP 依賴)+ `<name>_mcp_server.py`(FastMCP)+ `start_*.ps1`/`install_task.ps1` + `tests/`。
4. **最便宜的一步:裝 Sysmon** —— 不用寫任何新 MCP,它把 process 建立 / 網路連線 / driver 載入寫進 Event Log,現有 eventlog MCP 立刻變強。

---

## 附錄:方法

6 個平行視角掃描(perf-live / crash-reliability / config-startup / storage-fs / network / exec-forensics)產出 28 個候選,再由 1 個 completeness critic 補漏(memstate / wheadecode / filterstack)並校正高估項。每個候選經上述 (a)–(e) 準則評分。原始 workflow:`windows-mcp-candidates`(2026-07-11)。
