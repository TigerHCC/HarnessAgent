# mcp/dtm_sdk/tests/test_server.py
import asyncio
import inspect
import time
import dtm_sdk_mcp_server as srv


def _fake_run(*a, **k):
    return {"ok": True, "exit_code": 0, "command_line": "x", "parsed": {"ran": True},
            "stdout_raw": "", "stderr": "", "duration_seconds": 0.1, "format": "json", "timed_out": False}


def setup_function(_):
    srv._TOKENS.clear()


def _patch(monkeypatch, exe="FAKE.exe"):
    # _dispatch checks is_admin() before running; the test host may not be elevated, so force it.
    monkeypatch.setattr(srv, "is_admin", lambda: True)
    monkeypatch.setattr(srv, "_exe_for", lambda util: exe)
    monkeypatch.setattr(srv.runner, "run", _fake_run)


def test_default_client_id_injected(monkeypatch):
    monkeypatch.setattr(srv, "cfg", lambda: {
        "default_client_id": "675f1370-b7ce-4113-8d6e-a128ee3bb74b", "default_client_name": None})
    out = srv._with_client_id(["--datatype-name", "X"])
    assert out == ["--id", "675f1370-b7ce-4113-8d6e-a128ee3bb74b", "--datatype-name", "X"]


def test_caller_id_not_overridden(monkeypatch):
    monkeypatch.setattr(srv, "cfg", lambda: {
        "default_client_id": "675f1370-b7ce-4113-8d6e-a128ee3bb74b", "default_client_name": None})
    out = srv._with_client_id(["--id", "custom-id", "--foo"])
    assert out == ["--id", "custom-id", "--foo"]   # caller's id wins; default not added
    assert out.count("--id") == 1


def test_default_client_name_appended_when_set(monkeypatch):
    monkeypatch.setattr(srv, "cfg", lambda: {"default_client_id": "abc", "default_client_name": "MyApp"})
    assert srv._with_client_id([]) == ["--id", "abc", "--appName", "MyApp"]


def test_no_default_when_unset(monkeypatch):
    monkeypatch.setattr(srv, "cfg", lambda: {"default_client_id": None})
    assert srv._with_client_id(["--foo"]) == ["--foo"]


def test_preview_shows_default_client_id(monkeypatch):
    # end-to-end: the shipped config.json default id surfaces in the confirmation preview
    _patch(monkeypatch)
    r = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], "")
    assert "--id 675f1370-b7ce-4113-8d6e-a128ee3bb74b" in r["command_line"]


def test_no_json_cli_flag_in_argv(monkeypatch):
    # The real DtpUtilHelper utils REJECT `--json` as a per-subcommand arg (parse error -> the util
    # prints help and does nothing). JSON output is requested via the DTPUTIL_JSON_OUTPUT env var only,
    # so the built argv must never contain --json. (Regression: found in phase-1 live testing.)
    _patch(monkeypatch)
    for util, cmd in [("instrumentation", "metadata"), ("transmission", "collect-transmit"),
                      ("analytics", "custom-analysis"), ("dtmutil", "workflow status")]:
        r = srv._dispatch(util, cmd, ["--datatype-name", "X"], "")
        cl = r.get("command_line", "")
        assert "--json" not in cl, "%s %s: argv must not contain --json (got %r)" % (util, cmd, cl)


def test_safe_command_runs_without_token(monkeypatch):
    _patch(monkeypatch)
    r = srv._dispatch("instrumentation", "metadata", [], "")
    assert r["ok"] is True
    assert r["parsed"]["ran"] is True


def test_dangerous_command_requires_confirmation(monkeypatch):
    _patch(monkeypatch)
    r = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], "")
    assert r["requires_confirmation"] is True
    assert r["category"] == "egress"
    assert r["confirm_token"]
    assert "collect-transmit" in r["command_line"]


def test_confirmation_token_executes(monkeypatch):
    _patch(monkeypatch)
    prev = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], "")
    tok = prev["confirm_token"]
    r = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], tok)
    assert r["ok"] is True


def test_token_for_other_command_rejected(monkeypatch):
    _patch(monkeypatch)
    prev = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], "")
    tok = prev["confirm_token"]
    # reuse the token for a DIFFERENT command -> must not execute
    r = srv._dispatch("transmission", "cancel", [], tok)
    assert r.get("requires_confirmation") is True


def test_token_single_use(monkeypatch):
    _patch(monkeypatch)
    prev = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], "")
    tok = prev["confirm_token"]
    assert srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], tok)["ok"]
    second = srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], tok)
    assert second.get("requires_confirmation") is True  # consumed


def test_bad_command_string_rejected(monkeypatch):
    _patch(monkeypatch)
    r = srv._dispatch("transmission", "cancel; whoami", [], "")
    assert "error" in r


def test_missing_exe_names_the_key(monkeypatch):
    _patch(monkeypatch, exe=None)
    r = srv._dispatch("instrumentation", "metadata", [], "")
    assert "error" in r and "instrumentation" in r["error"]


def test_health_shape(monkeypatch):
    h = srv.dtm_health()
    for k in ("is_admin", "dell_techhub", "executables", "datatype_tables", "howto"):
        assert k in h


def test_run_tools_are_async(monkeypatch):
    # The 5 subprocess-spawning tools MUST be async (offloaded off the event loop) so a blocking util
    # can never freeze the whole server. dtm_health + the lookup tools stay sync (fast, no util).
    for name in ("dtm_run_dtmutil", "dtm_run_instrumentation", "dtm_run_analytics",
                 "dtm_run_transmission", "dtm_run_platinum"):
        assert inspect.iscoroutinefunction(getattr(srv, name)), name + " must be async"
    assert not inspect.iscoroutinefunction(srv.dtm_health)


def test_async_run_tool_offloads_and_returns(monkeypatch):
    _patch(monkeypatch)
    r = asyncio.run(srv.dtm_run_instrumentation("metadata", [], ""))
    assert r["ok"] is True and r["parsed"]["ran"] is True


def test_expired_tokens_are_pruned(monkeypatch):
    _patch(monkeypatch)
    # seed an ancient token, then issue a fresh preview -> the stale one must be pruned
    srv._TOKENS["stale"] = ("transmission", "collect-transmit", ["x"], time.time() - 9999)
    srv._dispatch("transmission", "collect-transmit", ["--datatype-name", "X"], "")
    assert "stale" not in srv._TOKENS
