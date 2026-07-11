import bugchecks


def test_known_bugcheck():
    name, desc = bugchecks.describe_bugcheck(0x133)
    assert name == "DPC_WATCHDOG_VIOLATION"
    assert desc and isinstance(desc, str)


def test_known_bugcheck_whea():
    name, _ = bugchecks.describe_bugcheck(0x124)
    assert name == "WHEA_UNCORRECTABLE_ERROR"


def test_unknown_bugcheck():
    assert bugchecks.describe_bugcheck(0xDEAD1234) == (None, None)
    assert bugchecks.describe_bugcheck(None) == (None, None)


def test_known_exception():
    name, desc = bugchecks.describe_exception("c0000005")
    assert name == "ACCESS_VIOLATION"
    # case / 0x-prefix tolerant
    assert bugchecks.describe_exception("0xC0000005")[0] == "ACCESS_VIOLATION"


def test_stack_buffer_overrun():
    assert bugchecks.describe_exception("c0000409")[0] == "STACK_BUFFER_OVERRUN"


def test_unknown_exception():
    assert bugchecks.describe_exception("deadbeef") == (None, None)
    assert bugchecks.describe_exception("") == (None, None)
    assert bugchecks.describe_exception(None) == (None, None)
