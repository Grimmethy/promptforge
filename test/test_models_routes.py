"""The model import routes (POST /api/models/plan, POST /api/models/import, GET /api/models/import/<job>) and the registry list with licences.
The importer's fetcher and downloader are fakes (app.IMPORT_IO), every folder is a temp folder, no network, no ComfyUI, no GPU
(python3 -m unittest test.test_models_routes)."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as pf  # noqa: E402
from core import licenses, loras, model_importer as mi  # noqa: E402
from test_model_importer import SAMPLE_1564160, fetcher_for, safetensors_bytes  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
SECRET = "SECRETKEY"
LINK = "https://civitai.com/models/1564160/shortstack-concept-ilustrious"


def _digest(name):
    with open(os.path.join(DATA, name), "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def setUpModule():
    global _before
    _before = {n: _digest(n) for n in ("loras.json", "model-licenses.json")}


def tearDownModule():
    """No import may leak into the real registry or licence table: both files are byte-identical after every test here."""
    assert {n: _digest(n) for n in _before} == _before, "a test wrote to the real data/loras.json or data/model-licenses.json"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.models = os.path.join(self.tmp.name, "models")
        self.reg = os.path.join(self.tmp.name, "loras.json")
        self.lic = os.path.join(self.tmp.name, "model-licenses.json")
        self.data = safetensors_bytes()
        api = json.loads(json.dumps(SAMPLE_1564160))
        api["modelVersions"][0]["files"][0].update(hashes={"SHA256": hashlib.sha256(self.data).hexdigest()}, sizeKB=len(self.data) / 1024)
        self.api = api
        pf.IMPORT_IO["fetcher"] = fetcher_for(api)
        pf.IMPORT_IO["downloader"] = self.downloader
        self.addCleanup(lambda: pf.IMPORT_IO.update(fetcher=None, downloader=None))
        for p in (mock.patch.dict(os.environ, {"COMFYUI_MODELS_DIR": self.models, "PROMPTFORGE_LORAS_DIR": os.path.join(self.models, "loras")}),
                  mock.patch.object(mi, "REGISTRY_PATH", self.reg), mock.patch.object(mi, "LICENSES_PATH", self.lic),
                  mock.patch.object(loras, "REGISTRY_PATH", self.reg), mock.patch.object(licenses, "PATH", self.lic),
                  mock.patch.object(mi, "load_key", return_value=SECRET)):
            p.start()
            self.addCleanup(p.stop)
        self.c = pf.app.test_client()
        self.gate = None
        self.addCleanup(self.drain)          # registered after the patches, so it runs BEFORE they are undone: no job outlives its test

    def drain(self):
        if self.gate:
            self.gate.set()
        end = time.time() + 10
        while pf._import_running[0] and time.time() < end:
            time.sleep(0.02)
        self.assertIsNone(pf._import_running[0], "an import job was still running")

    def downloader(self, url, dest, headers, max_bytes, progress):
        if self.gate:
            self.gate.wait(10)
        if progress:
            progress(len(self.data) // 2, len(self.data))
        with open(dest, "wb") as fh:
            fh.write(self.data)
        return len(self.data)

    def post(self, path, **body):
        return self.c.post(path, json=body)

    def wait_job(self, jid, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            j = self.c.get("/api/models/import/" + jid).get_json()
            if j["status"] in ("done", "error"):
                return j
            time.sleep(0.02)
        self.fail("the import job did not finish")


class PlanRouteTest(Base):
    def test_plan_describes_the_import_and_downloads_nothing(self):
        r = self.post("/api/models/plan", url=LINK)
        self.assertEqual(r.status_code, 200)
        p = r.get_json()
        self.assertEqual((p["type"], p["base"], p["folder"], p["licence"]["commercial_use"]), ("LORA", "illustrious", "loras", "conditional"))
        self.assertEqual(p["trained_words"], ["ShortStack", "Large_Hips"])
        self.assertEqual(p["destination"], os.path.join(self.models, "loras", "ShortStack-Concept-Ilustrious.safetensors"))
        self.assertFalse(os.path.exists(self.models))

    def test_bad_input_is_a_400_with_a_message(self):
        for body in ({}, {"url": ""}, {"url": "https://example.com/x"}, {"url": "http://civitai.com/models/1"}):
            r = self.post("/api/models/plan", **body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn("error", r.get_json())
        self.assertEqual(self.c.post("/api/models/plan", data="not json", content_type="application/json").status_code, 400)

    def test_the_key_never_appears_in_a_response(self):
        def leaky(url, headers):
            raise OSError("failed with %s" % headers.get("Authorization"))
        pf.IMPORT_IO["fetcher"] = leaky
        r = self.post("/api/models/plan", url=LINK)
        self.assertEqual(r.status_code, 400)
        self.assertNotIn(SECRET, r.get_data(as_text=True))

    def test_a_hugging_face_link_needs_a_type(self):
        link = "https://huggingface.co/o/r/blob/main/a.safetensors"
        pf.IMPORT_IO["fetcher"] = lambda u, h: json.dumps([{"path": "a.safetensors", "size": 10}]).encode()
        self.assertEqual(self.post("/api/models/plan", url=link).status_code, 400)
        self.assertEqual(self.post("/api/models/plan", url=link, type="lora").status_code, 200)


    def test_plan_oversize_refused_returns_422_with_plan_fields(self):
        big = json.loads(json.dumps(self.api))
        big["modelVersions"][0]["files"][0]["sizeKB"] = 7 * 1024 * 1024      # 7 GiB, over the 6 GiB LoRA cap (sizeKB is kilobytes)
        pf.IMPORT_IO["fetcher"] = fetcher_for(big)
        r = self.post("/api/models/plan", url=LINK)
        self.assertTrue(400 <= r.status_code < 500, r.status_code)
        p = r.get_json()
        self.assertIn("GB", p["error"])
        for key in ("name", "type", "base_model", "licence", "file"):
            self.assertIn(key, p)
        self.assertIn("name", p["file"])
        self.assertIn("size", p["file"])
        self.assertFalse(os.path.exists(self.models))


class ImportRouteTest(Base):
    def test_confirm_must_be_the_boolean_true(self):
        for confirm in (None, False, "true", 1, "yes", [True]):
            body = {"url": LINK} if confirm is None else {"url": LINK, "confirm": confirm}
            r = self.post("/api/models/import", **body)
            self.assertEqual(r.status_code, 400, confirm)
        self.assertFalse(os.path.exists(self.models))
        self.assertEqual(self.c.post("/api/models/import", data="x", content_type="application/json").status_code, 400)

    def test_a_confirmed_import_runs_to_done_and_the_registry_gains_the_entry(self):
        r = self.post("/api/models/import", url=LINK, confirm=True)
        self.assertEqual(r.status_code, 202)
        job = self.wait_job(r.get_json()["job"])
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["result"]["file"], "ShortStack-Concept-Ilustrious.safetensors")
        self.assertTrue(os.path.isfile(os.path.join(self.models, "loras", "ShortStack-Concept-Ilustrious.safetensors")))
        self.assertGreater(job["bytes"], 0)
        listed = {row["file"]: row for row in self.c.get("/api/loras").get_json()["loras"]}
        row = listed["ShortStack-Concept-Ilustrious.safetensors"]
        self.assertEqual((row["base"], row["present"], row["triggers"]), ("illustrious", True, ["ShortStack", "Large_Hips"]))
        self.assertEqual(row["licence"]["commercial_use"], "conditional")

    def test_the_server_plans_again_and_ignores_a_plan_sent_by_the_browser(self):
        r = self.post("/api/models/import", url=LINK, confirm=True, plan={"file": {"name": "evil.safetensors", "url": "https://evil.example/x"}}, destination="/etc")
        job = self.wait_job(r.get_json()["job"])
        self.assertEqual(job["result"]["file"], "ShortStack-Concept-Ilustrious.safetensors")
        self.assertFalse(os.path.exists(os.path.join(self.models, "loras", "evil.safetensors")))

    def test_a_second_import_while_one_runs_is_a_409(self):
        self.gate = threading.Event()
        first = self.post("/api/models/import", url=LINK, confirm=True)
        self.assertEqual(first.status_code, 202)
        second = self.post("/api/models/import", url=LINK, confirm=True)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["job"], first.get_json()["job"])
        self.assertIn(self.c.get("/api/models/import/" + first.get_json()["job"]).get_json()["status"], ("planning", "downloading"))
        self.gate.set()
        self.assertEqual(self.wait_job(first.get_json()["job"])["status"], "done")

    def test_a_failed_download_ends_the_job_with_a_message_and_frees_the_lock(self):
        def boom(url, dest, headers, max_bytes, progress):
            raise OSError("connection reset %s" % headers.get("Authorization"))
        pf.IMPORT_IO["downloader"] = boom
        job = self.wait_job(self.post("/api/models/import", url=LINK, confirm=True).get_json()["job"])
        self.assertEqual(job["status"], "error")
        self.assertIn("download failed", job["error"])
        self.assertNotIn(SECRET, json.dumps(job))
        self.assertFalse(os.path.isdir(os.path.join(self.models, ".quarantine")))
        pf.IMPORT_IO["downloader"] = self.downloader
        again = self.post("/api/models/import", url=LINK, confirm=True)                             # not stuck on the old job
        self.assertEqual(again.status_code, 202)
        self.assertEqual(self.wait_job(again.get_json()["job"])["status"], "done")

    def test_the_job_passes_through_downloading_and_verifying(self):
        seen = {}
        real_check = mi.check_safetensors

        def downloading(url, dest, headers, max_bytes, progress):
            seen["during_download"] = pf._imports[pf._import_running[0]]["status"]
            return self.downloader(url, dest, headers, max_bytes, progress)

        def verifying(path):
            seen["during_verify"] = pf._imports[pf._import_running[0]]["status"]
            return real_check(path)
        pf.IMPORT_IO["downloader"] = downloading
        with mock.patch.object(mi, "check_safetensors", verifying):
            self.assertEqual(self.wait_job(self.post("/api/models/import", url=LINK, confirm=True).get_json()["job"])["status"], "done")
        self.assertEqual(seen, {"during_download": "downloading", "during_verify": "verifying"})

    def test_an_error_from_inside_the_job_never_carries_the_key(self):
        for exc in (mi.ImportError_("the importer said %s" % SECRET), RuntimeError("something odd with %s" % SECRET)):
            def boom(url, dest, headers, max_bytes, progress, exc=exc):
                raise exc
            pf.IMPORT_IO["downloader"] = boom
            job = self.wait_job(self.post("/api/models/import", url=LINK, confirm=True).get_json()["job"])
            self.assertEqual(job["status"], "error")
            self.assertNotIn(SECRET, json.dumps(job))
            self.assertIn("<key>", job["error"])

    def test_a_bad_link_becomes_an_error_job_and_an_unknown_job_is_404(self):
        job = self.wait_job(self.post("/api/models/import", url="https://example.com/x", confirm=True).get_json()["job"])
        self.assertEqual(job["status"], "error")
        self.assertEqual(self.c.get("/api/models/import/nope").status_code, 404)

    def test_a_file_that_is_already_installed_is_not_overwritten(self):
        os.makedirs(os.path.join(self.models, "loras"))
        dest = os.path.join(self.models, "loras", "ShortStack-Concept-Ilustrious.safetensors")
        with open(dest, "wb") as fh:
            fh.write(b"MINE")
        job = self.wait_job(self.post("/api/models/import", url=LINK, confirm=True).get_json()["job"])
        self.assertEqual(job["status"], "error")
        self.assertIn("already installed", job["error"])
        with open(dest, "rb") as fh:
            self.assertEqual(fh.read(), b"MINE")


class LorasListTest(Base):
    def test_every_row_carries_its_licence_verdict(self):
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "loras.json"), self.reg)       # the seed registry
        rows = self.c.get("/api/loras").get_json()["loras"]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertIn(row["licence"]["commercial_use"], licenses.VALUES)

    def test_the_web_page_loads_models_js_before_app_js_and_the_form_sends_loras(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
        with open(os.path.join(root, "index.html"), encoding="utf-8") as fh:
            html = fh.read()
        with open(os.path.join(root, "app.js"), encoding="utf-8") as fh:
            js = fh.read()
        self.assertLess(html.index("/models.js"), html.index("/app.js"))
        for needle in ('id="impUrl"', 'id="impCheck"', 'id="impGo"', 'id="loraRows"', 'id="loraAdd"', "disabled"):
            self.assertIn(needle, html)
        self.assertIn("confirm: true", js)
        self.assertIn("Checking the link...", js)                # the slow site answer is never a silent wait
        self.assertIn("$('#impCheck').disabled = true", js)
        self.assertIn("spec.loras = PFModels.toSpec(loraRows)", js)
        self.assertIn("!isKreaModel()", js)


if __name__ == "__main__":
    unittest.main()
