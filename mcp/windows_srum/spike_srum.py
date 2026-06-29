"""Read-only SRUM schema spike. Run ELEVATED. Writes findings to stdout."""
import os, subprocess, tempfile, sys
from dissect.esedb import EseDB

SRUDB = os.path.join(os.environ["SystemRoot"], "System32", "sru", "SRUDB.dat")


def copy_locked(dst):
    # VSS copy handles the live lock; fall back to plain /y
    last = ""
    for args in (["esentutl.exe", "/y", SRUDB, "/vss", "/d", dst],
                 ["esentutl.exe", "/y", SRUDB, "/d", dst]):
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst):
            return True, " ".join(args)
        last = r.stdout + r.stderr
    print("COPY FAILED:", last)
    return False, last


def _safe(rec, col):
    try:
        return rec.get(col)
    except Exception as e:
        return f"<err {e}>"


def main():
    tmp = os.path.join(tempfile.gettempdir(), "SRUDB_spike.dat")
    ok, how = copy_locked(tmp)
    if not ok:
        sys.exit(1)
    print("copied via:", how)
    print("copy path:", tmp)
    with open(tmp, "rb") as fh:
        db = EseDB(fh)
        for t in db.tables():
            try:
                cols = [c.name for c in t.columns]
            except Exception as e:
                cols = [f"<cols err: {e}>"]
            print(f"\n=== TABLE {t.name} | cols={cols}")
            n = 0
            try:
                for rec in t.records():
                    vals = {c: _safe(rec, c) for c in cols[:8]}
                    print("  row:", vals)
                    n += 1
                    if n >= 2:
                        break
            except Exception as e:
                print("  <records err:", e, ">")


if __name__ == "__main__":
    main()
