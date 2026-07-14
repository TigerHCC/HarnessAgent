import json
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "config" / "mcp_servers.json"


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
        if content_type == "resource" and isinstance(item.get("resource"), dict):
            continue
        raise StageError(stage, f"malformed health content item: {content_type!r}")


def test_server(entry, timeout):
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
        url = f"http://127.0.0.1:{port}/mcp"
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


def run_all(entries, timeout):
    return [test_server(entry, timeout) for entry in entries]


def main(argv=None) -> int:
    del argv
    results = run_all(load_manifest(DEFAULT_MANIFEST), timeout=10)
    return 0 if all(result["status"] == "passed" for result in results) else 1
