"""Stand-in for a DTP util: echoes how it was invoked so the runner can be tested without a real
util (and without any data egress). Controlled by argv:
  --emit json|yaml|text   what to print on stdout
  --exit N                exit code
  --sleep S               sleep S seconds (to exercise the timeout path)
"""
import json
import os
import sys
import time


def main():
    argv = sys.argv[1:]
    emit = "json"
    code = 0
    sleep = 0.0
    for i, a in enumerate(argv):
        if a == "--emit" and i + 1 < len(argv):
            emit = argv[i + 1]
        elif a == "--exit" and i + 1 < len(argv):
            code = int(argv[i + 1])
        elif a == "--sleep" and i + 1 < len(argv):
            sleep = float(argv[i + 1])
    if sleep:
        time.sleep(sleep)
    payload = {"argv": argv, "json_env": os.environ.get("DTPUTIL_JSON_OUTPUT")}
    if emit == "json":
        print(json.dumps(payload))
    elif emit == "yaml":
        print("argv:")
        for a in argv:
            print("  - %s" % a)
    else:
        print("plain text output " + " ".join(argv))
    sys.exit(code)


if __name__ == "__main__":
    main()
