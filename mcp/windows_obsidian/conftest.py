"""Make the windows_obsidian modules importable from tests/ regardless of pytest invocation."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
