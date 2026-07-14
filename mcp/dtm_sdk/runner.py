"""Subprocess execution for the dtmsdk utils. argv-list only (no shell); output parsed json -> yaml
-> text with the raw always preserved so a parse miss never looks like a command failure.
"""
import json
import os
import subprocess
import time

try:
    import yaml
except Exception:  # pyyaml is a declared dep; degrade to json+text if somehow absent
    yaml = None


def build_argv(exe, command, args, *, json_flag):
    prefix = list(exe) if isinstance(exe, (list, tuple)) else [exe]
    argv = prefix + command.split()
    if json_flag:
        argv.append("--json")
    return argv + list(args)


def parse_output(text):
    stripped = (text or "").strip()
    if not stripped:
        return None, "text"
    try:
        return json.loads(stripped), "json"
    except ValueError:
        pass
    if yaml is not None:
        try:
            v = yaml.safe_load(stripped)
            if isinstance(v, (dict, list)):
                return v, "yaml"
        except Exception:
            pass
    return text, "text"


def _kill_tree(proc):
    # Kill the WHOLE process tree so no surviving grandchild keeps the stdout pipe open. This is the
    # key defense: subprocess.run's own timeout path, after killing only the direct child, drains the
    # pipe with an UNTIMED communicate() -- which blocks forever if a grandchild still holds the pipe
    # write-end (the observed dtmsdk wedge). taskkill /T kills descendants and guarantees pipe EOF.
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=10)
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass


def run(exe, command, args, *, timeout, json_flag, env_json):
    argv = build_argv(exe, command, args, json_flag=json_flag)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_json:
        env["DTPUTIL_JSON_OUTPUT"] = "true"
    start = time.monotonic()
    timed_out = False
    # Popen + communicate(timeout=) rather than subprocess.run(timeout=), so on timeout we control the
    # cleanup: kill the whole tree, then drain with a BOUNDED communicate so this never blocks forever.
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", env=env)
    try:
        out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        code = -1
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)   # bounded: never an untimed drain
        except subprocess.TimeoutExpired:
            out, err = "", ""
    out = out or ""
    err = err or ""
    dur = round(time.monotonic() - start, 3)
    parsed, fmt = parse_output(out)
    return {
        "ok": (code == 0 and not timed_out),
        "exit_code": code,
        "command_line": " ".join(argv),
        "parsed": parsed,
        "stdout_raw": out,
        "stderr": err,
        "duration_seconds": dur,
        "format": fmt,
        "timed_out": timed_out,
    }
