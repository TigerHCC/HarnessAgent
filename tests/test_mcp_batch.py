import importlib.util
import json
import argparse
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
        self.redirect_requests = []
        self.target_requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1" if owner.mode == "persistent_sse" else "HTTP/1.0"

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(length)
                exchange = {
                    "method": self.command,
                    "body": raw_body,
                    "content_type": self.headers.get("Content-Type"),
                    "accept": self.headers.get("Accept"),
                    "session": self.headers.get("Mcp-Session-Id"),
                }
                if owner.mode == "redirect" and self.path == "/mcp":
                    owner.redirect_requests.append(exchange)
                    self.send_response(307)
                    self.send_header("Location", "/mcp/")
                    self.end_headers()
                    return
                if owner.mode == "redirect" and self.path == "/mcp/":
                    owner.target_requests.append(exchange)
                if owner.mode == "remote_redirect" and self.path == "/mcp":
                    owner.redirect_requests.append(exchange)
                    self.send_response(307)
                    self.send_header("Location", "http://example.com/mcp")
                    self.end_headers()
                    return
                if owner.mode == "redirect_loop":
                    owner.redirect_requests.append(exchange)
                    location = "/mcp/" if self.path == "/mcp" else "/mcp"
                    self.send_response(308)
                    self.send_header("Location", location)
                    self.end_headers()
                    return
                if owner.mode == "redirect_chain":
                    owner.redirect_requests.append(exchange)
                    hop = 0 if self.path == "/mcp" else int(self.path.rsplit("/", 1)[1])
                    self.send_response(307)
                    self.send_header("Location", f"/hop/{hop + 1}")
                    self.end_headers()
                    return

                request = json.loads(raw_body)
                owner.methods.append(request["method"])
                owner.session_headers.append(self.headers.get("Mcp-Session-Id"))

                method = request["method"]
                if method == "notifications/initialized":
                    self.send_response(202)
                    self.end_headers()
                    return

                if method == "initialize" and owner.mode == "http_error":
                    self.send_error(500, "initialize failed")
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

                if owner.mode == "wrong_initialize_id" and method == "initialize":
                    response["id"] = 999
                elif owner.mode == "wrong_tools_id" and method == "tools/list":
                    response["id"] = 999
                elif owner.mode == "wrong_health_id" and method == "tools/call":
                    response["id"] = 999
                elif owner.mode == "malformed_content" and method == "tools/call":
                    response["result"] = {"content": [{}]}
                elif owner.mode == "empty_content" and method == "tools/call":
                    response["result"] = {"content": []}
                elif owner.mode == "malformed_text" and method == "tools/call":
                    response["result"] = {"content": [{"type": "text"}]}
                elif owner.mode == "malformed_image" and method == "tools/call":
                    response["result"] = {
                        "content": [{"type": "image", "data": "aGVsbG8="}]
                    }
                elif owner.mode == "malformed_audio" and method == "tools/call":
                    response["result"] = {
                        "content": [{"type": "audio", "mimeType": "audio/wav"}]
                    }
                elif owner.mode == "malformed_resource" and method == "tools/call":
                    response["result"] = {
                        "content": [{"type": "resource", "resource": {}}]
                    }
                elif owner.mode == "resource_missing_uri" and method == "tools/call":
                    response["result"] = {
                        "content": [
                            {"type": "resource", "resource": {"text": "healthy"}}
                        ]
                    }
                elif owner.mode == "resource_missing_body" and method == "tools/call":
                    response["result"] = {
                        "content": [
                            {
                                "type": "resource",
                                "resource": {"uri": "file:///health.txt"},
                            }
                        ]
                    }
                elif owner.mode == "valid_resource" and method == "tools/call":
                    response["result"] = {
                        "content": [
                            {
                                "type": "resource",
                                "resource": {
                                    "uri": "file:///health.txt",
                                    "text": '{"ok": true}',
                                },
                            }
                        ]
                    }

                body = json.dumps(response).encode()
                if owner.mode == "bad_json" and method == "initialize":
                    body = b"{not-json"
                elif owner.mode == "bad_sse" and method == "initialize":
                    body = b"event: message\ndata: {not-json\n\n"
                elif owner.mode == "complex_sse":
                    notification = json.dumps(
                        {"jsonrpc": "2.0", "method": "notifications/progress"}
                    ).encode()
                    split_at = body.find(b",") + 1
                    body = (
                        b": keepalive\n\n"
                        + b"data: "
                        + notification
                        + b"\n\n"
                        + b"event: message\n"
                        + b"data: "
                        + body[:split_at]
                        + b"\n"
                        + b"data: "
                        + body[split_at:].lstrip()
                        + b"\n\n"
                    )
                elif owner.content_type == "text/event-stream":
                    body = b"event: message\n" + b"data: " + body + b"\n\n"
                self.send_response(200)
                response_type = (
                    "text/event-stream"
                    if owner.mode in {"bad_sse", "complex_sse"}
                    or (owner.mode == "persistent_sse" and method == "initialize")
                    else owner.content_type
                )
                self.send_header("Content-Type", response_type)
                if method == "initialize":
                    self.send_header("Mcp-Session-Id", "test-session")
                if not (owner.mode == "persistent_sse" and method == "initialize"):
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if owner.mode == "persistent_sse" and method == "initialize":
                    self.wfile.write(b"event: message\ndata: " + body + b"\n\n")
                    self.wfile.flush()
                    time.sleep(0.75)
                elif owner.mode == "slow_initialize" and method == "initialize":
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.03)
                else:
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
    assert len(result["tools"]) == 1
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
    assert result["stage"] == "connect"
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
    assert result["stage"] == stage
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


def canonical_manifest_entries():
    return [
        {
            "name": f"server-{index}",
            "directory": f"server-{index}",
            "port": port,
            "task": f"MCP Server {index}",
            "run_level": "Highest",
            "description": f"Server {index}",
            "health_tool": f"server_{index}_health",
        }
        for index, port in enumerate(range(8777, 8791), 1)
    ]


def test_load_manifest_reads_and_validates_canonical_entries(tmp_path):
    manifest = tmp_path / "servers.json"
    entries = canonical_manifest_entries()
    manifest.write_text(json.dumps(entries), encoding="utf-8")

    assert load_engine().load_manifest(manifest) == entries


@pytest.mark.parametrize(
    "mutate,error_text",
    [
        (lambda entries: entries.clear(), "exactly 14"),
        (lambda entries: entries.pop(), "exactly 14"),
        (lambda entries: entries[1].update(name=entries[0]["name"]), "duplicate name"),
        (lambda entries: entries[1].update(port=entries[0]["port"]), "duplicate port"),
        (lambda entries: entries[1].update(task=entries[0]["task"]), "duplicate task"),
        (lambda entries: entries[0].update(port=9000), "canonical ports"),
        (lambda entries: entries[0].pop("directory"), "directory"),
        (lambda entries: entries[0].update(description=""), "description"),
        (lambda entries: entries[0].update(run_level="Admin"), "run_level"),
        (lambda entries: entries[0].update(health_tool=" "), "health_tool"),
    ],
    ids=[
        "empty", "wrong-count", "duplicate-name", "duplicate-port", "duplicate-task",
        "noncanonical-port", "missing-field", "empty-field", "run-level", "health-tool",
    ],
)
def test_load_manifest_rejects_invalid_inventory(tmp_path, mutate, error_text):
    entries = canonical_manifest_entries()
    mutate(entries)
    manifest = tmp_path / "servers.json"
    manifest.write_text(json.dumps(entries), encoding="utf-8")

    with pytest.raises(ValueError, match=error_text):
        load_engine().load_manifest(manifest)


def test_main_returns_success_and_writes_reports(fake_mcp_factory, tmp_path, capsys):
    server = fake_mcp_factory()
    manifest = tmp_path / "servers.json"
    output_dir = tmp_path / "reports"
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
    module.load_manifest = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))

    exit_code = module.main(
        [
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output_dir),
            "--timeout",
            "2",
        ]
    )

    assert exit_code == 0
    assert len(list(output_dir.glob("mcp-test-*.json"))) == 1
    assert len(list(output_dir.glob("mcp-test-*.md"))) == 1
    stdout = capsys.readouterr().out
    assert "[PASS] sample" in stdout
    assert "1 passed, 0 failed, 1 total" in stdout
    assert str(output_dir) in stdout


def test_main_returns_failure_and_writes_partial_failure_reports(
    fake_mcp_factory, tmp_path, capsys
):
    healthy = fake_mcp_factory()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused_port = sock.getsockname()[1]
    manifest = tmp_path / "servers.json"
    output_dir = tmp_path / "reports"
    manifest.write_text(
        json.dumps(
            [
                {
                    "name": "offline",
                    "port": unused_port,
                    "health_tool": "sample_health",
                },
                {
                    "name": "healthy",
                    "port": healthy.port,
                    "health_tool": "sample_health",
                },
            ]
        ),
        encoding="utf-8",
    )
    module = load_engine()
    module.load_manifest = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))

    exit_code = module.main(
        [
            "--manifest",
            str(manifest),
            "--output-dir",
            str(output_dir),
            "--timeout",
            "0.2",
        ]
    )

    assert exit_code == 1
    report = json.loads(next(output_dir.glob("mcp-test-*.json")).read_text())
    assert report["summary"]["total"] == 2
    assert report["summary"]["passed"] == 1
    assert report["summary"]["failed"] == 1
    assert len(list(output_dir.glob("mcp-test-*.md"))) == 1
    stdout = capsys.readouterr().out
    assert "[FAIL] offline" in stdout
    assert "[PASS] healthy" in stdout
    assert "1 passed, 1 failed, 2 total" in stdout


@pytest.mark.parametrize(
    "contents",
    [
        "{not-json",
        '{"name": "not-a-list"}',
        "[]",
        json.dumps(canonical_manifest_entries()[:-1]),
    ],
)
def test_main_returns_usage_error_for_malformed_manifest_without_network(
    tmp_path, monkeypatch, capsys, contents
):
    manifest = tmp_path / "servers.json"
    manifest.write_text(contents, encoding="utf-8")
    module = load_engine()
    called = False

    def unexpected_run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("run_all must not be called")

    monkeypatch.setattr(module, "run_all", unexpected_run)

    exit_code = module.main(
        ["--manifest", str(manifest), "--output-dir", str(tmp_path / "reports")]
    )

    assert exit_code == 2
    assert called is False
    assert "manifest" in capsys.readouterr().err.lower()
    assert not (tmp_path / "reports").exists()


def test_main_rejects_remote_host_without_network(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "servers.json"
    manifest.write_text(
        json.dumps(
            [{"name": "sample", "port": 1234, "health_tool": "sample_health"}]
        ),
        encoding="utf-8",
    )
    module = load_engine()
    called = False

    def capture_run(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(module, "run_all", capture_run)

    exit_code = module.main(
        [
            "--manifest",
            str(manifest),
            "--output-dir",
            str(tmp_path / "reports"),
            "--host",
            "example.com",
        ]
    )

    assert exit_code == 2
    assert called is False
    assert "host" in capsys.readouterr().err.lower()
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1"])
def test_timeout_must_be_finite_and_positive(value):
    module = load_engine()

    with pytest.raises(argparse.ArgumentTypeError):
        module._positive_seconds(value)


@pytest.mark.parametrize("failing_function", ["build_report", "write_reports"])
def test_main_maps_all_report_failures_to_exit_two(
    tmp_path, monkeypatch, capsys, failing_function
):
    manifest = tmp_path / "servers.json"
    manifest.write_text(
        json.dumps(
            [{"name": "sample", "port": 1234, "health_tool": "sample_health"}]
        ),
        encoding="utf-8",
    )
    module = load_engine()
    monkeypatch.setattr(module, "run_all", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        module,
        "load_manifest",
        lambda path: json.loads(Path(path).read_text(encoding="utf-8")),
    )

    def fail(*args, **kwargs):
        raise ValueError(f"injected {failing_function} failure")

    monkeypatch.setattr(module, failing_function, fail)

    exit_code = module.main(
        ["--manifest", str(manifest), "--output-dir", str(tmp_path / "reports")]
    )

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "report error" in stderr.lower()
    assert f"injected {failing_function} failure" in stderr


@pytest.mark.parametrize("mode", ["bad_json", "bad_sse", "http_error"])
def test_initialize_response_failures_are_not_connection_failures(
    fake_mcp_factory, mode
):
    server = fake_mcp_factory(mode=mode)

    result = load_engine().test_server(
        {"name": "sample", "port": server.port, "health_tool": "sample_health"}, 1
    )

    assert result["status"] == "failed"
    assert result["stage"] == "initialize"
    assert result["error"]


@pytest.mark.parametrize(
    "mode,stage",
    [
        ("wrong_initialize_id", "initialize"),
        ("wrong_tools_id", "tools_list"),
        ("wrong_health_id", "health_call"),
    ],
)
def test_server_rejects_unmatched_jsonrpc_response_ids(
    fake_mcp_factory, mode, stage
):
    server = fake_mcp_factory(mode=mode)

    result = load_engine().test_server(
        {"name": "sample", "port": server.port, "health_tool": "sample_health"}, 1
    )

    assert result["status"] == "failed"
    assert result["stage"] == stage
    assert result["error"]


@pytest.mark.parametrize("mode", ["malformed_content", "empty_content"])
def test_server_rejects_malformed_health_content(fake_mcp_factory, mode):
    server = fake_mcp_factory(mode=mode)

    result = load_engine().test_server(
        {"name": "sample", "port": server.port, "health_tool": "sample_health"}, 1
    )

    assert result["status"] == "failed"
    assert result["stage"] == "health_call"
    assert result["error"]


@pytest.mark.parametrize(
    "mode",
    [
        "malformed_text",
        "malformed_image",
        "malformed_audio",
        "malformed_resource",
        "resource_missing_uri",
        "resource_missing_body",
    ],
)
def test_server_rejects_missing_fields_in_supported_health_content(
    fake_mcp_factory, mode
):
    server = fake_mcp_factory(mode=mode)

    result = load_engine().test_server(
        {"name": "sample", "port": server.port, "health_tool": "sample_health"}, 1
    )

    assert result["status"] == "failed"
    assert result["stage"] == "health_call"
    assert result["error"]


def test_server_accepts_valid_embedded_text_resource(fake_mcp_factory):
    server = fake_mcp_factory(mode="valid_resource")

    result = load_engine().test_server(
        {"name": "sample", "port": server.port, "health_tool": "sample_health"}, 1
    )

    assert result["status"] == "passed"
    resource = result["health"]["content"][0]["resource"]
    assert resource["uri"] == "file:///health.txt"
    assert resource["text"] == '{"ok": true}'


def test_server_preserves_post_exchange_across_fastmcp_redirect(fake_mcp_factory):
    server = fake_mcp_factory(mode="redirect")

    result = load_engine().test_server(
        {"name": "sample", "port": server.port, "health_tool": "sample_health"}, 2
    )

    assert result["status"] == "passed"
    assert len(server.redirect_requests) == len(server.target_requests) == 4
    for source, target in zip(server.redirect_requests, server.target_requests):
        assert target == source
        assert target["method"] == "POST"
        assert target["content_type"] == "application/json"
        assert target["accept"] == "application/json, text/event-stream"
    assert [request["session"] for request in server.target_requests[1:]] == [
        "test-session"
    ] * 3


@pytest.mark.parametrize(
    "mode,error_text",
    [
        ("remote_redirect", "loopback"),
        ("redirect_loop", "loop"),
        ("redirect_chain", "too many"),
    ],
)
def test_server_rejects_unsafe_or_unbounded_redirects(
    fake_mcp_factory, mode, error_text
):
    server = fake_mcp_factory(mode=mode)

    result = load_engine().test_server(
        {"name": "sample", "port": server.port, "health_tool": "sample_health"}, 2
    )

    assert result["status"] == "failed"
    assert result["stage"] == "initialize"
    assert error_text in result["error"].lower()


def test_server_selects_matching_response_from_complex_sse(fake_mcp_factory):
    server = fake_mcp_factory(mode="complex_sse")

    result = load_engine().test_server(
        {"name": "sample", "port": server.port, "health_tool": "sample_health"}, 1
    )

    assert result["status"] == "passed"
    assert result["health"]["content"][0]["text"] == '{"ok": true}'


def test_server_returns_complete_sse_event_before_persistent_stream_closes(
    fake_mcp_factory,
):
    server = fake_mcp_factory(mode="persistent_sse")

    started = time.monotonic()
    result = load_engine().test_server(
        {"name": "sample", "port": server.port, "health_tool": "sample_health"},
        0.3,
    )

    assert result["status"] == "passed"
    assert time.monotonic() - started < 0.7


def test_run_all_bounds_trickled_body_and_continues(fake_mcp_factory):
    slow = fake_mcp_factory(mode="slow_initialize")
    healthy = fake_mcp_factory()
    entries = [
        {"name": "slow", "port": slow.port, "health_tool": "sample_health"},
        {"name": "healthy", "port": healthy.port, "health_tool": "sample_health"},
    ]

    started = time.monotonic()
    results = load_engine().run_all(entries, timeout=0.15)
    elapsed = time.monotonic() - started

    assert elapsed < 0.8
    assert [result["status"] for result in results] == ["failed", "passed"]
    assert results[0]["stage"] == "initialize"


def test_run_all_isolates_invalid_entry_metadata(fake_mcp_factory):
    healthy = fake_mcp_factory()
    entries = [
        {"name": "invalid", "health_tool": "sample_health"},
        {"name": "healthy", "port": healthy.port, "health_tool": "sample_health"},
    ]

    results = load_engine().run_all(entries, timeout=1)

    assert [result["name"] for result in results] == ["invalid", "healthy"]
    assert [result["status"] for result in results] == ["failed", "passed"]
    assert results[0]["error"]


def sample_report(module):
    started_at = datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc)
    ended_at = datetime(2026, 7, 15, 1, 2, 5, 500000, tzinfo=timezone.utc)
    results = [
        {
            "name": "healthy",
            "port": 1234,
            "endpoint": "http://127.0.0.1:1234/mcp",
            "health_tool": "sample_health",
            "status": "passed",
            "stage": "health_call",
            "tools": ["sample_health", "sample_info"],
            "tool_count": 2,
            "health": {
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "raw_headers": {"Authorization": "secret"},
            },
            "error": None,
            "duration_seconds": 0.25,
            "raw_headers": {"Mcp-Session-Id": "secret"},
        },
        {
            "name": "offline",
            "port": 1235,
            "endpoint": "http://127.0.0.1:1235/mcp",
            "health_tool": "sample_health",
            "status": "failed",
            "stage": "connect",
            "error": "connection refused",
            "tools": [],
            "tool_count": 0,
            "health": None,
            "duration_seconds": 0.1,
        },
    ]
    return module.build_report(results, started_at, ended_at)


def test_build_report_summarizes_results_and_runtime_metadata():
    report = sample_report(load_engine())

    assert report["started_at"] == "2026-07-15T01:02:03Z"
    assert report["ended_at"] == "2026-07-15T01:02:05.500000Z"
    assert report["duration_seconds"] == 2.5
    assert report["schema_version"] == "1.0"
    assert report["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "duration_seconds": 2.5,
    }
    assert report["runtime"]["hostname"]
    assert report["runtime"]["platform"]
    assert report["runtime"]["python_version"]
    assert report["servers"][0]["tools"] == ["sample_health", "sample_info"]
    assert report["servers"][0]["health"]["content"][0]["type"] == "text"
    assert report["servers"][1]["stage"] == "connect"
    assert report["servers"][1]["error"] == "connection refused"
    assert "raw_headers" not in json.dumps(report)


def test_report_schema_normalizes_passed_and_failed_servers(fake_mcp_factory):
    healthy = fake_mcp_factory()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        unused_port = sock.getsockname()[1]
    entries = [
        {
            "name": "healthy",
            "port": healthy.port,
            "health_tool": "sample_health",
        },
        {
            "name": "offline",
            "port": unused_port,
            "health_tool": "sample_health",
        },
    ]
    module = load_engine()
    started_at = datetime(2026, 7, 15, 1, 2, 3, tzinfo=timezone.utc)
    ended_at = datetime(2026, 7, 15, 1, 2, 4, 500000, tzinfo=timezone.utc)

    report = module.build_report(module.run_all(entries, timeout=1), started_at, ended_at)

    assert report["schema_version"] == "1.0"
    assert report["summary"]["duration_seconds"] == 1.5
    expected_keys = {
        "name",
        "port",
        "endpoint",
        "health_tool",
        "status",
        "stage",
        "duration_seconds",
        "tools",
        "tool_count",
        "health",
        "error",
    }
    assert all(set(server) == expected_keys for server in report["servers"])
    passed, failed = report["servers"]
    assert passed["port"] == healthy.port
    assert passed["endpoint"] == f"http://127.0.0.1:{healthy.port}/mcp"
    assert passed["health_tool"] == "sample_health"
    assert passed["status"] == "passed"
    assert passed["stage"] == "health_call"
    assert passed["tools"] == ["sample_health"]
    assert passed["tool_count"] == 1
    assert passed["health"]["content"]
    assert passed["error"] is None
    assert failed["port"] == unused_port
    assert failed["endpoint"] == f"http://127.0.0.1:{unused_port}/mcp"
    assert failed["health_tool"] == "sample_health"
    assert failed["status"] == "failed"
    assert failed["stage"] == "connect"
    assert failed["tools"] == []
    assert failed["tool_count"] == 0
    assert failed["health"] is None
    assert failed["error"]


def test_write_reports_creates_paired_json_and_markdown(tmp_path):
    module = load_engine()
    report = sample_report(module)

    json_path, markdown_path = module.write_reports(report, tmp_path)

    assert json_path.parent == tmp_path
    assert markdown_path.parent == tmp_path
    assert json_path.stem == markdown_path.stem
    assert json_path.name.startswith("mcp-test-20260715T010205Z")
    assert json.loads(json_path.read_text(encoding="utf-8")) == report
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "| Server | Status | Stage | Tools | Duration |" in markdown
    assert "| healthy | PASS | health_call | 2 | 0.250s |" in markdown
    assert "## Failure Details" in markdown
    assert "offline" in markdown and "connection refused" in markdown
    assert "## Health: healthy" in markdown
    assert '```json\n{' in markdown
    assert "raw_headers" not in markdown


def test_write_reports_suffixes_filename_when_timestamp_collides(tmp_path):
    module = load_engine()
    report = sample_report(module)

    first_json, first_markdown = module.write_reports(report, tmp_path)
    second_json, second_markdown = module.write_reports(report, tmp_path)

    assert first_json.exists() and first_markdown.exists()
    assert second_json.name == "mcp-test-20260715T010205Z-1.json"
    assert second_markdown.name == "mcp-test-20260715T010205Z-1.md"
    assert second_json.exists() and second_markdown.exists()


def test_write_reports_reserves_colliding_names_across_concurrent_writers(
    tmp_path, monkeypatch
):
    module = load_engine()
    report = sample_report(module)
    barrier = threading.Barrier(2)
    original_write_temporary = module._write_temporary_sibling

    def synchronized_write(path, content):
        if path.suffix == ".json":
            barrier.wait(timeout=5)
        return original_write_temporary(path, content)

    monkeypatch.setattr(module, "_write_temporary_sibling", synchronized_write)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(module.write_reports, report, tmp_path) for _ in range(2)]
        pairs = [future.result(timeout=10) for future in futures]

    assert pairs[0][0] != pairs[1][0]
    assert pairs[0][1] != pairs[1][1]
    assert len(list(tmp_path.glob("mcp-test-*.json"))) == 2
    assert len(list(tmp_path.glob("mcp-test-*.md"))) == 2


def test_write_reports_does_not_expose_final_paths_while_reserved(
    tmp_path, monkeypatch
):
    module = load_engine()
    report = sample_report(module)
    writer_entered = threading.Event()
    release_writer = threading.Event()

    def pause_then_fail(path, content):
        writer_entered.set()
        if not release_writer.wait(timeout=5):
            raise TimeoutError("test did not release writer")
        raise OSError("injected write failure after reservation")

    monkeypatch.setattr(module, "_write_temporary_sibling", pause_then_fail)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(module.write_reports, report, tmp_path)
        assert writer_entered.wait(timeout=5)
        try:
            assert list(tmp_path.glob("mcp-test-*.json")) == []
            assert list(tmp_path.glob("mcp-test-*.md")) == []
        finally:
            release_writer.set()
        with pytest.raises(OSError, match="after reservation"):
            future.result(timeout=5)

    assert list(tmp_path.iterdir()) == []


def test_write_reports_cleans_pair_when_second_publish_fails(tmp_path, monkeypatch):
    module = load_engine()
    report = sample_report(module)
    original_replace = Path.replace

    def fail_markdown_publish(path, target):
        if Path(target).suffix == ".md":
            raise OSError("injected Markdown publish failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_markdown_publish)

    with pytest.raises(OSError, match="Markdown publish failure"):
        module.write_reports(report, tmp_path)

    assert list(tmp_path.iterdir()) == []
