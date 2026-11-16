"""Import through the signed-in Civitai session (routes + job): fakes in app.IMPORT_IO, temp folders, no browser, no network."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_models_routes import Base, LINK, SECRET, setUpModule, tearDownModule  # noqa: E402,F401
import app as pf  # noqa: E402
from core import civitai_session as cs, model_importer as mi  # noqa: E402


class SessionImportTest(Base):
    def setUp(self):
        super().setUp()
        self.calls = []
        pf.IMPORT_IO["downloader"] = self.denied
        pf.IMPORT_IO["session"] = self.session_dl
        self.addCleanup(lambda: pf.IMPORT_IO.update(session=None, session_status=None, login=None))
        self.sess_error = None

    def denied(self, url, dest, headers, max_bytes, progress):
        self.calls.append("direct")
        raise mi.AuthRequired("Civitai needs you signed in to download this file")

    def session_dl(self, url, dest):
        self.calls.append("session")
        if self.sess_error:
            raise cs.SessionError(self.sess_error, "x " + SECRET if self.sess_error == "refused" else "x")
        with open(dest, "wb") as fh:
            fh.write(self.data)
        return len(self.data)

    def start(self):
        r = self.post("/api/models/import", url=LINK, confirm=True)
        self.assertEqual(r.status_code, 202)
        return self.wait_job(r.get_json()["job"])

    def test_401_falls_back_to_the_session_and_installs_a_verified_file(self):
        j = self.start()
        self.assertEqual(j["status"], "done", j)
        self.assertEqual(j["via"], "session")
        self.assertEqual(self.calls, ["direct", "session"])
        self.assertTrue(os.path.isfile(os.path.join(self.models, "loras", "ShortStack-Concept-Ilustrious.safetensors")))
        self.assertFalse(j.get("needs_signin"))

    def test_a_corrupt_session_download_is_still_verified_and_discarded(self):
        self.data_bad = b"not a safetensors file"

        def bad(url, dest):
            with open(dest, "wb") as fh:
                fh.write(self.data_bad)
        pf.IMPORT_IO["session"] = bad
        j = self.start()
        self.assertEqual(j["status"], "error")
        self.assertFalse(os.path.exists(os.path.join(self.models, "loras", "ShortStack-Concept-Ilustrious.safetensors")))
        self.assertFalse(os.path.exists(os.path.join(self.models, ".quarantine")))

    def test_signed_out_ends_as_an_error_with_needs_signin_and_frees_the_lock(self):
        self.sess_error = "signed_out"
        j = self.start()
        self.assertEqual((j["status"], j["needs_signin"]), ("error", True))
        self.assertIn("Sign in to Civitai", j["error"])
        self.assertIsNone(pf._import_running[0])
        self.sess_error = None
        self.assertEqual(self.start()["status"], "done")

    def test_refused_and_timeout_are_plain_errors_without_the_key(self):
        for kind in ("refused", "timeout", "unavailable"):
            self.sess_error = kind
            j = self.start()
            self.assertEqual(j["status"], "error", kind)
            self.assertFalse(j.get("needs_signin"))
            self.assertNotIn(SECRET, str(j))
            self.assertIn("signed-in download failed", j["error"])

    def test_no_helper_and_no_fake_gives_a_clear_error(self):
        pf.IMPORT_IO["session"] = None
        with mock.patch.object(cs, "session_python", return_value=None):
            j = self.start()
        self.assertEqual(j["status"], "error")
        self.assertIn("sign-in helper was not found", j["error"])

    def test_a_working_direct_download_never_touches_the_session(self):
        pf.IMPORT_IO["downloader"] = self.downloader
        j = self.start()
        self.assertEqual(j["status"], "done")
        self.assertEqual(self.calls, [])
        self.assertNotIn("via", j)


class SessionRoutesTest(Base):
    def setUp(self):
        super().setUp()
        pf._session_cache.update(at=0.0, value=None)
        self.addCleanup(lambda: (pf._session_cache.update(at=0.0, value=None), pf.IMPORT_IO.update(session_status=None, login=None)))
        self.n = 0

        def status():
            self.n += 1
            return {"signed_in": self.n > 1, "available": True, "error": None}
        pf.IMPORT_IO["session_status"] = status

    def test_status_is_cached_for_a_minute_and_refresh_rechecks(self):
        a = self.c.get("/api/models/session").get_json()
        b = self.c.get("/api/models/session").get_json()
        self.assertEqual((a["signed_in"], b["signed_in"], self.n), (False, False, 1))
        c = self.c.get("/api/models/session?refresh=1").get_json()
        self.assertEqual((c["signed_in"], self.n), (True, 2))
        pf._session_cache["at"] -= 61
        self.c.get("/api/models/session")
        self.assertEqual(self.n, 3)

    def test_status_is_not_checked_during_an_import(self):
        pf._import_running[0] = "job"
        try:
            r = self.c.get("/api/models/session?refresh=1").get_json()
        finally:
            pf._import_running[0] = None
        self.assertEqual(self.n, 0)
        self.assertIsNone(r["signed_in"])

    def test_login_202_then_409_and_nothing_secret_in_responses(self):
        opened = []

        def login():
            if opened:
                raise cs.SessionError("busy", "a Civitai browser window is already open")
            opened.append(1)
        pf.IMPORT_IO["login"] = login
        r = self.c.post("/api/models/session/login")
        self.assertEqual((r.status_code, r.get_json()), (202, {"started": True}))
        r2 = self.c.post("/api/models/session/login")
        self.assertEqual(r2.status_code, 409)
        for resp in (r, r2, self.c.get("/api/models/session")):
            self.assertNotIn(SECRET, resp.get_data(as_text=True))
            for bad in ("cookie", "password"):
                self.assertNotIn(bad, resp.get_data(as_text=True).lower())

    def test_a_sign_in_window_that_exits_at_once_answers_503(self):
        class Dead:
            returncode = 1

            def poll(self):
                return 1

            def wait(self, timeout=None):
                return 1

        pf.IMPORT_IO["login"] = None
        cs._login[0] = None
        self.addCleanup(lambda: cs._login.__setitem__(0, None))
        with mock.patch.object(cs, "session_python", return_value="/fake/bin/python"), \
                mock.patch.object(cs, "port_busy", return_value=False), \
                mock.patch.object(cs.subprocess, "Popen", return_value=Dead()):
            r = self.c.post("/api/models/session/login")
        self.assertEqual(r.status_code, 503)
        self.assertIsNone(cs._login[0])

    def test_login_refused_while_an_import_runs_and_when_unavailable(self):
        pf.IMPORT_IO["login"] = lambda: self.fail("opened a window during an import")
        pf._import_running[0] = "job"
        try:
            self.assertEqual(self.c.post("/api/models/session/login").status_code, 409)
        finally:
            pf._import_running[0] = None

        def gone():
            raise cs.SessionError("unavailable", "session python not found")
        pf.IMPORT_IO["login"] = gone
        self.assertEqual(self.c.post("/api/models/session/login").status_code, 503)


class AuthRequiredTest(unittest.TestCase):
    def test_default_downloader_turns_civitai_401_and_403_into_auth_required(self):
        import io
        import urllib.error
        import tempfile
        for code in (401, 403):
            err = urllib.error.HTTPError("https://civitai.com/api/download/models/1", code, "x", {}, io.BytesIO(b""))
            fake = mock.Mock()
            fake.open.side_effect = err
            with tempfile.TemporaryDirectory() as d, mock.patch.object(mi, "_opener", return_value=fake):
                with self.assertRaises(mi.AuthRequired):
                    mi.default_downloader("https://civitai.com/api/download/models/1", os.path.join(d, "f"))
                self.assertEqual(os.listdir(d), [])

    def test_other_codes_and_other_hosts_stay_plain_errors(self):
        import io
        import urllib.error
        import tempfile
        for url, code in (("https://civitai.com/x", 500), ("https://huggingface.co/x", 401)):
            fake = mock.Mock()
            fake.open.side_effect = urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            with tempfile.TemporaryDirectory() as d, mock.patch.object(mi, "_opener", return_value=fake):
                with self.assertRaises(urllib.error.HTTPError):
                    mi.default_downloader(url, os.path.join(d, "f"))


if __name__ == "__main__":
    unittest.main()
