# mcp/dtm_sdk/tests/test_policy.py
import json
import policy


def test_all_65_commands_present():
    total = sum(len(v) for v in policy.UTILS.values())
    assert total == 65
    assert set(policy.UTILS) == {"dtmutil", "instrumentation", "analytics", "transmission", "platinum"}


def test_safe_count_is_24_and_all_real():
    assert sum(len(v) for v in policy.SAFE.values()) == 24
    for util, cmds in policy.SAFE.items():
        for c in cmds:
            assert c in policy.UTILS[util], f"{util}:{c} not a real command"


def test_classify_safe_vs_gated():
    assert policy.classify("instrumentation", "metadata") == "safe"
    assert policy.classify("transmission", "collect-transmit") == "egress"
    assert policy.classify("instrumentation", "enable-datatype") == "state"
    assert policy.classify("instrumentation", "collect") == "action"


def test_retrieve_file_is_not_safe():
    # elevated write to a caller-chosen path -> dangerous despite being a "retrieve"
    assert not policy.is_safe("instrumentation", "retrieve-file")


def test_bundle_date_range_is_safe_despite_its_name():
    # calls RetrieveBundleTransmissionStatusItemsAsync -- a query
    assert policy.is_safe("dtmutil", "bundle-transmission-date-range")


def test_unknown_command_is_gated_not_safe():
    assert policy.classify("instrumentation", "brand-new-command") == "unknown"
    assert not policy.is_safe("instrumentation", "brand-new-command")


def test_token_roundtrip():
    t = policy.make_token("transmission", "collect-transmit", ["--datatype-name", "X"])
    assert policy.verify_token("transmission", "collect-transmit", ["--datatype-name", "X"],
                               t, now=100.0, issued_at=100.0)


def test_token_bound_to_args():
    t = policy.make_token("transmission", "collect-transmit", ["--datatype-name", "X"])
    # same token, different args -> rejected
    assert not policy.verify_token("transmission", "collect-transmit", ["--datatype-name", "Y"],
                                   t, now=100.0, issued_at=100.0)


def test_token_bound_to_command():
    t = policy.make_token("transmission", "collect-transmit", [])
    assert not policy.verify_token("transmission", "cancel", [], t, now=100.0, issued_at=100.0)


def test_token_expires():
    t = policy.make_token("transmission", "cancel", [])
    assert not policy.verify_token("transmission", "cancel", [], t, now=221.0, issued_at=100.0)
    assert policy.verify_token("transmission", "cancel", [], t, now=220.0, issued_at=100.0)


def test_validate_command():
    assert policy.validate_command("workflow retrieve collection")
    assert policy.validate_command("collect-transmit")
    assert not policy.validate_command("collect; rm -rf /")
    assert not policy.validate_command("--flag")
    assert not policy.validate_command("")
