# Goose Web System Health Example Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `health` starter chip that fills the Goose Web composer with the approved multiline Windows system-health prompt.

**Architecture:** Extend the existing hero-chip tuple with an optional full-prompt value while retaining a label fallback for all existing examples. Keep the feature entirely in the static frontend and protect the prompt and click contract with a focused source regression test plus browser interaction checks.

**Tech Stack:** HTML, vanilla JavaScript, Python unittest/pytest, Goose Web local server, browser automation

## Global Constraints

- The chip tag is exactly `health`.
- The visible label is exactly `Perform a system health check on this Windows machine:`.
- Clicking fills the complete approved English prompt with all four numbered requirements and the final suspicious-items instruction.
- Clicking focuses and resizes the composer but does not submit the message.
- The four existing starter prompts retain identical visible text and filled values.
- Do not add backend APIs, configuration, dependencies, or persistence.

---

### Task 1: Health Starter Prompt and Responsive Verification

**Files:**
- Modify: `goose_web/index.html:400-410`
- Create: `goose_web/tests/test_examples.py`

**Interfaces:**
- Consumes: the existing `showHero()` chip renderer and `input`, `autoGrow()` browser globals.
- Produces: a fifth `[tag, label, prompt]` chip whose click handler assigns the prompt to `input.value` without calling `send()`.

- [ ] **Step 1: Write the failing source regression test**

Create `goose_web/tests/test_examples.py`:

```python
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
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m pytest goose_web/tests/test_examples.py -q`

Expected: FAIL because the `health` entry and three-value handler do not exist.

- [ ] **Step 3: Implement the minimal frontend change**

Add this fifth entry to `chips`:

```javascript
["health","Perform a system health check on this Windows machine:",`Perform a system health check on this Windows machine:
1.Check for any handle/memory leaks (identify the specific process/driver).
2.Identify which third-party AV/VPN drivers are hooked into the file and network paths.
3.Check if there have been any recent update failures or system crashes.
4.Determine which directories are experiencing excessive write activity.
Use the diagnostic tools available to you to investigate, and finally list the suspicious items along with their corresponding processes/drivers.`],
```

Change the renderer to destructure `([tag,label,prompt=label])`, display `label`, and assign `prompt` in the existing click handler. Keep focus and `autoGrow()` calls unchanged and do not call `send()`.

- [ ] **Step 4: Run focused and Goose Web tests**

Run:

```powershell
python -m pytest goose_web/tests/test_examples.py -q
python -m pytest goose_web/tests -q
```

Expected: all tests PASS.

- [ ] **Step 5: Verify the browser interaction and responsive layout**

Start Goose Web on an unused loopback port, open a new chat, and verify at desktop `1440x900` and mobile `390x844`:

- all five hero chips are visible and do not overlap;
- the fifth chip displays the short label;
- clicking it fills the textarea with the exact six-line prompt;
- no user message appears until Send is clicked.

Capture screenshots for both viewports and stop the test server afterward.

- [ ] **Step 6: Commit the implementation**

```powershell
git add goose_web/index.html goose_web/tests/test_examples.py docs/superpowers/specs/2026-07-17-goose-web-health-example-design.md docs/superpowers/plans/2026-07-17-goose-web-health-example.md
git commit -m "feat(goose_web): add system health example"
```
