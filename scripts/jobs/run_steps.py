"""Step, heartbeat and memory helpers for the render window runner.

heartbeat.json and window-run.log live in <jobs root>/.runner/, the same directory
scripts/jobs/window_run.py and scripts/jobs/runner.py use. The jobs root comes from
PROMPTFORGE_JOBS_DIR, which server/core/job_run.py default_spawn sets for the window run.
Standard library only; no model client is imported.
"""
import contextlib
import datetime
import json
import os
import tempfile
import time

PROC_MEMINFO = "/proc/meminfo"
PROC_PSI_MEMORY = "/proc/pressure/memory"


def _run_dir():
    root = os.environ.get("PROMPTFORGE_JOBS_DIR") or os.getcwd()
    return os.path.join(root, ".runner")


def _heartbeat_path():
    return os.path.join(_run_dir(), "heartbeat.json")


def _log_path():
    return os.path.join(_run_dir(), "window-run.log")


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _atomic_write_json(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _read_heartbeat():
    try:
        with open(_heartbeat_path(), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


def update_heartbeat(**fields):
    """Merge fields into heartbeat.json and write it atomically.

    The read-merge-write is NOT locked across processes. That is acceptable only
    because the window supervisor and the runner write at different times.
    """
    rec = _read_heartbeat()
    rec.update(fields)
    _atomic_write_json(_heartbeat_path(), rec)
    return rec


def _append_log(line):
    os.makedirs(_run_dir(), exist_ok=True)
    with open(_log_path(), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


@contextlib.contextmanager
def step(name):
    """Mark the named step in heartbeat.json and window-run.log for the duration of the block."""
    update_heartbeat(step=name, step_since=_now())
    _append_log("%s step=%s event=enter" % (_now(), name))
    t0 = time.monotonic()
    try:
        yield
    finally:
        duration = time.monotonic() - t0
        _append_log("%s step=%s event=exit duration_s=%.3f" % (_now(), name, duration))


def _meminfo_kb():
    values = {}
    with open(PROC_MEMINFO, encoding="utf-8") as fh:
        for line in fh:
            key, _, rest = line.partition(":")
            if key in ("MemAvailable", "SwapTotal", "SwapFree"):
                values[key] = int(rest.split()[0])
    return values


def _psi_memory_avg10():
    """(some, full) avg10 from /proc/pressure/memory, or (None, None) when PSI is not available."""
    try:
        with open(PROC_PSI_MEMORY, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return None, None
    found = {}
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        for part in parts[1:]:
            if part.startswith("avg10="):
                found[parts[0]] = float(part[len("avg10="):])
    return found.get("some"), found.get("full")


def read_memory_numbers():
    mem = _meminfo_kb()
    some, full = _psi_memory_avg10()
    return {
        "mem_available_kb": mem["MemAvailable"],
        "swap_used_kb": mem["SwapTotal"] - mem["SwapFree"],
        "mem_psi_some_avg10": some,
        "mem_psi_full_avg10": full,
    }
