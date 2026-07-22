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
