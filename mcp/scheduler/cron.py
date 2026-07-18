"""Pure schedule math: 5-field cron + one-shot `at`. No I/O, no third-party deps.

Cron resolution is one minute. `next_run` steps minute-by-minute from `now` (capacped) until a match,
which is simple and correct for a minute-resolution scheduler.
"""
from datetime import datetime, timedelta

_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]   # min hour dom month dow (dow: 0=Mon..6=Sun)
_MAX_STEPS = 366 * 24 * 60   # a year of minutes -- guard against an unsatisfiable spec


def _parse_field(field, lo, hi):
    """Return the set of allowed ints for one cron field (`*`, `a`, `a-b`, `*/n`, `a-b/n`, `a,b,c`)."""
    allowed = set()
    for part in field.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError("cron step must be positive: %r" % field)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        if start < lo or end > hi or start > end:
            raise ValueError("cron field out of range: %r" % field)
        allowed.update(range(start, end + 1, step))
    return allowed


def _parse_cron(expr):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError("cron must have 5 fields, got %d: %r" % (len(fields), expr))
    parsed = [_parse_field(f, lo, hi) for f, (lo, hi) in zip(fields, _RANGES)]
    # Convert dow from standard cron (Sun=0, Mon=1) to Python (Mon=0, Sun=6)
    parsed[4] = {(i - 1) % 7 for i in parsed[4]}
    return parsed


def _matches(sets, dt):
    minute, hour, dom, month, dow = sets
    # Python weekday(): Mon=0..Sun=6, matching our dow convention.
    return (dt.minute in minute and dt.hour in hour and dt.day in dom
            and dt.month in month and dt.weekday() in dow)


def _parse_at(expr):
    return datetime.fromisoformat(expr)   # raises ValueError on a malformed datetime


def validate(kind, expr):
    if kind == "cron":
        _parse_cron(expr)
    elif kind == "at":
        _parse_at(expr)
    else:
        raise ValueError("unknown schedule kind: %r" % kind)


def next_run(kind, expr, now):
    if kind == "at":
        when = _parse_at(expr)
        return when if when > now else None
    sets = _parse_cron(expr)
    # start at the next whole minute after `now`
    dt = (now + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for _ in range(_MAX_STEPS):
        if _matches(sets, dt):
            return dt
        dt += timedelta(minutes=1)
    raise ValueError("cron never matches within a year: %r" % expr)


def describe(kind, expr):
    if kind == "at":
        return "一次性 " + _parse_at(expr).strftime("%Y-%m-%d %H:%M")
    m, h, dom, mon, dow = expr.split()
    if expr == "0 * * * *":
        return "每小時"
    if dom == "*" and mon == "*" and dow == "*" and m.isdigit() and h.isdigit():
        return "每日 %02d:%02d" % (int(h), int(m))
    if dom == "*" and mon == "*" and dow != "*" and m.isdigit() and h.isdigit():
        return "每週(%s) %02d:%02d" % (dow, int(h), int(m))
    return "cron " + expr
