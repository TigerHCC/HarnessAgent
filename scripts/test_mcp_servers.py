import argparse
import ipaddress
import json
import math
import platform
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "config" / "mcp_servers.json"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "mcp"
MAX_REDIRECTS = 5
MANIFEST_FIELDS = {
    "name", "directory", "port", "task", "run_level", "description", "health_tool"
}
CANONICAL_PORTS = set(range(8777, 8791))


class StageError(Exception):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def load_manifest(path):
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    _validate_manifest(entries)
    return entries


def _result(response, stage, expected_id):
    if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
        raise StageError(stage, "malformed JSON-RPC response")
    if "id" not in response or response["id"] != expected_id:
        raise StageError(stage, f"JSON-RPC response id does not match {expected_id!r}")
    if "error" in response:
        error = response["error"]
        message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        raise StageError(stage, f"JSON-RPC error: {message}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise StageError(stage, "JSON-RPC result must be an object")
    return result


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("MCP request deadline exceeded")
    return remaining


def _set_read_timeout(response, timeout):
    raw = getattr(getattr(response, "fp", None), "raw", None)
    sock = getattr(raw, "_sock", None)
    if sock is not None:
        sock.settimeout(timeout)


def _read_body(response, deadline):
    chunks = []
    read = getattr(response, "read1", None)
    if read is None:
        read = lambda size: response.read(1)
    while True:
        remaining = _remaining(deadline)
        _set_read_timeout(response, remaining)
        chunk = read(8192)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _sse_messages(body):
    messages = []
    data_lines = []
    for line in body.splitlines():
        if not line:
            if data_lines:
                messages.append(json.loads("\n".join(data_lines)))
                data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field == "data" and separator:
            data_lines.append(value[1:] if value.startswith(" ") else value)
    if data_lines:
        messages.append(json.loads("\n".join(data_lines)))
    return messages


def _read_sse_response(response, deadline, expected_id):
    messages = []
    data_lines = []
    while True:
        _set_read_timeout(response, _remaining(deadline))
        raw_line = response.readline()
        if not raw_line:
            if data_lines:
                messages.append(json.loads("\n".join(data_lines)))
            break
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if data_lines:
                message = json.loads("\n".join(data_lines))
                messages.append(message)
                data_lines = []
                if isinstance(message, dict) and message.get("id") == expected_id:
                    return message
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field == "data" and separator:
            data_lines.append(value[1:] if value.startswith(" ") else value)
    for message in messages:
        if isinstance(message, dict) and message.get("id") == expected_id:
            return message
    if messages:
        return messages[0]
    raise ValueError("SSE response contains no data events")


def _decode_response(response, deadline, expected_id):
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/event-stream" in content_type:
        return _read_sse_response(response, deadline, expected_id)
    body = _read_body(response, deadline).decode("utf-8")
    return json.loads(body)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _redirect_target(current_url, location):
    if not location:
        raise ValueError("redirect response lacks Location header")
    target, _ = urllib.parse.urldefrag(urllib.parse.urljoin(current_url, location))
    parsed = urllib.parse.urlsplit(target)
    if parsed.scheme != "http" or parsed.username or parsed.password:
        raise ValueError("redirect target must be a loopback HTTP URL")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as exc:
        raise ValueError("redirect target must use a loopback IP address") from exc
    if not address.is_loopback:
        raise ValueError("redirect target must use a loopback IP address")
    return target


def _post(url, message, timeout, session_id=None, expect_response=True):
    deadline = time.monotonic() + timeout
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    body = json.dumps(message).encode("utf-8")
    current_url = url
    visited = {current_url}
    redirect_count = 0
    opener = urllib.request.build_opener(_NoRedirectHandler())
    while True:
        request = urllib.request.Request(
            current_url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            response = opener.open(request, timeout=_remaining(deadline))
        except urllib.error.HTTPError as exc:
            if exc.code not in {307, 308}:
                raise
            try:
                target = _redirect_target(current_url, exc.headers.get("Location"))
            finally:
                exc.close()
            if target in visited:
                raise ValueError("redirect loop detected")
            if redirect_count >= MAX_REDIRECTS:
                raise ValueError(f"too many redirects (maximum {MAX_REDIRECTS})")
            visited.add(target)
            redirect_count += 1
            current_url = target
            continue
        except urllib.error.URLError as exc:
            if redirect_count:
                raise OSError(f"redirect target request failed: {exc}") from exc
            raise

        with response:
            if not expect_response:
                return None, response.headers.get("Mcp-Session-Id")
            expected_id = message["id"]
            return (
                _decode_response(response, deadline, expected_id),
                response.headers.get("Mcp-Session-Id"),
            )


def _validate_health_content(content, stage):
    if not isinstance(content, list) or not content:
        raise StageError(stage, "health result.content must be a non-empty list")
    for item in content:
        if not isinstance(item, dict):
            raise StageError(stage, "health content item must be an object")
        content_type = item.get("type")
        if content_type == "text" and isinstance(item.get("text"), str):
            continue
        if content_type in {"image", "audio"} and all(
            isinstance(item.get(field), str) for field in ("data", "mimeType")
        ):
            continue
        if content_type == "resource":
            resource = item.get("resource")
            if isinstance(resource, dict):
                uri = resource.get("uri")
                has_body = isinstance(resource.get("text"), str) or isinstance(
                    resource.get("blob"), str
                )
                mime_type = resource.get("mimeType")
                if (
                    isinstance(uri, str)
                    and uri
                    and has_body
                    and (mime_type is None or isinstance(mime_type, str))
                ):
                    continue
        raise StageError(stage, f"malformed health content item: {content_type!r}")


def _endpoint(host, port):
    url_host = host if ":" not in host or host.startswith("[") else f"[{host}]"
    return f"http://{url_host}:{port}/mcp"


def _server_record(
    name, port, endpoint, health_tool, status, stage, duration, tools, health, error
):
    return {
        "name": name,
        "port": port,
        "endpoint": endpoint,
        "health_tool": health_tool,
        "status": status,
        "stage": stage,
        "duration_seconds": duration,
        "tools": tools,
        "tool_count": len(tools),
        "health": health,
        "error": error,
    }


def test_server(entry, timeout, host="127.0.0.1"):
    started = time.monotonic()
    name = entry.get("name", "<unknown>") if isinstance(entry, dict) else "<unknown>"
    port = entry.get("port") if isinstance(entry, dict) else None
    health_tool = entry.get("health_tool") if isinstance(entry, dict) else None
    endpoint = None
    tools = []
    health_result = None
    stage = "connect"
    try:
        if not isinstance(entry, dict):
            raise StageError(stage, "server entry must be an object")
        try:
            port = entry["port"]
            health_tool = entry["health_tool"]
        except KeyError as exc:
            raise StageError(stage, f"server entry lacks {exc.args[0]!r}") from exc
        if not isinstance(name, str) or not name:
            raise StageError(stage, "server entry name must be a non-empty string")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise StageError(stage, "server entry port must be an integer from 1 to 65535")
        if not isinstance(health_tool, str) or not health_tool:
            raise StageError(stage, "server health_tool must be a non-empty string")
        url = _endpoint(host, port)
        endpoint = url
        stage = "initialize"
        try:
            initialized, session_id = _post(
                url,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "mcp-batch-test", "version": "1"},
                    },
                },
                timeout,
            )
        except urllib.error.HTTPError:
            raise
        except urllib.error.URLError as exc:
            raise StageError("connect", str(exc)) from exc
        initialize_result = _result(initialized, stage, 1)
        if not isinstance(initialize_result.get("protocolVersion"), str):
            raise StageError(stage, "initialize result lacks protocolVersion")
        _post(
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            timeout,
            session_id,
            expect_response=False,
        )
        stage = "tools_list"
        listed, _ = _post(
            url,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            timeout,
            session_id,
        )
        list_result = _result(listed, stage, 2)
        listed_tools = list_result.get("tools")
        if not isinstance(listed_tools, list):
            raise StageError(stage, "tools/list result.tools must be a list")
        try:
            tools = [tool["name"] for tool in listed_tools]
        except (KeyError, TypeError) as exc:
            raise StageError(stage, "tools/list contains a malformed tool") from exc
        if health_tool not in tools:
            raise StageError(stage, f"health tool {health_tool!r} not found")
        stage = "health_call"
        health, _ = _post(
            url,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": health_tool, "arguments": {}},
            },
            timeout,
            session_id,
        )
        health_result = _result(health, stage, 3)
        if health_result.get("isError") is True:
            raise StageError(stage, "health tool returned isError")
        content = health_result.get("content")
        _validate_health_content(content, stage)
        return _server_record(
            name,
            port,
            endpoint,
            health_tool,
            "passed",
            stage,
            time.monotonic() - started,
            tools,
            health_result,
            None,
        )
    except StageError as exc:
        return _server_record(
            name,
            port,
            endpoint,
            health_tool,
            "failed",
            exc.stage,
            time.monotonic() - started,
            tools,
            health_result,
            str(exc),
        )
    except Exception as exc:
        return _server_record(
            name,
            port,
            endpoint,
            health_tool,
            "failed",
            stage,
            time.monotonic() - started,
            tools,
            health_result,
            str(exc),
        )


def run_all(entries, timeout, host="127.0.0.1"):
    return [test_server(entry, timeout, host=host) for entry in entries]


def _utc_text(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _without_raw_headers(value):
    if isinstance(value, dict):
        return {
            key: _without_raw_headers(item)
            for key, item in value.items()
            if key != "raw_headers"
        }
    if isinstance(value, list):
        return [_without_raw_headers(item) for item in value]
    return value


def build_report(results, started_at, ended_at):
    servers = [_without_raw_headers(result) for result in results]
    passed = sum(result.get("status") == "passed" for result in servers)
    duration = (ended_at - started_at).total_seconds()
    return {
        "schema_version": "1.0",
        "started_at": _utc_text(started_at),
        "ended_at": _utc_text(ended_at),
        "duration_seconds": duration,
        "runtime": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
        },
        "summary": {
            "total": len(servers),
            "passed": passed,
            "failed": len(servers) - passed,
            "duration_seconds": duration,
        },
        "servers": servers,
    }


def _render_markdown(report):
    summary = report["summary"]
    lines = [
        "# MCP Batch Test Report",
        "",
        f"Started: `{report['started_at']}`  ",
        f"Ended: `{report['ended_at']}`  ",
        f"Duration: `{report['duration_seconds']:.3f}s`  ",
        (
            f"Result: **{summary['passed']} passed, {summary['failed']} failed, "
            f"{summary['total']} total**"
        ),
        "",
        "| Server | Status | Stage | Tools | Duration |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for server in report["servers"]:
        status = "PASS" if server.get("status") == "passed" else "FAIL"
        stage = server.get("stage", "-")
        tools = len(server.get("tools") or [])
        duration = server.get("duration_seconds", 0)
        lines.append(
            f"| {server.get('name', '<unknown>')} | {status} | {stage} | "
            f"{tools} | {duration:.3f}s |"
        )

    failures = [s for s in report["servers"] if s.get("status") != "passed"]
    if failures:
        lines.extend(["", "## Failure Details"])
        for server in failures:
            lines.extend(
                [
                    "",
                    f"### {server.get('name', '<unknown>')}",
                    "",
                    f"- Stage: `{server.get('stage', 'unknown')}`",
                    f"- Error: {server.get('error', 'unknown error')}",
                ]
            )

    for server in report["servers"]:
        if "health" not in server:
            continue
        lines.extend(
            [
                "",
                f"## Health: {server.get('name', '<unknown>')}",
                "",
                "```json",
                json.dumps(server["health"], indent=2, ensure_ascii=True),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def _write_temporary_sibling(path, content):
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
        return temporary_path
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _reserve_report_paths(output_dir, base):
    suffix = 0
    while True:
        candidate = base if suffix == 0 else f"{base}-{suffix}"
        json_path = output_dir / f"{candidate}.json"
        markdown_path = output_dir / f"{candidate}.md"
        reservation_path = output_dir / f".{candidate}.lock"
        try:
            with reservation_path.open("x", encoding="utf-8"):
                pass
        except FileExistsError:
            suffix += 1
            continue
        if json_path.exists() or markdown_path.exists():
            reservation_path.unlink(missing_ok=True)
            suffix += 1
            continue
        return json_path, markdown_path, reservation_path


def write_reports(report, output_dir):
    json_content = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    markdown_content = _render_markdown(report)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ended_at = datetime.fromisoformat(report["ended_at"].replace("Z", "+00:00"))
    timestamp = ended_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"mcp-test-{timestamp}"
    json_path, markdown_path, reservation_path = _reserve_report_paths(output_dir, base)
    temporary_paths = []
    try:
        temporary_paths.append(_write_temporary_sibling(json_path, json_content))
        temporary_paths.append(
            _write_temporary_sibling(markdown_path, markdown_content)
        )
        temporary_paths[0].replace(json_path)
        temporary_paths[1].replace(markdown_path)
    except Exception:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)
        markdown_path.unlink(missing_ok=True)
        raise
    finally:
        reservation_path.unlink(missing_ok=True)
    return json_path, markdown_path


def _positive_seconds(value):
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than zero")
    return seconds


def _validate_host(host):
    if host not in {"127.0.0.1", "::1", "[::1]"}:
        raise ValueError("host must be a loopback address")


def _parse_args(argv):
    parser = argparse.ArgumentParser(description="Test all configured MCP servers")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=_positive_seconds, default=15.0)
    parser.add_argument("--host", default="127.0.0.1")
    return parser.parse_args(argv)


def _validate_manifest(entries):
    if not isinstance(entries, list):
        raise ValueError("manifest root must be a list")
    if len(entries) != 14:
        raise ValueError(f"manifest must contain exactly 14 entries; found {len(entries)}")
    names = set()
    ports = set()
    tasks = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"manifest server {index} must be an object")
        for field in MANIFEST_FIELDS:
            if field not in entry:
                raise ValueError(f"manifest server {index} is missing required field {field}")
        name = entry["name"]
        port = entry.get("port")
        for field in MANIFEST_FIELDS - {"port"}:
            if not isinstance(entry[field], str) or not entry[field].strip():
                raise ValueError(f"manifest server {index} has an invalid {field}")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError(f"manifest server {index} has an invalid port")
        if entry["run_level"] not in {"Highest", "Limited"}:
            raise ValueError(f"manifest server {index} has an invalid run_level")
        if name in names:
            raise ValueError(f"manifest contains duplicate name: {name}")
        if port in ports:
            raise ValueError(f"manifest contains duplicate port: {port}")
        if entry["task"] in tasks:
            raise ValueError(f"manifest contains duplicate task: {entry['task']}")
        names.add(name)
        ports.add(port)
        tasks.add(entry["task"])
    if ports != CANONICAL_PORTS:
        raise ValueError("manifest must use canonical ports 8777-8790 exactly once")


def _print_summary(results, report_paths):
    for result in results:
        label = "PASS" if result.get("status") == "passed" else "FAIL"
        detail = ""
        if label == "FAIL":
            detail = (
                f" ({result.get('stage', 'unknown')}: "
                f"{result.get('error', 'unknown error')})"
            )
        print(f"[{label}] {result.get('name', '<unknown>')}{detail}")
    passed = sum(result.get("status") == "passed" for result in results)
    failed = len(results) - passed
    print(f"{passed} passed, {failed} failed, {len(results)} total")
    if report_paths:
        print(f"JSON report: {report_paths[0]}")
        print(f"Markdown report: {report_paths[1]}")


def main(argv=None) -> int:
    args = _parse_args(argv)
    try:
        _validate_host(args.host)
    except ValueError as exc:
        print(f"Host error: {exc}", file=sys.stderr)
        return 2
    try:
        entries = load_manifest(args.manifest)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Manifest error ({args.manifest}): {exc}", file=sys.stderr)
        return 2

    started_at = datetime.now(timezone.utc)
    results = run_all(entries, timeout=args.timeout, host=args.host)
    ended_at = datetime.now(timezone.utc)
    try:
        report = build_report(results, started_at, ended_at)
        report_paths = write_reports(report, args.output_dir)
    except Exception as exc:
        _print_summary(results, None)
        print(f"Report error: {exc}", file=sys.stderr)
        return 2

    _print_summary(results, report_paths)
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
