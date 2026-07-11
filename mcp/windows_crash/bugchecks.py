"""Curated lookup tables: BSOD bugcheck codes + user-mode NTSTATUS exception codes.

No dependencies. Values are the diagnostic knowledge a raw shell has no access to.
Tables are intentionally the *common* codes, not exhaustive; describe() returns
(None, None) for anything not listed so callers can fall back to the raw code.
"""

# Stop code (bugcheck) -> (name, short human description).
# Keyed by the integer code. The famous / common ones for desktop/laptop diagnosis.
BUGCHECKS = {
    0x0000000A: ("IRQL_NOT_LESS_OR_EQUAL", "Driver accessed pageable/invalid memory at too high an IRQL — usually a faulty driver."),
    0x00000012: ("TRAP_CAUSE_UNKNOWN", "Unknown trap — often hardware or a driver that left no trace."),
    0x0000001A: ("MEMORY_MANAGEMENT", "Memory-management fault — bad RAM, a driver, or corrupted page tables."),
    0x0000001E: ("KMODE_EXCEPTION_NOT_HANDLED", "A kernel-mode program raised an exception the handler didn't catch — usually a driver."),
    0x00000024: ("NTFS_FILE_SYSTEM", "Problem in ntfs.sys — often a failing disk or filesystem corruption."),
    0x0000002B: ("PANIC_STACK_SWITCH", "Kernel stack overflow, often an oversized driver stack usage."),
    0x0000003B: ("SYSTEM_SERVICE_EXCEPTION", "Exception while executing a system service routine — commonly a driver or graphics."),
    0x0000003D: ("INTERRUPT_EXCEPTION_NOT_HANDLED", "Unhandled interrupt exception — driver or hardware."),
    0x00000044: ("MULTIPLE_IRP_COMPLETE_REQUESTS", "A driver completed the same I/O request twice — driver bug."),
    0x00000050: ("PAGE_FAULT_IN_NONPAGED_AREA", "Referenced invalid system memory — faulty RAM, driver, or antivirus."),
    0x0000007A: ("KERNEL_DATA_INPAGE_ERROR", "Couldn't read a kernel page from disk — failing disk/cabling or bad block."),
    0x0000007E: ("SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", "A system thread raised an exception nobody handled — usually a named driver."),
    0x0000007F: ("UNEXPECTED_KERNEL_MODE_TRAP", "A CPU trap (often double-fault) — overclocking, bad RAM, or a driver."),
    0x0000009C: ("MACHINE_CHECK_EXCEPTION", "CPU machine-check — hardware fault (CPU/board/power). See WHEA."),
    0x0000009F: ("DRIVER_POWER_STATE_FAILURE", "A driver didn't complete a power (sleep/resume) transition in time."),
    0x000000A0: ("INTERNAL_POWER_ERROR", "The power policy manager hit an internal error during a power transition."),
    0x000000BE: ("ATTEMPTED_WRITE_TO_READONLY_MEMORY", "A driver tried to write to read-only memory — driver bug."),
    0x000000C1: ("SPECIAL_POOL_DETECTED_MEMORY_CORRUPTION", "Driver Verifier caught a pool buffer overrun — the flagged driver is corrupt."),
    0x000000C2: ("BAD_POOL_CALLER", "A driver made a bad pool allocation/free call — driver bug."),
    0x000000C4: ("DRIVER_VERIFIER_DETECTED_VIOLATION", "Driver Verifier caught an illegal operation — the flagged driver is at fault."),
    0x000000C5: ("DRIVER_CORRUPTED_EXPOOL", "Pool corruption, usually a driver writing out of bounds."),
    0x000000CE: ("DRIVER_UNLOADED_WITHOUT_CANCELLING_PENDING_OPERATIONS", "A driver unloaded but left callbacks/timers active."),
    0x000000D1: ("DRIVER_IRQL_NOT_LESS_OR_EQUAL", "A driver accessed pageable memory at too high an IRQL — the flagged driver."),
    0x000000EF: ("CRITICAL_PROCESS_DIED", "A critical system process ended — corruption, bad update, or failing disk."),
    0x000000F4: ("CRITICAL_OBJECT_TERMINATION", "A critical thread/process terminated unexpectedly — often a failing disk."),
    0x000000F5: ("FLTMGR_FILE_SYSTEM", "Filter Manager fault — a misbehaving filesystem minifilter (AV/backup/VPN)."),
    0x000000FC: ("ATTEMPTED_EXECUTE_OF_NOEXECUTE_MEMORY", "Code tried to execute non-executable memory — driver bug or corruption."),
    0x00000101: ("CLOCK_WATCHDOG_TIMEOUT", "A CPU core stopped responding to clock interrupts — hardware, or overclock/power."),
    0x00000109: ("CRITICAL_STRUCTURE_CORRUPTION", "Kernel detected critical code/data corruption — driver, RAM, or tampering."),
    0x00000116: ("VIDEO_TDR_ERROR", "GPU didn't recover in time (TDR) — display driver or overheating/failing GPU."),
    0x00000117: ("VIDEO_TDR_TIMEOUT_DETECTED", "GPU timed out (TDR) — display driver hang."),
    0x00000119: ("VIDEO_SCHEDULER_INTERNAL_ERROR", "GPU scheduler found a fatal error — display driver or GPU hardware."),
    0x0000011B: ("DRIVER_RETURNED_HOLDING_CANCEL_LOCK", "A driver returned still holding the cancel spinlock — driver bug."),
    0x00000124: ("WHEA_UNCORRECTABLE_ERROR", "Fatal hardware error (CPU/cache/bus/PCIe/RAM). Decode the WHEA/CPER record."),
    0x00000133: ("DPC_WATCHDOG_VIOLATION", "A DPC ran too long or a driver stuck the CPU — often an old storage/SSD driver."),
    0x00000139: ("KERNEL_SECURITY_CHECK_FAILURE", "A kernel data-structure integrity check failed — driver or corruption."),
    0x0000013A: ("KERNEL_MODE_HEAP_CORRUPTION", "The kernel pool/heap is corrupt — a driver overwrote memory."),
    0x00000144: ("BUGCODE_USB3_DRIVER", "USB 3 stack fault — USB driver/device/hub."),
    0x0000014C: ("FATAL_ABNORMAL_RESET_ERROR", "Firmware/hardware requested an abnormal reset."),
    0x00000154: ("UNEXPECTED_STORE_EXCEPTION", "The memory-compression store hit an unexpected exception — driver, AV, or bad RAM."),
    0x0000015E: ("BUGCODE_NDIS_DRIVER_LIVE_DUMP", "NDIS network-driver problem captured as a live dump (no crash)."),
    0x0000019C: ("WHEA_LIVEDUMP", "A hardware error captured as a live dump (machine kept running)."),
    0x000001C8: ("HYPERVISOR_ERROR_LIVEDUMP", "Hypervisor error captured as a live dump."),
    0x00000133: ("DPC_WATCHDOG_VIOLATION", "A DPC ran too long or a driver stuck the CPU — often an old storage/SSD driver."),
    0xDEADDEAD: ("MANUALLY_INITIATED_CRASH", "A crash the user/admin triggered on purpose (e.g. keyboard crashdump or NotMyFault)."),
}

# User-mode exception codes seen in WER Sig[] exception-code fields (NTSTATUS-style, hex string).
# Keyed by lowercase hex WITHOUT 0x, as it appears in Report.wer.
NTSTATUS_EXCEPTIONS = {
    "80000003": ("STATUS_BREAKPOINT", "A breakpoint was hit (often a debugger/assert)."),
    "c0000005": ("ACCESS_VIOLATION", "Read/write to memory the process doesn't own — the #1 crash cause (null/dangling pointer)."),
    "c0000006": ("IN_PAGE_ERROR", "Couldn't page in code/data — often a failing disk or a lost network file."),
    "c000001d": ("ILLEGAL_INSTRUCTION", "The CPU hit an invalid instruction — corruption or bad code-gen."),
    "c0000025": ("NONCONTINUABLE_EXCEPTION", "A non-continuable exception was raised."),
    "c0000026": ("INVALID_DISPOSITION", "An exception handler returned an invalid disposition."),
    "c000008c": ("ARRAY_BOUNDS_EXCEEDED", "Array index out of bounds."),
    "c0000090": ("FLOAT_INVALID_OPERATION", "Invalid floating-point operation."),
    "c0000094": ("INTEGER_DIVIDE_BY_ZERO", "Integer divide by zero."),
    "c0000096": ("PRIVILEGED_INSTRUCTION", "A privileged instruction was executed in user mode."),
    "c00000fd": ("STACK_OVERFLOW", "The thread ran out of stack — unbounded recursion or huge stack allocation."),
    "c0000135": ("DLL_NOT_FOUND", "A required DLL was missing at load time."),
    "c0000138": ("ORDINAL_NOT_FOUND", "A DLL export ordinal wasn't found — version mismatch."),
    "c0000139": ("ENTRYPOINT_NOT_FOUND", "A DLL export wasn't found — version mismatch."),
    "c0000142": ("DLL_INIT_FAILED", "A DLL's initialization routine failed."),
    "c0000374": ("HEAP_CORRUPTION", "The process heap is corrupt — a buffer overrun or double-free."),
    "c0000409": ("STACK_BUFFER_OVERRUN", "/GS stack cookie tripped — a stack buffer overrun (or fail-fast)."),
    "c0000417": ("INVALID_CRUNTIME_PARAMETER", "The C runtime caught an invalid parameter and aborted."),
    "c0000420": ("ASSERTION_FAILURE", "An assertion failed."),
    "cfffffff": ("APPLICATION_HANG", "The app stopped responding (hang), not a hard crash."),
    "e0434352": ("CLR_EXCEPTION", "An unhandled .NET/CLR managed exception."),
    "e0434f4d": ("CLR_EXCEPTION", "An unhandled .NET/CLR managed exception."),
    "e06d7363": ("CPP_EH_EXCEPTION", "An unhandled C++ exception (MSVC throw)."),
}


def describe_bugcheck(code):
    """code: int -> (name, desc) or (None, None)."""
    if code is None:
        return (None, None)
    try:
        return BUGCHECKS.get(int(code), (None, None))
    except (TypeError, ValueError):
        return (None, None)


def describe_exception(hexstr):
    """hexstr: 'c0000005' (any case, optional 0x) -> (name, desc) or (None, None)."""
    if not hexstr:
        return (None, None)
    s = str(hexstr).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    s = s.zfill(8) if len(s) < 8 and s else s
    return NTSTATUS_EXCEPTIONS.get(s, (None, None))
