import argparse
import json
import platform
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "config" / "mcp_servers.json"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "reports" / "mcp"


class StageError(Exception):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def load_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


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


def _decode_response(response, deadline, expected_id):
    body = _read_body(response, deadline).decode("utf-8")
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/event-stream" in content_type:
        messages = _sse_messages(body)
        for message in messages:
            if isinstance(message, dict) and message.get("id") == expected_id:
                return message
        if messages:
            return messages[0]
        raise ValueError("SSE response contains no data events")
    return json.loads(body)


def _post(url, message, timeout, session_id=None, expect_response=True):
    deadline = time.monotonic() + timeout
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(
        url,
        data=json.dumps(message).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_remaining(deadline)) as response:
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


def test_server(entry, timeout, host="127.0.0.1"):
    started = time.monotonic()
    name = entry.get("name", "<unknown>") if isinstance(entry, dict) else "<unknown>"
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
        url = f"http://{host}:{port}/mcp"
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
        return {
            "name": name,
            "status": "passed",
            "tool_count": len(tools),
            "tools": tools,
            "health": health_result,
            "duration_seconds": time.monotonic() - started,
        }
    except StageError as exc:
        return {
            "name": name,
            "status": "failed",
            "failed_stage": exc.stage,
            "error": str(exc),
            "duration_seconds": time.monotonic() - started,
        }
    except Exception as exc:
        return {
            "name": name,
            "status": "failed",
            "failed_stage": stage,
            "error": str(exc),
            "duration_seconds": time.monotonic() - started,
        }


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
    servers = _without_raw_headers(results)
    passed = sum(result.get("status") == "passed" for result in servers)
    return {
        "started_at": _utc_text(started_at),
        "ended_at": _utc_text(ended_at),
        "duration_seconds": (ended_at - started_at).total_seconds(),
        "runtime": {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
        },
        "summary": {
            "total": len(servers),
            "passed": passed,
            "failed": len(servers) - passed,
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
        stage = server.get("failed_stage", "-")
        tools = server.get("tool_count", "-")
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
                    f"- Stage: `{server.get('failed_stage', 'unknown')}`",
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


def _atomic_write(path, content):
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def write_reports(report, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ended_at = datetime.fromisoformat(report["ended_at"].replace("Z", "+00:00"))
    timestamp = ended_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"mcp-test-{timestamp}"
    suffix = 0
    while True:
        candidate = base if suffix == 0 else f"{base}-{suffix}"
        json_path = output_dir / f"{candidate}.json"
        markdown_path = output_dir / f"{candidate}.md"
        if not json_path.exists() and not markdown_path.exists():
            break
        suffix += 1

    _atomic_write(json_path, json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    _atomic_write(markdown_path, _render_markdown(report))
    return json_path, markdown_path


def _positive_seconds(value):
    seconds = float(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than zero")
    return seconds


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
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"manifest server {index} must be an object")
        name = entry.get("name")
        port = entry.get("port")
        health_tool = entry.get("health_tool")
        if not isinstance(name, str) or not name:
            raise ValueError(f"manifest server {index} has an invalid name")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError(f"manifest server {index} has an invalid port")
        if not isinstance(health_tool, str) or not health_tool:
            raise ValueError(f"manifest server {index} has an invalid health_tool")


def _print_summary(results, report_paths):
    for result in results:
        label = "PASS" if result.get("status") == "passed" else "FAIL"
        detail = ""
        if label == "FAIL":
            detail = (
                f" ({result.get('failed_stage', 'unknown')}: "
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
        entries = load_manifest(args.manifest)
        _validate_manifest(entries)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Manifest error ({args.manifest}): {exc}", file=sys.stderr)
        return 2

    started_at = datetime.now(timezone.utc)
    results = run_all(entries, timeout=args.timeout, host=args.host)
    ended_at = datetime.now(timezone.utc)
    report = build_report(results, started_at, ended_at)
    try:
        report_paths = write_reports(report, args.output_dir)
    except OSError as exc:
        _print_summary(results, None)
        print(f"Report error: {exc}", file=sys.stderr)
        return 2

    _print_summary(results, report_paths)
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
