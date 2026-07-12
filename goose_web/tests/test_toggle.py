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


if __name__ == "__main__":
    unittest.main()
