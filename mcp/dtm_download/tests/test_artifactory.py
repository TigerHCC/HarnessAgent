import hashlib
import json
import os

import pytest

import artifactory


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b"", headers=None, chunks=None):
        self.status_code = status_code
        self._json_body = json_body
        self._content = content
        self._chunks = chunks          # optional list of byte chunks for iter_content
        self.headers = headers or {}

    def json(self):
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body

    def iter_content(self, chunk_size=1):
        if self._chunks is not None:
            for c in self._chunks:
                yield c
        else:
            yield self._content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_resolve_latest_build(monkeypatch):
    body = {"children": [
        {"uri": "/Daily-260101-1.0.0.100", "folder": True},
        {"uri": "/Daily-260102-1.0.0.200", "folder": True},
        {"uri": "/not-a-build", "folder": True},
        {"uri": "/somefile.txt", "folder": False},
    ]}
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: FakeResponse(json_body=body))
    latest = artifactory.resolve_latest_build("https://x", "repo", "Daily", "tok")
    assert latest == "Daily-260102-1.0.0.200"


def test_resolve_latest_build_none_found(monkeypatch):
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: FakeResponse(json_body={"children": []}))
    with pytest.raises(artifactory.ArtifactoryError):
        artifactory.resolve_latest_build("https://x", "repo", "Daily", "tok")


def test_api_get_401_raises(monkeypatch):
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: FakeResponse(status_code=401))
    with pytest.raises(artifactory.ArtifactoryError):
        artifactory.api_get("https://x", "some/path", "bad-token")


def test_discover_zip_files_applies_filter(monkeypatch):
    body = {"children": [
        {"uri": "/DTPInstallers_x64_Release.zip", "folder": False},
        {"uri": "/DTPSamples_x64_Release.zip", "folder": False},
        {"uri": "/unrelated.zip", "folder": False},
        {"uri": "/docs.html", "folder": False},
    ]}
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: FakeResponse(json_body=body))
    names = artifactory.discover_zip_files("https://x", "repo/DTP/Daily/build", "tok",
                                           ["*DTPInstallers*", "*DTPSamples*"])
    assert names == ["DTPInstallers_x64_Release.zip", "DTPSamples_x64_Release.zip"]


def test_download_file_writes_content(monkeypatch, tmp_path):
    content = b"hello world"
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: FakeResponse(content=content))
    out = tmp_path / "sub" / "file.zip"
    artifactory.download_file("https://x", "repo/file.zip", "tok", str(out))
    assert out.read_bytes() == content


def test_verify_checksum_matches(tmp_path, monkeypatch):
    data = b"payload"
    f = tmp_path / "f.bin"
    f.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    body = {"checksums": {"sha256": sha}}
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: FakeResponse(json_body=body))
    ok, detail = artifactory.verify_checksum("https://x", "repo/f.bin", "tok", str(f))
    assert ok is True
    assert "verified" in detail


def test_verify_checksum_mismatch(tmp_path, monkeypatch):
    f = tmp_path / "f.bin"
    f.write_bytes(b"payload")
    body = {"checksums": {"sha256": "0" * 64}}
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: FakeResponse(json_body=body))
    ok, detail = artifactory.verify_checksum("https://x", "repo/f.bin", "tok", str(f))
    assert ok is False
    assert "mismatch" in detail


def test_verify_checksum_skips_when_absent(tmp_path, monkeypatch):
    f = tmp_path / "f.bin"
    f.write_bytes(b"payload")
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: FakeResponse(json_body={}))
    ok, detail = artifactory.verify_checksum("https://x", "repo/f.bin", "tok", str(f))
    assert ok is True
    assert "skipped" in detail


def test_extract_zip(tmp_path):
    import zipfile
    zpath = tmp_path / "a.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("inner/file.txt", "content")
    dest = tmp_path / "out"
    count = artifactory.extract_zip(str(zpath), str(dest))
    assert count == 1
    assert (dest / "inner" / "file.txt").read_text() == "content"


def test_download_build_requires_token():
    with pytest.raises(artifactory.ArtifactoryError):
        artifactory.download_build({"artifactory_base_url": "https://x", "repo": "r",
                                    "download_path": "d"}, token="")


def test_download_file_emits_progress_with_percent(monkeypatch, tmp_path, capsys):
    # shrink the step so tiny fake chunks cross it
    monkeypatch.setattr(artifactory, "_PROGRESS_STEP", 4)
    chunks = [b"aaaa", b"bbbb", b"cc"]          # 10 bytes total
    resp = FakeResponse(content=b"", chunks=chunks, headers={"Content-Length": "10"})
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: resp)
    out = tmp_path / "f.zip"
    got = []
    artifactory.download_file("https://x", "repo/f.zip", "tok", str(out),
                              label="f.zip", log=got.append)
    assert out.read_bytes() == b"aaaabbbbcc"
    assert any("%" in line and "f.zip" in line for line in got)      # percent shown
    assert any("f.zip" in line for line in capsys.readouterr().out.splitlines())


def test_download_file_progress_without_content_length(monkeypatch, tmp_path):
    monkeypatch.setattr(artifactory, "_PROGRESS_STEP", 4)
    resp = FakeResponse(chunks=[b"aaaa", b"bbbb"], headers={})
    monkeypatch.setattr(artifactory.requests, "get", lambda *a, **k: resp)
    got = []
    artifactory.download_file("https://x", "repo/f.zip", "tok", str(tmp_path / "f.zip"),
                              label="f.zip", log=got.append)
    assert got and all("%" not in line for line in got)              # cumulative-only format


def test_download_file_defaults_unchanged(monkeypatch, tmp_path):
    # no label/log: behaves exactly as before (writes content, returns path)
    monkeypatch.setattr(artifactory.requests, "get",
                        lambda *a, **k: FakeResponse(content=b"hello"))
    out = tmp_path / "f.zip"
    assert artifactory.download_file("https://x", "repo/f.zip", "tok", str(out)) == str(out)
    assert out.read_bytes() == b"hello"


def test_dllog_writes_both_and_survives_write_failure(tmp_path, capsys):
    p = tmp_path / "download.log"
    dlog = artifactory._DlLog(str(p))
    dlog.emit("[dl] line one")
    dlog._fh.close()                              # force subsequent writes to fail
    dlog.emit("[dl] line two")                    # must not raise; warns once, disables file
    dlog.emit("[dl] line three")
    dlog.close()
    assert "[dl] line one" in p.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert out.count("could not write") == 1      # one-time warning
    assert "[dl] line three" in out               # stdout still gets every line


def test_download_build_writes_download_log(monkeypatch, tmp_path):
    cfg = {"artifactory_base_url": "https://x", "repo": "r", "download_path": str(tmp_path),
           "zip_filter": ["*"], "csv_files": [], "html_files": [], "default_channel": "Daily"}
    monkeypatch.setattr(artifactory, "resolve_latest_build", lambda *a, **k: "B1")
    monkeypatch.setattr(artifactory, "discover_zip_files", lambda *a, **k: ["a.zip"])

    def fake_download(base, path, tok, out, timeout=600, label="", log=None):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(b"x")
        return out
    monkeypatch.setattr(artifactory, "download_file", fake_download)
    monkeypatch.setattr(artifactory, "verify_checksum", lambda *a, **k: (True, "SHA256 verified"))
    monkeypatch.setattr(artifactory, "extract_zip", lambda *a, **k: 3)
    res = artifactory.download_build(cfg, "tok")
    log_text = (tmp_path / "B1" / "download.log").read_text(encoding="utf-8")
    assert "(1/1) a.zip" in log_text              # per-file start line
    assert "done" in log_text                     # completion line
    assert res["build_id"] == "B1"


def test_download_build_lines_print_exactly_once(monkeypatch, tmp_path, capsys):
    cfg = {"artifactory_base_url": "https://x", "repo": "r", "download_path": str(tmp_path),
           "zip_filter": ["*"], "csv_files": [], "html_files": [], "default_channel": "Daily"}
    monkeypatch.setattr(artifactory, "resolve_latest_build", lambda *a, **k: "B1")
    monkeypatch.setattr(artifactory, "discover_zip_files", lambda *a, **k: ["a.zip"])
    monkeypatch.setattr(artifactory, "_PROGRESS_STEP", 4)

    def fake_get(*a, **k):
        return FakeResponse(chunks=[b"aaaa", b"bbbb"], headers={"Content-Length": "8"})
    monkeypatch.setattr(artifactory.requests, "get", fake_get)   # real download_file runs
    monkeypatch.setattr(artifactory, "verify_checksum", lambda *a, **k: (True, "SHA256 verified"))
    monkeypatch.setattr(artifactory, "extract_zip", lambda *a, **k: 3)

    artifactory.download_build(cfg, "tok")
    out_lines = capsys.readouterr().out.splitlines()
    log_lines = (tmp_path / "B1" / "download.log").read_text(encoding="utf-8").splitlines()
    # the per-file start line and each chunk-progress line: exactly once on stdout AND in the log
    start = [l for l in out_lines if "(1/1) a.zip" in l and "done" not in l]
    assert len(start) == 1
    progress = [l for l in out_lines if "%" in l]
    assert progress                                   # chunk lines exist
    for line in set(progress + start):
        assert out_lines.count(line) == 1, line
        assert log_lines.count(line) == 1, line
