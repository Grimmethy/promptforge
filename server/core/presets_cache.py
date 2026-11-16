# Mtime/size-keyed cache for file-backed presets data.
#
# get() serves the cached value while every source file's (st_mtime_ns, st_size)
# signature is unchanged. Otherwise it calls load(), stores the result with the new
# signature, and returns it. There are no timers, threads, or watchers: a refresh
# happens only when get() is called.
import os
import threading

_MISSING = (None, None)


def _signature(sources):
    sig = []
    for path in sources:
        try:
            st = os.stat(path)
            sig.append((st.st_mtime_ns, st.st_size))
        except OSError:
            sig.append(_MISSING)
    return tuple(sig)


class PresetsCache:
    def __init__(self, sources, load):
        self._sources = list(sources)
        self._load = load
        self._lock = threading.Lock()
        self._sig = None
        self._value = None
        self._has_value = False

    def get(self):
        with self._lock:
            sig = _signature(self._sources)
            if self._has_value and sig == self._sig:
                return self._value
            value = self._load()
            self._value = value
            self._sig = sig
            self._has_value = True
            return value
