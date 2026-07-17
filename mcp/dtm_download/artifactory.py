"""Pure-Python reimplementation of the Artifactory download logic in
ccp/tools/DTMTransmissionAutoTest-/Install-DTP.ps1 (curl + Invoke-ArtifactoryApi + Confirm-FileChecksum
+ zip extraction). No subprocess/.ps1 dependency -- uses `requests` + stdlib `zipfile`/`hashlib`.
"""
import fnmatch
import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone

import requests
import urllib3

# Artifactory is reached with verify=False (mirrors the original script's `curl -k`), which would
# otherwise emit an InsecureRequestWarning on every call.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BUILD_VERSION_RE = None  # set lazily to avoid importing re at module scope twice


class ArtifactoryError(Exception):
    pass


def _headers(token):
    return {"Authorization": "Bearer %s" % token} if token else {}


def api_get(base_url, path, token, timeout=15):
    """GET an Artifactory REST API URL and return the parsed JSON body."""
    url = "%s/%s" % (base_url.rstrip("/"), path.lstrip("/"))
    try:
        resp = requests.get(url, headers=_headers(token), timeout=timeout, verify=False)
    except requests.RequestException as e:
        raise ArtifactoryError("request failed: %s (url=%s)" % (e, url))
    if resp.status_code == 401:
        raise ArtifactoryError("Authentication failed (HTTP 401). Token is invalid or expired.")
    if resp.status_code == 403:
        raise ArtifactoryError("Access denied (HTTP 403). Token lacks read permission.")
    if resp.status_code == 404:
        raise ArtifactoryError("Not found (HTTP 404). URL: %s" % url)
    if resp.status_code >= 400:
        raise ArtifactoryError("Artifactory request failed (HTTP %s). URL: %s" % (resp.status_code, url))
    try:
        return resp.json()
    except ValueError:
        raise ArtifactoryError("non-JSON response from Artifactory (url=%s)" % url)


def resolve_latest_build(base_url, repo, channel, token, timeout=15):
    """Return the highest-versioned build folder name under <repo>/DTP/<channel>/."""
    import re
    global BUILD_VERSION_RE
    if BUILD_VERSION_RE is None:
        BUILD_VERSION_RE = re.compile(r"-(\d+\.\d+\.\d+\.\d+)$")

    info = api_get(base_url, "api/storage/%s/DTP/%s" % (repo, channel), token, timeout=timeout)
    latest_name, latest_ver = None, None
    for child in info.get("children", []):
        if not child.get("folder"):
            continue
        name = child["uri"].lstrip("/")
        m = BUILD_VERSION_RE.search(name)
        if not m:
            continue
        ver = tuple(int(x) for x in m.group(1).split("."))
        if latest_ver is None or ver > latest_ver:
            latest_ver, latest_name = ver, name
    if latest_name is None:
        raise ArtifactoryError("No builds found in '%s' channel." % channel)
    return latest_name


def list_build_children(base_url, repo, channel, token, timeout=15):
    info = api_get(base_url, "api/storage/%s/DTP/%s" % (repo, channel), token, timeout=timeout)
    return [c["uri"].lstrip("/") for c in info.get("children", []) if c.get("folder")]


def _matches_any(name, patterns):
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def discover_zip_files(base_url, repo_path, token, zip_filter, timeout=15):
    info = api_get(base_url, "api/storage/%s" % repo_path, token, timeout=timeout)
    names = [c["uri"].lstrip("/") for c in info.get("children", []) if not c.get("folder")]
    return [n for n in names if n.lower().endswith(".zip") and _matches_any(n, zip_filter)]


def download_file(base_url, repo_path_file, token, out_file, timeout=600):
    url = "%s/%s" % (base_url.rstrip("/"), repo_path_file.lstrip("/"))
    os.makedirs(os.path.dirname(out_file), exist_ok=True)
    with requests.get(url, headers=_headers(token), timeout=timeout, stream=True, verify=False) as resp:
        if resp.status_code >= 400:
            raise ArtifactoryError("Download failed (HTTP %s): %s" % (resp.status_code, url))
        with open(out_file, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    return out_file


def verify_checksum(base_url, repo_path_file, token, file_path, timeout=15):
    """Returns (verified: bool, detail: str). Skips (verified=True) if no sha256 is published."""
    try:
        meta = api_get(base_url, "api/storage/%s" % repo_path_file, token, timeout=timeout)
    except ArtifactoryError as e:
        return True, "checksum skipped (could not fetch metadata: %s)" % e
    expected = (meta.get("checksums") or {}).get("sha256") or \
        (meta.get("originalChecksums") or {}).get("sha256")
    if not expected:
        return True, "checksum skipped (not available)"
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            sha256.update(chunk)
    actual = sha256.hexdigest().lower()
    if actual == expected.lower():
        return True, "SHA256 verified"
    return False, "checksum mismatch for %s (expected %s, got %s)" % (file_path, expected, actual)


def extract_zip(zip_path, dest_dir):
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir, ignore_errors=True)
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
    file_count = sum(len(files) for _, _, files in os.walk(dest_dir))
    return file_count


def download_build(cfg, token, channel=None, build_id=None):
    """Full orchestration mirroring Install-DTP.ps1's download section: resolve build, download +
    checksum-verify the sample/installer zips + CSV tables + optional HTML docs, extract zips, write a
    build-docs-manifest.json. Returns a summary dict; raises ArtifactoryError on hard failure.
    """
    if not token:
        raise ArtifactoryError("No Artifactory token provided (set DTM_DOWNLOAD_ARTIFACTORY_TOKEN).")

    base_url = cfg["artifactory_base_url"]
    repo = cfg["repo"]
    channel = channel or cfg.get("default_channel", "Daily")
    timeout = cfg.get("connect_timeout_seconds", 15)
    dl_timeout = cfg.get("download_timeout_seconds", 600)

    if not build_id:
        build_id = resolve_latest_build(base_url, repo, channel, token, timeout=timeout)

    repo_path = "%s/DTP/%s/%s" % (repo, channel, build_id)
    download_path = os.path.join(cfg["download_path"], build_id)
    if os.path.isdir(download_path):
        shutil.rmtree(download_path, ignore_errors=True)
    os.makedirs(download_path, exist_ok=True)

    zip_names = discover_zip_files(base_url, repo_path, token, cfg.get("zip_filter", []), timeout=timeout)
    if not zip_names:
        raise ArtifactoryError("No ZIPs matched filters in build folder '%s'." % repo_path)

    downloaded_zips, extracted = [], []
    for name in zip_names:
        out_file = os.path.join(download_path, name)
        download_file(base_url, "%s/%s" % (repo_path, name), token, out_file, timeout=dl_timeout)
        ok, detail = verify_checksum(base_url, "%s/%s" % (repo_path, name), token, out_file, timeout=timeout)
        if not ok:
            raise ArtifactoryError(detail)
        downloaded_zips.append({"name": name, "path": out_file, "checksum": detail})
        dest_name = os.path.splitext(name)[0]
        dest_path = os.path.join(download_path, dest_name)
        file_count = extract_zip(out_file, dest_path)
        extracted.append({"name": dest_name, "path": dest_path, "file_count": file_count})

    csv_results = []
    for csv_name in cfg.get("csv_files", []):
        out_file = os.path.join(download_path, csv_name)
        try:
            download_file(base_url, "%s/%s" % (repo_path, csv_name), token, out_file, timeout=dl_timeout)
            csv_results.append({"name": csv_name, "path": out_file, "ok": True})
        except ArtifactoryError as e:
            csv_results.append({"name": csv_name, "ok": False, "error": str(e)})

    html_results = []
    for entry in cfg.get("html_files", []):
        out_file = os.path.join(download_path, entry["file"])
        try:
            download_file(base_url, "%s/%s" % (repo_path, entry["file"]), token, out_file, timeout=dl_timeout)
            html_results.append({"name": entry["file"], "label": entry.get("label", ""), "path": out_file})
        except ArtifactoryError:
            continue  # HTML docs are optional

    if html_results:
        manifest = {
            "buildId": build_id, "channel": channel,
            "downloadedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "files": html_results,
        }
        with open(os.path.join(download_path, "build-docs-manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    msi_path = _find_msi(extracted, cfg.get("msi_name", "DTPforCustomer_x64_*.msi"))

    return {
        "channel": channel, "build_id": build_id, "download_path": download_path,
        "msi_path": msi_path, "zips": downloaded_zips, "extracted": extracted,
        "csv_files": csv_results, "html_files": html_results,
    }


def _find_msi(extracted, msi_name_glob):
    for entry in extracted:
        if not entry["name"].lower().startswith("dtpinstallers"):
            continue
        for cfg_dir in ("Release", "Debug"):
            candidate_dir = os.path.join(entry["path"], "DTPInstallers", cfg_dir)
            if not os.path.isdir(candidate_dir):
                continue
            for fname in os.listdir(candidate_dir):
                if fnmatch.fnmatch(fname, msi_name_glob):
                    return os.path.join(candidate_dir, fname)
    return None
