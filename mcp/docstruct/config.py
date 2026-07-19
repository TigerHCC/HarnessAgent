"""Config for the docstruct MCP: config.json defaults with DOCSTRUCT_MCP_<KEY> env overrides.
llm_base_url should track goose's live OPENAI_HOST (see README)."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def env_key(name):
    return "DOCSTRUCT_MCP_" + name.upper()


def load(path=None):
    path = path or os.environ.get("DOCSTRUCT_MCP_CONFIG") or os.path.join(HERE, "config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k in ("llm_base_url", "llm_model"):
        cfg[k] = os.environ.get(env_key(k), cfg.get(k, ""))
    for k, default in (("max_tokens", 6000), ("ocr_dpi", 150), ("llm_timeout_seconds", 300)):
        cfg[k] = int(os.environ.get(env_key(k), cfg.get(k, default)))
    return cfg
