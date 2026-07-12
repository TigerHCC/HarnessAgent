# mcp/dtm_sdk/tests/test_live.py
"""Phase-1 live tests: prove the plumbing against the REAL instrumentation + analytics utils.

Gated hard -- these execute real utils, so they run ONLY when elevated AND Dell TechHub is running
AND DTM_SDK_LIVE_TESTS=1. They exercise safe (read-only) commands and, for the confirm path, LOCAL
actions only. They never transmit, unregister, or change DTP config. dtmutil/transmission/platinum
live tests are deferred to phase 2 (see TODO_PHASE2.md), and upload APIs are excluded there.
"""
import os
import unittest

import dtm_sdk_mcp_server as srv

_LIVE = os.environ.get("DTM_SDK_LIVE_TESTS") == "1"


def _prereqs():
    if not _LIVE:
        return "DTM_SDK_LIVE_TESTS != 1"
    if not srv.is_admin():
        return "not elevated"
    if srv.dellhub_state() != "running":
        return "Dell TechHub not running (%s)" % srv.dellhub_state()
    if not srv._exe_for("instrumentation") or not srv._exe_for("analytics"):
        return "instrumentation/analytics exe not found"
    return None


@unittest.skipUnless(_prereqs() is None, _prereqs() or "prereqs unmet")
class Live(unittest.TestCase):
    def _assert_really_ran(self, r):
        # A util that rejects an arg prints its usage/help and exits 0 -- so exit_code alone is not
        # proof of real execution. Assert the util did NOT emit an arg-parse complaint. (This is the
        # check that would have caught the --json bug found in phase-1 live testing.)
        self.assertEqual(r["timed_out"], False)
        self.assertNotIn("Unrecognized command or argument", r.get("stderr", ""))
        self.assertNotIn("Unrecognized command or argument", r.get("stdout_raw", ""))

    def test_instrumentation_metadata_runs(self):
        r = srv._dispatch("instrumentation", "metadata", [], "")
        self._assert_really_ran(r)
        self.assertEqual(r["exit_code"], 0)
        # metadata returns the datatype catalogue; prove real content came back, not a help dump
        self.assertIn("Metadata", r["stdout_raw"])

    def test_analytics_metadata_runs(self):
        r = srv._dispatch("analytics", "metadata", [], "")
        self._assert_really_ran(r)
        self.assertEqual(r["exit_code"], 0)

    def test_instrumentation_collect_via_confirmation(self):
        # collect is a LOCAL action (no egress). Prove the confirm flow end-to-end on a real util.
        prev = srv._dispatch("instrumentation", "collect",
                             ["--datatype-name", "BatteryStaticData"], "")
        self.assertTrue(prev["requires_confirmation"])
        r = srv._dispatch("instrumentation", "collect",
                          ["--datatype-name", "BatteryStaticData"], prev["confirm_token"])
        self._assert_really_ran(r)


if __name__ == "__main__":
    unittest.main()
