# Goose Web System Health Example Design

## Goal

Add a system-health starter prompt to the Goose Web empty-chat screen so a user can fill the composer
with a complete Windows diagnostic request in one click.

## User Experience

The hero keeps its existing example-chip layout and gains a fifth chip labeled `health`. The visible
chip copy is the first line of the supplied prompt: `Perform a system health check on this Windows
machine:`. Clicking the chip fills the composer with the full English prompt, including the four
numbered investigation steps and final suspicious-item reporting instruction. It focuses and resizes
the composer but does not submit the message automatically.

The existing flex layout remains responsible for wrapping five chips. No new visual component, backend
endpoint, configuration field, or persistence behavior is introduced.

## Implementation

Extend entries in the local `chips` array in `showHero()` from `[tag, label]` to an optional
`[tag, label, prompt]` shape. The renderer defaults `prompt` to `label`, so the four existing examples
keep identical display and click behavior. Add the health entry with its short label and a JavaScript
template literal for the full prompt, keeping line breaks readable in source and exact in the textarea.

## Verification

- Add a focused static regression test that reads `goose_web/index.html` and verifies the `health` tag,
  all four diagnostic requirements, the final suspicious-items instruction, and the existing
  click-to-fill behavior.
- Run the Goose Web test suite.
- Start the local Goose Web server and use browser screenshots at desktop and mobile widths to confirm
  that all five chips are visible, wrap without overlap, and clicking `health` fills the complete prompt
  without sending it.

## Non-Goals

- Making starter prompts configurable through `config.json` or an API.
- Automatically submitting the prompt.
- Changing the diagnostic prompt wording, MCP availability, or agent execution behavior.
