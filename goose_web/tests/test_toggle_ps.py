import os, shutil, subprocess, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOGGLE = HERE.parent / "mcp_toggle.ps1"
PWSH = shutil.which("powershell") or shutil.which("pwsh")

_FIX = """GOOSE_PROVIDER: openai

extensions:
  developer:
    type: builtin
    enabled: true
  srum:
    type: streamable_http
    enabled: true
    uri: http://127.0.0.1:8777/mcp
  eventlog:
    type: streamable_http
    enabled: true
    uri: http://127.0.0.1:8778/mcp
"""


@unittest.skipUnless(PWSH and TOGGLE.exists(), "PowerShell or mcp_toggle.ps1 unavailable")
class PsToggle(unittest.TestCase):
    def _ps(self, script):
        full = ". '%s'; %s" % (str(TOGGLE), script)
        r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", full],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_predicate(self):
        self.assertEqual(self._ps("Test-Togglable @{type='streamable_http';uri='http://127.0.0.1:8777/mcp'}"), "True")
        self.assertEqual(self._ps("Test-Togglable @{type='streamable_http';uri='http://192.168.86.44:8765/mcp'}"), "False")
        self.assertEqual(self._ps("Test-Togglable @{type='builtin'}"), "False")

    def test_flip_only_target_and_idempotent(self):
        d = tempfile.mkdtemp(prefix="gw_ps_")
        cfg = Path(d) / "config.yaml"
        cfg.write_text(_FIX, encoding="utf-8", newline="")
        out = self._ps("Set-ExtensionEnabled '%s' 'srum' $false" % cfg)
        self.assertEqual(out, "True")
        txt = cfg.read_text(encoding="utf-8")
        self.assertIn("enabled: false", txt)
        self.assertEqual(txt.count("enabled: true"), 2)  # developer + eventlog untouched
        # idempotent
        self.assertEqual(self._ps("Set-ExtensionEnabled '%s' 'srum' $false" % cfg), "False")


if __name__ == "__main__":
    unittest.main()
