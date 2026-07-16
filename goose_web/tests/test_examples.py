import re
import unittest
from pathlib import Path


INDEX = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")


class HeroExamples(unittest.TestCase):
    def test_system_health_prompt_is_complete_and_click_to_fill_only(self):
        required = [
            '["health","Perform a system health check on this Windows machine:"',
            "1.Check for any handle/memory leaks (identify the specific process/driver).",
            "2.Identify which third-party AV/VPN drivers are hooked into the file and network paths.",
            "3.Check if there have been any recent update failures or system crashes.",
            "4.Determine which directories are experiencing excessive write activity.",
            "finally list the suspicious items along with their corresponding processes/drivers.",
        ]
        for text in required:
            self.assertIn(text, INDEX)

        handler = re.search(r"chips\.forEach\(.*?\n\s*}\);", INDEX, re.S)
        self.assertIsNotNone(handler)
        self.assertIn("input.value=prompt", handler.group(0))
        self.assertIn("input.focus()", handler.group(0))
        self.assertIn("autoGrow()", handler.group(0))
        self.assertNotIn("send()", handler.group(0))

    def test_existing_examples_default_prompt_to_visible_label(self):
        self.assertIn("([tag,label,prompt=label])", INDEX)


if __name__ == "__main__":
    unittest.main()
