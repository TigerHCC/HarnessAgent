"""Config for the windows_audio MCP: config.json defaults + WINAUDIO_MCP_<KEY> env overrides."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))

def env_key(name): return "WINAUDIO_MCP_" + name.upper()

def load(path=None):
    path = path or os.environ.get("WINAUDIO_MCP_CONFIG") or os.path.join(HERE, "config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k, d in (("trace_seconds_default", 0), ("subprocess_timeout", 30), ("trace_max_seconds", 30)):
        cfg[k] = int(os.environ.get(env_key(k), cfg.get(k, d)))
    return cfg
