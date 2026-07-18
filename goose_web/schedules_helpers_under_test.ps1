function Merge-ConfirmArgs($arguments, $token) {
    $out = @{}; foreach ($k in $arguments.Keys) { $out[$k] = $arguments[$k] }
    $out['confirm_token'] = $token
    return $out
}
function Parse-McpResult($obj) {
    if ($null -eq $obj -or $null -eq $obj.result) { return $null }
    if ($obj.result.PSObject.Properties['structuredContent'] -and $obj.result.structuredContent) {
        return $obj.result.structuredContent
    }
    if ($obj.result.content) {
        foreach ($c in $obj.result.content) {
            if ($c.type -eq 'text' -and $c.text) { try { return ($c.text | ConvertFrom-Json) } catch {} }
        }
    }
    return $obj.result
}
