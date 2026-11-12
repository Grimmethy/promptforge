"""Is there room on this machine for one more picture? Vendored from ~/.local/share/build-guard/headroom.py (same profiles and thresholds); standard library only.

light: io some avg10 <= 85, memory some avg10 <= 30, MemAvailable >= 1200 MB.
heavy: io some avg10 <= 50, io some avg60 <= 60, memory some avg10 <= 15, MemAvailable >= 2500 MB, swap traffic <= 100 pages/s.
A reading that cannot be taken (no /proc/pressure on this kernel, say) counts as OK here: the gate must never block a run forever on a host that cannot measure.
Override a value with HEADROOM_<PROFILE>_<NAME>, for example HEADROOM_LIGHT_IO_SOME10=70.
"""
import os
import time

PROFILES = {
    "heavy": {"io_some10": 50.0, "io_some60": 60.0, "mem_some10": 15.0, "mem_avail_mb": 2500.0, "swap_rate_pps": 100.0},
    "light": {"io_some10": 85.0, "mem_some10": 30.0, "mem_avail_mb": 1200.0},
}
MINIMUMS = ("mem_avail_mb",)        # these must be AT LEAST the bound; every other key must be AT MOST the bound


def _psi(path):
    out = {}
    try:
        with open(path) as fh:
            for line in fh:
                kind, *fields = line.split()
                out[kind] = {k: float(v) for k, v in (f.split("=") for f in fields)}
    except (OSError, ValueError):
        pass
    return out


def _meminfo(path):
    out = {}
    try:
        with open(path) as fh:
            for line in fh:
                k, v = line.split(":", 1)
                out[k] = float(v.split()[0]) / 1024.0           # kB -> MB
    except (OSError, ValueError):
        pass
    return out


def _swap_pages(path):
    total, seen = 0, 0
    try:
        with open(path) as fh:
            for line in fh:
                k, _, v = line.partition(" ")
                if k in ("pswpin", "pswpout"):
                    total += int(v)
                    seen += 1
    except (OSError, ValueError):
        return None
    return total if seen == 2 else None


def limits(profile):
    lim = dict(PROFILES[profile])
    for key in lim:
        env = os.environ.get("HEADROOM_%s_%s" % (profile.upper(), key.upper()))
        if env:
            try:
                lim[key] = float(env)
            except ValueError:
                pass
    return lim


def check(profile="light", proc="/proc", sample_s=1.0, sleep=time.sleep):
    """(ok, reasons). Reads proc/pressure/{io,memory}, proc/meminfo and (heavy only) proc/vmstat twice, sample_s apart."""
    lim = limits(profile)
    io = _psi(os.path.join(proc, "pressure", "io")).get("some", {})
    mem = _psi(os.path.join(proc, "pressure", "memory")).get("some", {})
    mi = _meminfo(os.path.join(proc, "meminfo"))
    rate = None
    if "swap_rate_pps" in lim:
        a = _swap_pages(os.path.join(proc, "vmstat"))
        if a is not None:
            sleep(sample_s)
            b = _swap_pages(os.path.join(proc, "vmstat"))
            rate = None if b is None else max(0.0, (b - a) / sample_s)
    r = {"io_some10": io.get("avg10"), "io_some60": io.get("avg60"), "mem_some10": mem.get("avg10"), "mem_avail_mb": mi.get("MemAvailable"), "swap_rate_pps": rate}
    reasons = []
    for key, bound in lim.items():
        val = r.get(key)
        if val is None:
            continue                                            # unreadable: counts as OK
        if key in MINIMUMS:
            if val < bound:
                reasons.append("%s %.0f < %.0f" % (key, val, bound))
        elif val > bound:
            reasons.append("%s %.1f > %.1f" % (key, val, bound))
    return (not reasons), reasons


def wait(profile="light", max_s=1800, poll=30, sleep=time.sleep, now=time.monotonic, checker=check):
    """Block until check() says OK. True when there is headroom, False when max_s passed first."""
    start = now()
    while True:
        ok, _ = checker(profile)
        if ok:
            return True
        if now() - start >= max_s:
            return False
        sleep(poll)
