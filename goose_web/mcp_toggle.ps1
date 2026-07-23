# mcp_toggle.ps1 -- per-MCP enable/disable helpers shared by server.ps1 and its
# worker/discoverer runspaces. Pure text edit of goose's config.yaml; no goose
# restart needed (each `goose run` re-reads config). Parity twin of the Python
# _is_togglable / _set_extension_enabled in server.py. Windows PowerShell 5.1 safe.

function Test-Togglable($e) {
    # True iff a streamable_http MCP with a uri -- loopback (the windows_* diagnostic suite)
    # OR remote (dtm/pk on the GB10 box). Toggling only flips the config's enabled flag, so
    # remote MCPs are safe to toggle. builtin and stdio extensions are not togglable this way.
    # Parity twin of Python's _is_togglable in server.py.
    if ($e.type -ne 'streamable_http') { return $false }
    if (-not $e.uri) { return $false }
    return $true
}

function Set-ExtensionEnabled($configPath, $extId, [bool]$enabled) {
    # Flip `enabled:` for one extension. Returns $true if changed, $false on no-op.
    $want = if ($enabled) { 'true' } else { 'false' }
    $raw  = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
    $nl   = if ($raw.Contains("`r`n")) { "`r`n" } else { "`n" }
    $lines = [System.Collections.Generic.List[string]]::new()
    foreach ($l in ($raw -split "`n")) { [void]$lines.Add(($l -replace "`r$", '')) }

    # 1) find `  <extId>:` key line (indent 2) inside the extensions: block
    $keyIdx = -1; $inExt = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $s = $lines[$i].Trim()
        if ($s -eq '' -or $s.StartsWith('#')) { continue }
        $indent = $lines[$i].Length - $lines[$i].TrimStart(' ').Length
        if ($indent -eq 0) { $inExt = ($s -eq 'extensions:'); continue }
        if ($inExt -and $indent -eq 2 -and $s -eq "${extId}:") { $keyIdx = $i; break }
    }
    if ($keyIdx -lt 0) { throw "extension '$extId' not found in $configPath" }

    # 2) block body = keyIdx+1 .. first later non-blank/comment line with indent <= 2
    $blockEnd = $lines.Count
    for ($j = $keyIdx + 1; $j -lt $lines.Count; $j++) {
        $s = $lines[$j].Trim()
        if ($s -eq '' -or $s.StartsWith('#')) { continue }
        $ind = $lines[$j].Length - $lines[$j].TrimStart(' ').Length
        if ($ind -le 2) { $blockEnd = $j; break }
    }

    # 3) find an existing enabled: line inside the block
    $enIdx = -1
    for ($j = $keyIdx + 1; $j -lt $blockEnd; $j++) {
        if ($lines[$j].Trim().StartsWith('enabled:')) { $enIdx = $j; break }
    }

    if ($enIdx -ge 0) {
        $cur = ($lines[$enIdx].Split(':', 2)[1]).Trim().ToLower()
        if ($cur -eq $want) { return $false }
        $ind = $lines[$enIdx].Length - $lines[$enIdx].TrimStart(' ').Length
        $lines[$enIdx] = (' ' * $ind) + "enabled: $want"
    } else {
        $kind = $lines[$keyIdx].Length - $lines[$keyIdx].TrimStart(' ').Length
        $lines.Insert($keyIdx + 1, (' ' * ($kind + 2)) + "enabled: $want")
    }

    $out = ($lines -join $nl)

    # one-time backup
    $bak = "$configPath.bak-webtoggle"
    if (-not (Test-Path -LiteralPath $bak)) { try { Copy-Item -LiteralPath $configPath -Destination $bak -Force } catch {} }
    # honor a read-only durability guard: clear, write, restore
    $item = Get-Item -LiteralPath $configPath
    $wasRo = $item.IsReadOnly
    if ($wasRo) { $item.IsReadOnly = $false }
    $tmp  = "$configPath.tmp"
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($tmp, $out, $utf8)
    Move-Item -LiteralPath $tmp -Destination $configPath -Force   # atomic rename on NTFS
    if ($wasRo) { (Get-Item -LiteralPath $configPath).IsReadOnly = $true }
    return $true
}
