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


def run(exe, command, args, *, timeout, json_flag, env_json):
    argv = build_argv(exe, command, args, json_flag=json_flag)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_json:
        env["DTPUTIL_JSON_OUTPUT"] = "true"
    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, env=env)
        out, err, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        err = e.stderr or ""
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        code = -1
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
