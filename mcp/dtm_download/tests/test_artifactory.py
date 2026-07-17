import hashlib
import json
import os

import pytest

import artifactory


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b""):
        self.status_code = status_code
        self._json_body = json_body
        self._content = content

    def json(self):
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body

    def iter_content(self, chunk_size=1):
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
