"""Launch shim for Microsoft's official markitdown-mcp server (manifest-external, 127.0.0.1:8794).

Exists only because the shared hidden launcher (scripts/start_mcp_hidden.ps1) takes a .py ServerPath;
it sets argv for streamable-http on loopback:8794 and hands off to the official entry point. All
behavior (the convert_to_markdown tool) is the official package's, unmodified.
"""
import sys

ARGS = ["markitdown-mcp", "--http", "--host", "127.0.0.1", "--port", "8794"]


def _resolve_main():
    try:
        from markitdown_mcp import main as md_main            # console-script target
    except ImportError:
        from markitdown_mcp.__main__ import main as md_main   # fallback layout
    return md_main


def main():
    sys.argv = list(ARGS)
    _resolve_main()()


if __name__ == "__main__":
    main()
