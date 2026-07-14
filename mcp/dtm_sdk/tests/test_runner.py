import os
import sys
import time
import runner

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fake_util.py")
HANG = os.path.join(HERE, "hang_util.py")


def _run(command, args, **kw):
    # invoke the fake util through the python interpreter; the [python, script] list is the exe
    # prefix, so the script lands before the command words (Python needs argv[1] to be the script).
    kw.setdefault("timeout", 10)
    kw.setdefault("json_flag", False)
    kw.setdefault("env_json", False)
    return runner.run([sys.executable, FAKE], command, args, **kw)


def test_build_argv_adds_json_flag():
    argv = runner.build_argv("X.exe", "workflow status", ["--id", "7"], json_flag=True)
    assert argv == ["X.exe", "workflow", "status", "--json", "--id", "7"]


def test_build_argv_accepts_list_prefix():
    argv = runner.build_argv(["py", "u.py"], "metadata", ["--id", "7"], json_flag=False)
    assert argv == ["py", "u.py", "metadata", "--id", "7"]


def test_run_parses_json():
    r = _run("metadata", ["--emit", "json"])
    assert r["ok"] and r["exit_code"] == 0
    assert r["format"] == "json"
    assert "metadata" in r["parsed"]["argv"]


def test_run_parses_yaml_fallback():
    r = _run("metadata", ["--emit", "yaml"])
    assert r["format"] == "yaml"
    assert "argv" in r["parsed"]


def test_run_text_fallback_never_fails_the_command():
    r = _run("metadata", ["--emit", "text"])
    assert r["ok"] is True
    assert r["format"] == "text"
    assert "plain text output" in r["stdout_raw"]


def test_run_nonzero_exit():
    r = _run("metadata", ["--emit", "json", "--exit", "3"])
    assert r["ok"] is False
    assert r["exit_code"] == 3


def test_env_json_sets_dtputil_var():
    r = _run("metadata", ["--emit", "json"], env_json=True)
    assert r["parsed"]["json_env"] == "true"


def test_timeout_returns_partial_not_exception():
    r = _run("metadata", ["--emit", "json", "--sleep", "3"], timeout=1)
    assert r["timed_out"] is True
    assert r["ok"] is False


def test_timeout_with_pipe_holding_grandchild_does_not_block_forever():
    # Regression for the dtmsdk wedge: the util spawns a grandchild that inherits stdout and lives on.
    # Without the kill-tree + bounded-drain fix, runner.run's post-timeout untimed communicate() would
    # block FOREVER on the never-closing pipe (freezing the whole event loop). With the fix it must
    # return promptly with timed_out=True. Assert a hard wall-clock bound so a regression HANGS the test.
    start = time.monotonic()
    r = runner.run([sys.executable, HANG], "", [], timeout=2, json_flag=False, env_json=False)
    elapsed = time.monotonic() - start
    assert r["timed_out"] is True
    assert elapsed < 25, "runner blocked %.1fs on a pipe-holding grandchild (kill-tree/drain regressed)" % elapsed
