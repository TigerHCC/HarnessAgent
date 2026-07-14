import json
import time
import urllib.request
from pathlib import Path


DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "config" / "mcp_servers.json"


class StageError(Exception):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def load_manifest(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _result(response, stage):
    if not isinstance(response, dict) or response.get("jsonrpc") != "2.0":
        raise StageError(stage, "malformed JSON-RPC response")
    if "error" in response:
        error = response["error"]
        message = error.get("message", str(error)) if isinstance(error, dict) else str(error)
        raise StageError(stage, f"JSON-RPC error: {message}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise StageError(stage, "JSON-RPC result must be an object")
    return result


def _decode_response(response):
    body = response.read().decode("utf-8")
    content_type = response.headers.get("Content-Type", "").lower()
    if "text/event-stream" in content_type:
        data_lines = [
            line[5:].lstrip() for line in body.splitlines() if line.startswith("data:")
        ]
        body = "\n".join(data_lines)
    return json.loads(body)


def _post(url, message, timeout, session_id=None, expect_response=True):
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
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if not expect_response:
            return None, response.headers.get("Mcp-Session-Id")
        return _decode_response(response), response.headers.get("Mcp-Session-Id")


def test_server(entry, timeout):
    started = time.monotonic()
    name = entry["name"]
    url = f"http://127.0.0.1:{entry['port']}/mcp"
    stage = "connect"
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
        stage = "initialize"
        initialize_result = _result(initialized, stage)
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
        list_result = _result(listed, stage)
        listed_tools = list_result.get("tools")
        if not isinstance(listed_tools, list):
            raise StageError(stage, "tools/list result.tools must be a list")
        try:
            tools = [tool["name"] for tool in listed_tools]
        except (KeyError, TypeError) as exc:
            raise StageError(stage, "tools/list contains a malformed tool") from exc
        if entry["health_tool"] not in tools:
            raise StageError(stage, f"health tool {entry['health_tool']!r} not found")
        stage = "health_call"
        health, _ = _post(
            url,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": entry["health_tool"], "arguments": {}},
            },
            timeout,
            session_id,
        )
        health_result = _result(health, stage)
        if health_result.get("isError") is True:
            raise StageError(stage, "health tool returned isError")
        content = health_result.get("content")
        if not isinstance(content, list) or not all(
            isinstance(item, dict) for item in content
        ):
            raise StageError(stage, "health result.content must be a list of objects")
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
