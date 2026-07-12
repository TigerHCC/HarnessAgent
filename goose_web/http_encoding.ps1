# http_encoding.ps1 -- UTF-8 request decoding for server.ps1's HttpListener.
#
# WHY THIS FILE EXISTS
# .NET's HttpListenerRequest.ContentEncoding falls back to Encoding.Default -- the system ANSI
# codepage (Big5/GBK/Shift-JIS/1252, depending on the machine) -- whenever the request's
# Content-Type carries no charset. The browser sends "Content-Type: application/json" with no
# charset (index.html), so on a non-Western Windows the UTF-8 bytes of a Chinese message were being
# decoded as Big5 and arrived at goose as mojibake.
#
# It bites twice, because HttpListenerRequest.QueryString ALSO decodes its %-escapes using
# ContentEncoding -- so ?name=<utf8 filename> was mangled the same way on upload.
#
# Both are fixed by never trusting ContentEncoding: JSON is UTF-8 by definition (RFC 8259 s8.1),
# and URI percent-escapes are UTF-8 by definition (RFC 3986 s2.5). Decode both as UTF-8, always.
#
# Parity twin of server.py, which is already correct by construction: json.loads() on bytes
# auto-detects UTF-8, and urllib's parse_qs defaults to UTF-8.

function Read-Utf8Body($ctx) {
    # Read the whole request body as UTF-8, ignoring Request.ContentEncoding.
    $ms = New-Object System.IO.MemoryStream
    try {
        $ctx.Request.InputStream.CopyTo($ms)
        return [System.Text.Encoding]::UTF8.GetString($ms.ToArray())
    } finally { $ms.Dispose() }
}

function Get-QueryValue([string]$query, [string]$key) {
    # UTF-8 replacement for Request.QueryString[$key]. $query is the raw "?a=b&c=d" string
    # (HttpListenerRequest.Url.Query). Returns $null when the key is absent, '' when it is
    # present but valueless. UnescapeDataString always decodes %-escapes as UTF-8.
    if (-not $query) { return $null }
    foreach ($pair in $query.TrimStart('?').Split('&')) {
        if (-not $pair) { continue }
        $kv = $pair.Split('=', 2)
        $k = $kv[0]
        try { $k = [System.Uri]::UnescapeDataString($k) } catch {}
        if ($k -ne $key) { continue }
        if ($kv.Count -lt 2) { return '' }
        # NOTE: '+' is left as a literal '+'. The client encodes with encodeURIComponent, which
        # emits %20 for spaces and %2B for a real plus, so treating '+' as a space (the
        # form-urlencoded rule) would corrupt filenames that genuinely contain one.
        $v = $kv[1]
        try { $v = [System.Uri]::UnescapeDataString($v) } catch {}
        return $v
    }
    return $null
}
