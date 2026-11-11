"""scripts/civitai_fetch.py fetch()/status() with fake Sessions, context, page and download objects: no browser, no network."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import civitai_fetch as cf  # noqa: E402


class TimeoutError(Exception):   # same class name playwright's TimeoutError has; the module matches on the name
    pass


class FakeDownload:
    def __init__(self, data):
        self.value = self
        self.data = data

    def save_as(self, dest):
        with open(dest, "wb") as fh:
            fh.write(self.data)


class FakeInfo:
    def __init__(self, page):
        self.page = page
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        if a[0] is None:
            if self.page.timeout:
                raise TimeoutError("no download")
            self.value = FakeDownload(self.page.data) if self.page.data is not None else None
            if self.value is None:
                raise TimeoutError("no download")
        return False


class FakeResp:
    def __init__(self, status):
        self.status = status


class FakePage:
    def __init__(self, signed_in=True, data=b"abc", status=None, timeout=False, goto_raises=True):
        self.signed_in, self.data, self.status, self.timeout, self.goto_raises = signed_in, data, status, timeout, goto_raises
        self.visited = []

    def goto(self, url, **kw):
        self.visited.append(url)
        if url.startswith("https://civitai.com/api/download"):
            if self.status:
                return FakeResp(self.status)
            if self.goto_raises:
                raise RuntimeError("Download is starting")
        return FakeResp(200)

    def evaluate(self, js):
        return self.signed_in

    def expect_download(self, timeout=0):
        return FakeInfo(self)


class FakeCtx:
    def __init__(self, page):
        self.pages = [page]


class FakeSessions:
    def __init__(self, page, busy=False):
        self.page, self.busy, self.opened = page, busy, 0

    def session(self, headless=True):
        sessions = self

        class CM:
            def __enter__(self):
                if sessions.busy:
                    raise RuntimeError("port 9341 busy - a browser is already running on this profile")
                sessions.opened += 1
                return FakeCtx(sessions.page)

            def __exit__(self, *a):
                return False
        return CM()


URL = "https://civitai.com/api/download/models/123"


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = os.path.join(self.tmp.name, "m.safetensors")

    def test_success_writes_the_file(self):
        page = FakePage(data=b"hello")
        out = cf.fetch(FakeSessions(page), URL, self.dest, 5)
        self.assertEqual(out, {"ok": True, "bytes": 5})
        self.assertEqual(open(self.dest, "rb").read(), b"hello")
        self.assertIn(URL, page.visited)

    def test_existing_destination_is_refused_and_untouched(self):
        open(self.dest, "wb").write(b"old")
        s = FakeSessions(FakePage())
        out = cf.fetch(s, URL, self.dest, 5)
        self.assertFalse(out["ok"])
        self.assertEqual(open(self.dest, "rb").read(), b"old")
        self.assertEqual(s.opened, 0)

    def test_signed_out_downloads_nothing(self):
        page = FakePage(signed_in=False)
        out = cf.fetch(FakeSessions(page), URL, self.dest, 5)
        self.assertEqual(out, {"ok": False, "error": "signed_out"})
        self.assertNotIn(URL, page.visited)
        self.assertFalse(os.path.exists(self.dest))

    def test_refused_status_and_no_file(self):
        for st in (401, 403, 404):
            out = cf.fetch(FakeSessions(FakePage(status=st)), URL, self.dest, 5)
            self.assertEqual(out, {"ok": False, "error": "refused"}, st)
        self.assertFalse(os.path.exists(self.dest))

    def test_timeout(self):
        out = cf.fetch(FakeSessions(FakePage(timeout=True)), URL, self.dest, 1)
        self.assertEqual(out, {"ok": False, "error": "timeout"})

    def test_busy_profile(self):
        out = cf.fetch(FakeSessions(FakePage(), busy=True), URL, self.dest, 5)
        self.assertEqual(out, {"ok": False, "error": "browser_busy"})

    def test_status_reports_only_a_boolean(self):
        self.assertEqual(cf.status(FakeSessions(FakePage(signed_in=True))), {"signed_in": True})
        self.assertEqual(cf.status(FakeSessions(FakePage(signed_in=False))), {"signed_in": False})
        self.assertEqual(cf.status(FakeSessions(FakePage(signed_in={"user": {"name": "x"}}))), {"signed_in": True})

    def test_script_never_asks_for_a_password(self):
        src = open(os.path.join(HERE, "..", "scripts", "civitai_fetch.py")).read().lower()
        for bad in ("password", ".fill(", ".type(", "cookies("):
            self.assertNotIn(bad, src.replace("password-free", "").replace("a password", ""))


if __name__ == "__main__":
    unittest.main()
