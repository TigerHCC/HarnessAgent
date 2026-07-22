# Profile A/B question set (routing-accuracy regression + stability)

18 diagnostic questions with the expected tool family and the narrow profile each belongs to. Run each
under its profile with real tool scoping (`goose run --no-profile --with-streamable-http-extension`
per the profile's MCP ports, or apply the profile in goose_web and ask). Record: first diagnostic tool
called, whether it is in the expected family, whether the answer cites tool data, turn count. Forensics
questions carry extra phrasings (marked *stability*) to check the routing is robust, not a lucky shot.

Port map: srum 8777 · eventlog 8778 · crash 8779 · exec 8780 · drift 8781 · netconn 8782 ·
perfmon 8783 · disk 8784 · procinspect 8785 · memstate 8786 · filterstack 8787 · winupdate 8788.

## Performance (profile: perf → srum, perfmon, memstate, disk, procinspect, winupdate)

1. 這台電腦最近開機後特別慢，找出最可能的原因。（期望：perfmon/disk/memstate/procinspect）
3. 最近一週誰用掉最多網路流量？（期望：srum）
5. C 槽最近健康狀況如何，SMART 有沒有警告？（期望：disk）
7. 記憶體使用是否正常，有沒有洩漏跡象？（期望：memstate/perfmon）
9. Windows Update 有沒有失敗的更新或待重開機？（期望：winupdate）
10. 有沒有哪個行程鎖住了某個檔案導致無法刪除？（期望：procinspect）*— 已知 borderline：未指定檔案，模型可能反問而不呼叫工具*
16. 磁碟寫入量最近有沒有異常暴增，哪個程式造成的？（期望：disk/srum）
18. CPU 使用率長期偏高，是哪個行程造成的？（期望：perfmon/srum/procinspect）

## Forensics (profile: sec → eventlog, crash, exec, drift, netconn, filterstack)

2. 昨天有沒有發生過應用程式崩潰？哪個程式？（期望：crash）
4. 檢查有沒有可疑的對外連線正在進行。（期望：netconn）
6. 系統的自啟動項最近有沒有變化？（期望：drift）
8. 查一下 notepad.exe（或任一程式）最近有沒有被執行過。（期望：exec）
11. 有沒有程式在背景偷偷連到國外 IP？（期望：netconn）*stability*
12. 幫我看最近三天有哪些程式被第一次執行。（期望：exec）*stability*
13. 開機自動啟動的服務有沒有被新增或移除？（期望：drift）*stability*
14. 有沒有藍色當機畫面(BSOD)的紀錄？原因是什麼？（期望：crash）*stability*
15. 系統事件記錄裡有沒有反覆出現的錯誤？（期望：eventlog）
17. 有沒有防毒或VPN的驅動掛在網路層可能拖慢連線？（期望：filterstack）

## Latest result (2026-07-23, real scoping — see profile_ab_results.md)

Forensics 10/10 (both runs) · Performance 7/8 · overall 17/18 any-hit, stable across two runs. The only
miss is the borderline Q10. Conclusion: narrow scoping delivers accurate routing on the small model —
the fix is scoping the toolset, not the recipe wording.
