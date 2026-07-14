"""Test stub that reproduces the dtmsdk wedge primitive: spawn a grandchild that INHERITS this
process's stdout (so it holds the write-end of the runner's capture pipe) and sleeps, then sleep
ourselves. Without the runner's kill-tree + bounded-drain fix, runner.run's post-timeout untimed
communicate() would block forever waiting for the pipe to EOF.
"""
import subprocess
import sys
import time

# grandchild inherits our stdout/stderr (the runner's pipes) and lives past the runner's timeout
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
time.sleep(60)
