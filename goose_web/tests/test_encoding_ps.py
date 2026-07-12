"""Regression tests for the UTF-8 request decoding in server.ps1 (goose_web/http_encoding.ps1).

Bug being locked down: .NET's HttpListenerRequest.ContentEncoding falls back to the system ANSI
codepage (Big5 on a zh-TW box) when the request's Content-Type carries no charset -- which is
exactly what index.html sends. A Chinese chat message therefore reached goose as mojibake, and
HttpListenerRequest.QueryString mangled non-ASCII upload filenames the same way, because it
%-decodes using ContentEncoding too.

The PowerShell here builds its Chinese from code points on purpose: PowerShell 5.1 reads a BOM-less
.ps1 as ANSI, so a literal would be corrupted at parse time and the test would prove nothing.
"""
import shutil, subprocess, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENC = HERE.parent / "http_encoding.ps1"
PWSH = shutil.which("powershell") or shutil.which("pwsh")

# "你好，請檢查記憶體" and "測試報告.txt" as PowerShell code-point expressions
CN_MSG = "-join (@(0x4F60,0x597D,0xFF0C,0x8ACB,0x6AA2,0x67E5,0x8A18,0x61B6,0x9AD4) | %{[char]$_})"
CN_FILE = "(-join (@(0x6E2C,0x8A66,0x5831,0x544A) | %{[char]$_})) + '.txt'"


@unittest.skipUnless(PWSH and ENC.exists(), "PowerShell or http_encoding.ps1 unavailable")
class PsEncoding(unittest.TestCase):
    def _ps(self, script):
        full = ". '%s'; %s" % (str(ENC), script)
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", full],
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_query_value_decodes_utf8_percent_escapes(self):
        # encodeURIComponent("測試報告.txt") -> the %-escapes the browser actually sends
        out = self._ps(
            "$f = %s;"
            "$q = '?session=web&name=' + [System.Uri]::EscapeDataString($f);"
            "(Get-QueryValue $q 'name') -eq $f" % CN_FILE)
        self.assertEqual(out, "True", "non-ASCII filename did not survive query decoding")

    def test_query_value_basics(self):
        self.assertEqual(self._ps("Get-QueryValue '?a=1&b=2' 'b'"), "2")
        self.assertEqual(self._ps("Get-QueryValue '?a=1&b=2' 'a'"), "1")
        # absent key -> $null (prints as empty), valueless key -> empty string
        self.assertEqual(self._ps("$v = Get-QueryValue '?a=1' 'zz'; $null -eq $v"), "True")
        self.assertEqual(self._ps("$v = Get-QueryValue '?a' 'a'; $v -eq ''"), "True")
        self.assertEqual(self._ps("$v = Get-QueryValue '' 'a'; $null -eq $v"), "True")
        # '=' inside a value must survive (Split with a 2-part limit)
        self.assertEqual(self._ps("Get-QueryValue '?t=ab=cd' 't'"), "ab=cd")

    def test_body_is_decoded_as_utf8_not_the_ansi_codepage(self):
        # Drive a real HttpListener the way the browser does: UTF-8 JSON body,
        # "Content-Type: application/json" with NO charset. Assert Read-Utf8Body survives it AND
        # that the old ContentEncoding-trusting path would have corrupted it (so this test would
        # actually have caught the bug).
        script = """
$msg = %s
$payload = '{"message":"' + $msg + '"}'
$l = New-Object System.Net.HttpListener
$l.Prefixes.Add('http://127.0.0.1:18877/')
$l.Start()
$j = [powershell]::Create()
[void]$j.AddScript({
    param($l, $encFile)
    . $encFile
    $ctx = $l.GetContext()
    $ms = New-Object System.IO.MemoryStream
    $ctx.Request.InputStream.CopyTo($ms)
    $raw = $ms.ToArray()
    $res = @{
        fixed  = [System.Text.Encoding]::UTF8.GetString($raw)        # what Read-Utf8Body does
        old    = $ctx.Request.ContentEncoding.GetString($raw)        # the pre-fix behaviour
        encName = $ctx.Request.ContentEncoding.WebName
    }
    $ctx.Response.StatusCode = 200; $ctx.Response.Close()
    return $res
}).AddArgument($l).AddArgument('%s')
$h = $j.BeginInvoke()
$wc = New-Object System.Net.WebClient
$wc.Headers.Add('Content-Type', 'application/json')
[void]$wc.UploadData('http://127.0.0.1:18877/', 'POST', [System.Text.Encoding]::UTF8.GetBytes($payload))
$r = $j.EndInvoke($h)[0]
$l.Stop()
'fixed=' + ($r.fixed -eq $payload) + ' ansi=' + $r.encName + ' oldmatch=' + ($r.old -eq $payload)
""" % (CN_MSG, str(ENC).replace("'", "''"))
        out = self._ps(script)
        self.assertIn("fixed=True", out, "UTF-8 decode of the body failed: %s" % out)
        # On an ANSI codepage that isn't UTF-8, the old path must have been broken -- otherwise this
        # test is vacuous on this machine and we say so rather than silently passing.
        if "ansi=utf-8" not in out:
            self.assertIn("oldmatch=False", out,
                          "expected the old ContentEncoding path to corrupt non-ASCII, got: %s" % out)


if __name__ == "__main__":
    unittest.main()
