# mcp/dtm_sdk/tests/test_howto.py
import howto

DOC = """# Title

## DTMUtil -- DTM Client SDK Utility

Intro to dtmutil.

#### `workflow status`

Query workflow status.

#### `workflow start`

Start it.

## DtpInstrumentationUtil -- Instrumentation SDK Utility

Intro to instrumentation.
"""


def test_util_section():
    s = howto.util_section(DOC, "dtmutil")
    assert "Intro to dtmutil" in s
    assert "DtpInstrumentationUtil" not in s


def test_command_help():
    s = howto.command_help(DOC, "dtmutil", "workflow status")
    assert "Query workflow status" in s
    assert "Start it" not in s


def test_command_help_falls_back_to_section():
    s = howto.command_help(DOC, "dtmutil", "no-such-command")
    assert "Intro to dtmutil" in s
