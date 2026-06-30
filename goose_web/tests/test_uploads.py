import os, sys, tempfile, unittest
from pathlib import Path

# Point WORKSPACE at a temp dir BEFORE importing server (module resolves it at import).
_TMP = tempfile.mkdtemp(prefix="gw_test_")
os.environ["GOOSE_WEB_WORKSPACE"] = _TMP
os.environ["GOOSE_WEB_HOST"] = "127.0.0.1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402


class SafeName(unittest.TestCase):
    def test_strips_directories_and_traversal(self):
        self.assertEqual(server._safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(server._safe_name(r"C:\Windows\system32\cmd.exe"), "cmd.exe")
        self.assertEqual(server._safe_name("a/b/c/report.pdf"), "report.pdf")

    def test_allows_safe_chars_and_replaces_others(self):
        self.assertEqual(server._safe_name("my report (1).pdf"), "my report (1).pdf")
        self.assertEqual(server._safe_name("wei?rd*na:me.txt"), "wei_rd_na_me.txt")

    def test_strips_leading_dots_and_empty_fallback(self):
        self.assertEqual(server._safe_name("...hidden"), "hidden")
        self.assertEqual(server._safe_name(""), "file")
        self.assertEqual(server._safe_name("/"), "file")


class UploadDir(unittest.TestCase):
    def test_contained_under_workspace_uploads(self):
        d = server._session_upload_dir("web-1")
        root = (server.WORKSPACE / server.UPLOADS_SUBDIR).resolve()
        self.assertTrue(str(d).startswith(str(root)))

    def test_session_is_sanitized(self):
        d = server._session_upload_dir("../evil")
        root = (server.WORKSPACE / server.UPLOADS_SUBDIR).resolve()
        self.assertTrue(str(d).startswith(str(root)))


class Compose(unittest.TestCase):
    def _mk(self, session, name, content=b"hi"):
        d = server._session_upload_dir(session)
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(content)

    def test_no_attachments_returns_message_unchanged(self):
        self.assertEqual(server._compose_message("hello", "s1", []), "hello")
        self.assertEqual(server._compose_message("hello", "s1", None), "hello")

    def test_injects_existing_files_only(self):
        self._mk("s2", "a.txt")
        out = server._compose_message("read it", "s2", ["a.txt", "missing.txt"])
        self.assertIn("[附加檔案 (相對於工作目錄):]", out)
        self.assertIn("uploads/s2/a.txt", out)
        self.assertNotIn("missing.txt", out)
        self.assertTrue(out.startswith("read it"))

    def test_empty_message_uses_default_prompt(self):
        self._mk("s3", "b.txt")
        out = server._compose_message("", "s3", ["b.txt"])
        self.assertTrue(out.startswith("請查看我附加的檔案。"))

    def test_attachment_name_is_sanitized_on_lookup(self):
        self._mk("s4", "c.txt")
        out = server._compose_message("x", "s4", ["../c.txt"])  # resolves to c.txt in dir
        self.assertIn("uploads/s4/c.txt", out)


if __name__ == "__main__":
    unittest.main()
