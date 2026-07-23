# Removes the GooseWeb scheduled task (does not stop a manually-started instance).
$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName "GooseWeb"
Unregister-ScheduledTask -TaskName "GooseWeb" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'GooseWeb' (if it existed)." -ForegroundColor Green
