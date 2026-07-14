import importlib.util
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "scripts" / "test_mcp_servers.py"


def load_engine():
    spec = importlib.util.spec_from_file_location("test_mcp_servers", ENGINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeMcp:
    def __init__(self, content_type="application/json", mode="healthy"):
        self.content_type = content_type
        self.mode = mode
        self.methods = []
        self.session_headers = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                owner.methods.append(request["method"])
                owner.session_headers.append(self.headers.get("Mcp-Session-Id"))

                method = request["method"]
                if method == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return

                if method == "initialize":
                    response = {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "serverInfo": {"name": "fake", "version": "1"},
                        },
                    }
                    if owner.mode == "bad_initialize":
                        response["result"] = {"capabilities": {}}
                elif method == "tools/list":
                    tool_name = (
                        "different_health"
                        if owner.mode == "missing_health"
                        else "sample_health"
                    )
                    response = {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {
                            "tools": [{"name": tool_name, "inputSchema": {}}]
                        },
                    }
                elif method == "tools/call":
                    response = {
                        "jsonrpc": "2.0",
                        "id": request["id"],
                        "result": {
                            "content": [{"type": "text", "text": '{"ok": true}'}]
                        },
                    }
                    if owner.mode == "health_jsonrpc_error":
                        response.pop("result")
                        response["error"] = {"code": -32000, "message": "health failed"}
                    elif owner.mode == "health_is_error":
                        response["result"]["isError"] = True
                    elif owner.mode == "malformed_health":
                        response["result"] = {"content": "not-a-list"}
                else:
                    raise AssertionError(f"unexpected method {method}")

                body = json.dumps(response).encode()
                if owner.content_type == "text/event-stream":
                    body = b"event: message\n" + b"data: " + body + b"\n\n"
                self.send_response(200)
                self.send_header("Content-Type", owner.content_type)
                if method == "initialize":
                    self.send_header("Mcp-Session-Id", "test-session")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.fixture(params=["application/json", "text/event-stream"])
def fake_mcp(request):
    server = FakeMcp(content_type=request.param)
    yield server
    server.close()


@pytest.fixture
def fake_mcp_factory():
    servers = []

    def create(**kwargs):
        server = FakeMcp(**kwargs)
        servers.append(server)
        return server

    yield create
    for server in servers:
        server.close()


def test_server_completes_handshake_and_health(fake_mcp):
    module = load_engine()
    result = module.test_server(
        {"name": "sample", "port": fake_mcp.port, "health_tool": "sample_health"},
        timeout=2,
    )
    assert result["status"] == "passed"
    assert result["tool_count"] == 1
    assert result["tools"] == ["sample_health"]
    assert result["health"]["content"][0]["text"] == '{"ok": true}'
    assert result["duration_seconds"] >= 0
    assert fake_mcp.methods == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        "tools/call",
    ]
    assert fake_mcp.session_headers[1:] == ["test-session"] * 3


def test_server_classifies_connection_failure():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused_port = sock.getsockname()[1]

    result = load_engine().test_server(
        {"name": "sample", "port": unused_port, "health_tool": "sample_health"},
        timeout=0.2,
    )
    assert result["status"] == "failed"
    assert result["failed_stage"] == "connect"
    assert result["error"]


@pytest.mark.parametrize(
    "mode,stage",
    [
        ("bad_initialize", "initialize"),
        ("missing_health", "tools_list"),
        ("health_jsonrpc_error", "health_call"),
        ("health_is_error", "health_call"),
        ("malformed_health", "health_call"),
    ],
)
def test_server_classifies_protocol_failures(fake_mcp_factory, mode, stage):
    server = fake_mcp_factory(mode=mode)
    result = load_engine().test_server(
        {"name": "sample", "port": server.port, "health_tool": "sample_health"}, 2
    )
    assert result["status"] == "failed"
    assert result["failed_stage"] == stage
    assert result["error"]


def test_run_all_isolates_server_failures(fake_mcp_factory):
    healthy = fake_mcp_factory()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused_port = sock.getsockname()[1]

    entries = [
        {"name": "offline", "port": unused_port, "health_tool": "sample_health"},
        {"name": "healthy", "port": healthy.port, "health_tool": "sample_health"},
    ]
    results = load_engine().run_all(entries, timeout=0.2)

    assert [result["name"] for result in results] == ["offline", "healthy"]
    assert [result["status"] for result in results] == ["failed", "passed"]


def test_load_manifest_reads_json_entries(tmp_path):
    manifest = tmp_path / "servers.json"
    entries = [{"name": "sample", "port": 1234, "health_tool": "sample_health"}]
    manifest.write_text(json.dumps(entries), encoding="utf-8")

    assert load_engine().load_manifest(manifest) == entries


def test_main_returns_success_for_default_manifest(fake_mcp_factory, tmp_path):
    server = fake_mcp_factory()
    manifest = tmp_path / "servers.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "name": "sample",
                    "port": server.port,
                    "health_tool": "sample_health",
                }
            ]
        ),
        encoding="utf-8",
    )
    module = load_engine()
    module.DEFAULT_MANIFEST = manifest

    assert module.main([]) == 0


def test_main_returns_failure_when_a_default_server_fails(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused_port = sock.getsockname()[1]
    manifest = tmp_path / "servers.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "name": "offline",
                    "port": unused_port,
                    "health_tool": "sample_health",
                }
            ]
        ),
        encoding="utf-8",
    )
    module = load_engine()
    module.DEFAULT_MANIFEST = manifest

    assert module.main([]) == 1
