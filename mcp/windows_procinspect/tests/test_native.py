import ctypes
import os
import tempfile

import native


def test_struct_sizes():
    # layouts must match the Win32 definitions exactly
    assert ctypes.sizeof(native.RM_PROCESS_INFO) == 668
    assert ctypes.sizeof(native.WAITCHAIN_NODE_INFO) == 280


def test_who_locks_requires_path():
    assert "error" in native.who_locks("")
    assert "error" in native.who_locks(None)


def test_who_locks_self_lock():
    # lock a temp file in THIS process, then confirm we are reported as a holder
    f = tempfile.NamedTemporaryFile(delete=False)
    try:
        f.write(b"x" * 100)
        f.flush()
        r = native.who_locks(f.name)
        assert "error" not in r, r
        assert r["count"] >= 1
        assert any(h["pid"] == os.getpid() for h in r["holders"])
    finally:
        f.close()
        os.unlink(f.name)


def test_wait_chain_requires_target():
    assert "error" in native.wait_chain()


def test_wait_chain_self():
    r = native.wait_chain(pid=os.getpid())
    assert "error" not in r, r
    assert "thread_count" in r and r["thread_count"] >= 1
    assert "deadlock_detected" in r and isinstance(r["blocked_chains"], list)
    # unqueryable threads must be surfaced (so a process that can't be inspected isn't reported deadlock-free)
    assert "unqueryable_threads" in r and isinstance(r["unqueryable_threads"], int)


def test_chain_for_thread_returns_tuple():
    import ctypes as _c
    from ctypes import wintypes as _w
    h = native._adv.OpenThreadWaitChainSession(0, None)
    try:
        c, err = native._chain_for_thread(h, native._k.GetCurrentThreadId())
        assert (c is None) or isinstance(c, dict)
        assert isinstance(err, int)
    finally:
        native._adv.CloseThreadWaitChainSession(h)


def test_wait_chain_bad_pid():
    assert "error" in native.wait_chain(pid=0x7FFFFFFF)
