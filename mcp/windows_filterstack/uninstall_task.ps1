# Removes the mcp-filterstack scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-filterstack" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-filterstack' (if it existed)." -ForegroundColor Green
