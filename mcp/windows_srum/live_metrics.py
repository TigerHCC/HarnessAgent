"""Live system metrics via psutil (+ WMI battery discharge). Pure functions, no MCP deps."""
import time
import psutil


def _net_disk_rates(interval=0.5):
    n0, d0 = psutil.net_io_counters(pernic=True), psutil.disk_io_counters()
    t0 = time.time()
    time.sleep(interval)
    n1, d1 = psutil.net_io_counters(pernic=True), psutil.disk_io_counters()
    dt = max(time.time() - t0, 1e-3)
    nics = []
    tot_s = tot_r = 0.0
    for name, c1 in n1.items():
        c0 = n0.get(name)
        if not c0:
            continue
        s = (c1.bytes_sent - c0.bytes_sent) / dt
        r = (c1.bytes_recv - c0.bytes_recv) / dt
        nics.append({"name": name, "sent_bytes_per_s": round(s, 1), "recv_bytes_per_s": round(r, 1)})
        tot_s += s
        tot_r += r
    disk = {"read_bytes_per_s": 0.0, "write_bytes_per_s": 0.0}
    if d0 and d1:
        disk = {"read_bytes_per_s": round((d1.read_bytes - d0.read_bytes) / dt, 1),
                "write_bytes_per_s": round((d1.write_bytes - d0.write_bytes) / dt, 1)}
    return nics, round(tot_s, 1), round(tot_r, 1), disk


def _battery_discharge_mw():
    """mW discharge rate via WMI root\\wmi BatteryStatus; None if unavailable/desktop/on AC."""
    try:
        import wmi
        w = wmi.WMI(namespace="root\\wmi")
        for b in w.BatteryStatus():
            dr = getattr(b, "DischargeRate", 0) or 0
            if dr:
                return int(dr)
    except Exception:
        return None
    return None


def _power():
    batt = psutil.sensors_battery()
    if batt is None:
        return {"battery_percent": None, "plugged_in": None, "secs_left": None, "discharge_rate_mw": None}
    secs = None if batt.secsleft in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED) else batt.secsleft
    return {"battery_percent": round(batt.percent, 1), "plugged_in": bool(batt.power_plugged),
            "secs_left": secs, "discharge_rate_mw": _battery_discharge_mw()}


def top_processes(by="cpu", n=10):
    procs = []
    for p in psutil.process_iter(["pid", "name", "memory_info", "memory_percent"]):
        try:
            cpu = p.cpu_percent(None)  # since last call for this proc (primed by snapshot())
            info = p.info
            procs.append({"pid": info["pid"], "name": info["name"] or "?",
                          "cpu": cpu,
                          "rss": getattr(info.get("memory_info"), "rss", 0),
                          "mem%": round(info.get("memory_percent") or 0, 2)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key = "cpu" if by == "cpu" else "rss"
    procs.sort(key=lambda x: x[key], reverse=True)
    return procs[:max(1, n)]


def snapshot():
    psutil.cpu_percent(None)  # prime total cpu
    for p in psutil.process_iter():  # prime per-proc cpu counters
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    nics, tot_s, tot_r, disk = _net_disk_rates()
    per_core = psutil.cpu_percent(percpu=True)
    vm, sm = psutil.virtual_memory(), psutil.swap_memory()
    return {
        "cpu": {"percent_total": round(sum(per_core) / len(per_core), 1) if per_core else psutil.cpu_percent(),
                "percent_per_core": [round(c, 1) for c in per_core]},
        "memory": {"total": vm.total, "available": vm.available, "used": vm.used,
                   "percent": vm.percent, "swap_total": sm.total, "swap_used": sm.used},
        "disk_io": disk,
        "network": {"per_nic": nics, "total_sent_per_s": tot_s, "total_recv_per_s": tot_r},
        "power": _power(),
        "uptime_seconds": round(time.time() - psutil.boot_time()),
        "top_cpu": top_processes("cpu", 5),
        "top_mem": top_processes("memory", 5),
    }
