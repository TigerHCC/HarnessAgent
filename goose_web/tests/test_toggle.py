import os, sys, tempfile, unittest
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="gw_toggle_")
os.environ["GOOSE_WEB_WORKSPACE"] = _TMP
os.environ["GOOSE_WEB_HOST"] = "127.0.0.1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402


class Togglable(unittest.TestCase):
    def test_loopback_streamable_http_is_togglable(self):
        self.assertTrue(server._is_togglable({"type": "streamable_http", "uri": "http://127.0.0.1:8777/mcp"}))
        self.assertTrue(server._is_togglable({"type": "streamable_http", "uri": "http://localhost:8788/mcp"}))

    def test_remote_and_builtin_not_togglable(self):
        self.assertFalse(server._is_togglable({"type": "streamable_http", "uri": "http://192.168.86.44:8765/mcp"}))
        self.assertFalse(server._is_togglable({"type": "builtin"}))
        self.assertFalse(server._is_togglable({"type": "stdio", "cmd": "x"}))
        self.assertFalse(server._is_togglable({"type": "streamable_http", "uri": ""}))


_FIXTURE = """GOOSE_PROVIDER: openai

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
  noflag:
    type: streamable_http
    uri: http://127.0.0.1:8790/mcp
"""


class Writer(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gw_cfg_")
        self.cfg = Path(self.d) / "config.yaml"
        self.cfg.write_text(_FIXTURE, encoding="utf-8", newline="")
        os.environ["GOOSE_CONFIG"] = str(self.cfg)

    def tearDown(self):
        os.environ.pop("GOOSE_CONFIG", None)

    def test_flip_true_to_false_only_target_line(self):
        changed = server._set_extension_enabled("srum", False)
        self.assertTrue(changed)
        txt = self.cfg.read_text(encoding="utf-8")
        self.assertIn("  srum:\n    type: streamable_http\n    enabled: false\n", txt)
        # eventlog + developer still true (3 remaining: developer, eventlog, and none else)
        self.assertEqual(txt.count("enabled: true"), 2)
        self.assertEqual(txt.count("enabled: false"), 1)

    def test_round_trip_back_to_true(self):
        server._set_extension_enabled("srum", False)
        server._set_extension_enabled("srum", True)
        txt = self.cfg.read_text(encoding="utf-8")
        self.assertIn("  srum:\n    type: streamable_http\n    enabled: true\n", txt)

    def test_idempotent_noop(self):
        self.assertFalse(server._set_extension_enabled("srum", True))  # already true

    def test_insert_when_no_enabled_line(self):
        changed = server._set_extension_enabled("noflag", False)
        self.assertTrue(changed)
        txt = self.cfg.read_text(encoding="utf-8")
        self.assertIn("  noflag:\n    enabled: false\n    type: streamable_http\n", txt)

    def test_unknown_id_raises(self):
        with self.assertRaises(KeyError):
            server._set_extension_enabled("does_not_exist", False)

    def test_backup_created_once(self):
        bak = self.cfg.with_name(self.cfg.name + ".bak-webtoggle")
        server._set_extension_enabled("srum", False)
        self.assertTrue(bak.exists())
        first = bak.read_text(encoding="utf-8")
        server._set_extension_enabled("eventlog", False)
        self.assertEqual(bak.read_text(encoding="utf-8"), first)  # backup not overwritten

    def test_crlf_preserved(self):
        self.cfg.write_text(_FIXTURE.replace("\n", "\r\n"), encoding="utf-8", newline="")
        server._set_extension_enabled("srum", False)
        raw = self.cfg.read_bytes()
        self.assertNotIn(b"\r\r\n", raw)  # no doubled CR
        self.assertIn(b"    enabled: false\r\n", raw)

    def test_readonly_file_written_and_restored(self):
        import stat as _stat
        os.chmod(self.cfg, _stat.S_IREAD)  # simulate the durability read-only guard
        try:
            self.assertTrue(server._set_extension_enabled("srum", False))
            self.assertIn("enabled: false", self.cfg.read_text(encoding="utf-8"))
            self.assertFalse(os.access(self.cfg, os.W_OK))  # read-only bit restored
        finally:
            os.chmod(self.cfg, _stat.S_IWRITE)  # let tempdir cleanup remove it


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="gw_snap_")
        self.cfg = Path(self.d) / "config.yaml"
        # srum disabled+togglable (should show as 'disabled'); eventlog enabled
        txt = _FIXTURE.replace(
            "  srum:\n    type: streamable_http\n    enabled: true\n",
            "  srum:\n    type: streamable_http\n    enabled: false\n")
        self.cfg.write_text(txt, encoding="utf-8", newline="")
        os.environ["GOOSE_CONFIG"] = str(self.cfg)

    def tearDown(self):
        os.environ.pop("GOOSE_CONFIG", None)

    def test_disabled_togglable_appears_disabled(self):
        exts, _tools = server._build_snapshot(handshake=False)
        by_id = {e["id"]: e for e in exts}
        self.assertIn("srum", by_id)
        self.assertEqual(by_id["srum"]["status"], "disabled")
        self.assertFalse(by_id["srum"]["enabled"])
        self.assertTrue(by_id["srum"]["togglable"])
        self.assertEqual(by_id["srum"]["count"], 0)

    def test_enabled_carries_flags(self):
        exts, _ = server._build_snapshot(handshake=False)
        by_id = {e["id"]: e for e in exts}
        self.assertTrue(by_id["eventlog"]["enabled"])
        self.assertTrue(by_id["eventlog"]["togglable"])
        self.assertFalse(by_id["developer"]["togglable"])


if __name__ == "__main__":
    unittest.main()
