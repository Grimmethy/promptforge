"""A small PromptForge client for the engine: generate one picture, take and release the GPU session. Standard library only."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple

from .errors import SlotFillError

Generated = namedtuple("Generated", "job name data")
POLL_S = 2.0


class PFClient:
    def __init__(self, base_url=None, sleep=time.sleep, now=time.time):
        self.base = (base_url or os.environ.get("PROMPTFORGE_URL") or "http://localhost:7430").rstrip("/")
        self._sleep = sleep
        self._now = now

    def _call(self, path, body=None, timeout=60, raw=False):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = r.read()
        except urllib.error.HTTPError as e:
            raise SlotFillError("PromptForge %s answered %s" % (path, e.code))
        except (urllib.error.URLError, OSError) as e:
            raise SlotFillError("PromptForge not reachable at %s (%s)" % (self.base, getattr(e, "reason", e)))
        if raw:
            return out
        try:
            return json.loads(out.decode() or "{}")
        except ValueError:
            raise SlotFillError("PromptForge %s answered something that is not JSON" % path)

    def generate(self, spec, timeout=600):
        """POST /api/generate, poll the job every 2 s until done, then fetch the first picture. Raises SlotFillError on a failed job, a missing picture or the timeout."""
        j = self._call("/api/generate", spec)
        jid = j.get("job_id")
        if not jid:
            raise SlotFillError(str(j.get("error") or "no job id")[:200])
        t0 = self._now()
        while True:
            s = self._call("/api/job/" + jid)
            if s.get("status") == "done":
                break
            if s.get("status") in ("error", "failed") or s.get("error"):
                raise SlotFillError(str(s.get("error") or "generation failed")[:200])
            if self._now() - t0 >= timeout:
                raise SlotFillError("timeout")
            self._sleep(POLL_S)
        name = (s.get("images") or [None])[0]
        if not name:
            raise SlotFillError("no image returned")
        data = self._call("/api/image/" + urllib.parse.quote(name, safe="/"), raw=True)
        return Generated(s, name, data)

    def gpu_acquire(self, owner):
        return self._call("/api/gpu/acquire", {"owner": owner}, timeout=180)

    def gpu_release(self):
        return self._call("/api/gpu/release", {})
