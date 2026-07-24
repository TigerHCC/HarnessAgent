# Audio 診斷 — 各角色情境與預設 Prompt

`windows_audio` MCP（8796，第 18 個 canonical、唯讀）橫跨三個角色：Diagnostics（完整音訊三連查）、
Performance（斷音當效能事件追根因）、Forensics（麥克風隱私與可疑音訊裝置鑑識）。`audio` 已加入
diag/perf/sec 三個 profile 的 enable 清單，其工具家族也寫進對應 recipe 的選路索引。

工具：`audio_health`（服務＋紅旗）、`audio_devices`（全端點狀態/角色/音量）、`audio_defaults`（角色
預設，直指無多媒體/無會議聲）、`audio_microphone`（狀態/靜音/隱私）、`audio_bluetooth`（A2DP媒體
vs HFP通話）、`audio_sessions`（每 app 靜音）、`audio_glitches`（斷音風險指標＋選配 ETW trace）。

設計原則（延續 A/B 結論）：**處方式逐工具指定**（小模型路由才準）＋**跨工具家族協同**（audio 只說
「斷音/有人用麥克風」，配上角色的 perfmon/exec/netconn 才定位根因與兇手）＋**結尾要「結論＋修法」**。

---

## 🩺 Diagnostics 角色

### Preset A — 一鍵音訊體檢
```
執行完整音訊診斷。依序：
1. audio_health — 先看服務狀態與紅旗摘要。
2. audio_defaults — 檢查 Multimedia 與 Communications 兩個角色的預設輸出/輸入裝置是否 Active、有無靜音或音量 0（無多媒體聲/無會議聲的頭號原因）。
3. audio_devices — 列出所有端點狀態，找出被 Unplugged/Disabled/NotPresent 的裝置。
4. audio_microphone — 確認預設擷取裝置存在、未靜音，且 Windows 麥克風隱私未封鎖。
5. audio_bluetooth — 若用藍牙，確認 profile 是 A2DP（媒體）還是卡在 HFP（單聲道通話）。
6. audio_sessions — 檢查是否某個 app 在音量混音器被個別靜音。
每項給「正常/異常」判定並引用實際數值，最後條列「確認的問題 → 具體修法」（切換預設裝置、解除靜音、開啟麥克風權限）。
```

### Preset B — 無會議聲音專查
```
我音樂有聲音但視訊會議沒聲音。用 audio_defaults 比對 Multimedia 與 Communications 兩個角色的預設輸出裝置——它們常常不同；找出 Communications 預設是否指向一個已斷線/停用/靜音的裝置。再用 audio_devices 確認該裝置狀態。給出「會議聲音該用哪個裝置」的結論與修正步驟。
```

---

## ⚡ Performance 角色

### Preset C — 斷音效能追兇
```
我的聲音會斷斷續續/爆音。當作效能問題追根因，依序：
1. audio_glitches — 先取得音訊層的斷音風險指標（取樣率不符、近期音訊驅動錯誤）。
2. perfmon — 即時看 CPU、磁碟延遲、記憶體壓力有無瞬間瓶頸（DPC/中斷延遲是音訊 dropout 的頭號元兇）。
3. procinspect — 找出正在搶 CPU 或 hang 的行程（防毒掃描、備份、瀏覽器分頁常見）。
4. memstate — 確認有無記憶體壓力導致換頁卡頓。
5. srum — 若是特定時段才斷，看那時哪個程式在大量用 CPU/網路。
每項引用數值，最後指出「斷音的最可能效能成因 + 是哪個行程/驅動 + 建議處置」（關閉背景程式、關掉裝置電源節能、更新音訊驅動）。
```

### Preset D — 藍牙音質掉檔追蹤
```
我的藍牙耳機聲音變差/延遲高。用 audio_bluetooth 確認是否被切到 HFP（單聲道通話 profile，音質差）；用 audio_glitches 看近期音訊驅動錯誤；用 perfmon 看是否有 Wi-Fi/藍牙共存造成的干擾峰值。結論給出「為什麼掉檔 + 如何強制回 A2DP」。
```

---

## 🔎 Forensics 角色

### Preset E — 麥克風偷聽鑑識（招牌跨工具 prompt）
```
懷疑有程式在偷用我的麥克風，做鑑識調查，依序：
1. audio_microphone — 列出目前擁有麥克風存取權的 app（Windows 隱私 ConsentStore），特別標出你不認得或不該有麥克風權限的程式。
2. exec — 針對每個可疑 app，查它近期的執行紀錄（Prefetch/BAM/UserAssist）——是不是背景常駐、何時開始跑。
3. netconn — 查那些程式有沒有正在對外的連線（麥克風資料外送的跡象），列出擁有者行程與目的地。
4. drift — 檢查最近自啟動項/服務有無新增可疑的音訊相關項目，以及麥克風權限是否近期被改。
5. audio_devices — 確認有沒有你不認得的擷取裝置（可疑的虛擬音訊/藍牙裝置）被加入。
每項引用證據（時間戳＋路徑＋行程＋目的地 IP），最後給出「是否有偷聽跡象、由哪個程式、建議處置（撤銷麥克風權限/移除程式/封鎖連線）」。
```

### Preset F — 可疑音訊裝置盤查
```
盤查所有連上這台電腦的音訊裝置有無異常。用 audio_devices 列出全部 render+capture 端點；用 audio_bluetooth 列出藍牙音訊裝置——標出任何你不認得的、或狀態異常（例如陌生的 A2DP 接收器可能是側錄裝置）。配合 drift 看這些裝置是否近期才被加入。給出「有無可疑裝置 + 建議」。
```

---

## 使用方式

goose_web 側欄切到對應角色（Diagnostics / Performance / Forensics）→ 貼上該情境的 preset → 送出。
切角色後 goose 只看得到該角色的工具（含 audio），配合 recipe 的選路索引，小模型能穩定走完整診斷樹。
