import sys
import run_markitdown_mcp as shim


def test_args_pin_http_loopback_8794():
    assert shim.ARGS[0] == "markitdown-mcp"
    assert "--http" in shim.ARGS
    assert shim.ARGS[shim.ARGS.index("--host") + 1] == "127.0.0.1"
    assert shim.ARGS[shim.ARGS.index("--port") + 1] == "8794"


def test_resolve_main_returns_callable():
    assert callable(shim._resolve_main())          # requires markitdown-mcp installed


def test_main_sets_argv_then_calls_entry(monkeypatch):
    seen = {}
    def fake_main():
        seen["argv"] = list(sys.argv)
    monkeypatch.setattr(shim, "_resolve_main", lambda: fake_main)
    shim.main()
    assert seen["argv"] == shim.ARGS               # argv set BEFORE the entry ran
