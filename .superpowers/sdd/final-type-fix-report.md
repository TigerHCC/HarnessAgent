# Final PowerShell manifest type fix report

Date: 2026-07-15

## Root cause and fix

- Setup and watchdog checked required values after string conversion and cast ports with `[int]`.
  PowerShell therefore accepted JSON strings and decimal numbers that Python rejected, and coercion
  could change duplicate identity before uniqueness checks.
- Both PowerShell consumers now reject non-string textual fields and accept ports only when the raw
  decoded value has an integral CLR type. Validation runs in the same order: entry count, presence,
  raw textual and port types, run level, canonical port membership, uniqueness, then normalization.
- Python manifest validation was not changed because its existing contract already requires actual
  non-empty strings and an actual JSON integer.

## TDD evidence

- RED: watchdog returned success for a manifest containing `"port": "8777"`; setup passed manifest
  validation and reached dependency processing for the same malformed input.
- Added setup and watchdog runtime cases for string, integral-decimal, and fractional-decimal ports;
  numeric values in name, directory, task, run level, description, and health tool; and mixed-type
  name, task, and port duplicates whose identity changed under coercion.
- GREEN: all 24 parameterized raw-type/coercion runtime cases passed.

## Verification

- `python -m pytest -p no:cacheprovider tests/test_mcp_manifest.py -q`: 35 passed.
- Windows PowerShell 5.1 parser: `setup_mcp_servers.ps1` and
  `tools/mcp_watchdog/mcp_watchdog.ps1` parsed without errors.
- Windows PowerShell 5.1 watchdog runtime: `-InventoryOnly` returned 14 entries.
- The batch engine was not changed, so no batch-focused suite was required for this fix.
