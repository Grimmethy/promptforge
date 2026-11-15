"""Model importer core: parse a link, build a plan from a recorded Civitai answer, download into quarantine, verify, place without overwriting,
write the registry entry and the licence row. Every HTTP call is injected (a fake fetcher and downloader), temp folders only, no network,
no ComfyUI, no GPU (python3 -m unittest test.test_model_importer)."""
import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from core import model_importer as mi  # noqa: E402

SAMPLE_1564160 = {"id": 1564160, "name": "ShortStack-Concept-Ilustrious", "type": "LORA", "nsfw": False,
                  "allowCommercialUse": ["Image", "RentCivit", "Rent", "Sell", "SellMerge"],
                  "modelVersions": [{"id": 1770005, "name": "IL-ST", "baseModel": "Illustrious", "trainedWords": ["ShortStack", "Large_Hips"],
                                     "files": [{"name": "ShortStack-Concept-Ilustrious.safetensors", "sizeKB": 223113.14, "primary": True,
                                                "hashes": {"SHA256": "ABCDEF0123456789"},
                                                "downloadUrl": "https://civitai.com/api/download/models/1770005"}]}]}
SAMPLE_RENT_ONLY = {"id": 922648, "name": "Shortstack", "type": "LORA", "nsfw": True, "allowCommercialUse": ["RentCivit", "Rent"],
                    "modelVersions": [{"id": 1201137, "name": "Illustrious", "baseModel": "Illustrious", "trainedWords": ["Shortstack "],
                                       "files": [{"name": "Shortstack-000004.safetensors", "sizeKB": 100, "hashes": {},
                                                  "downloadUrl": "https://civitai.com/api/download/models/1201137"}]},
                                      {"id": 1032782, "name": "V1", "baseModel": "Pony", "trainedWords": ["Shortstack"],
                                       "files": [{"name": "Shortstack-pony.safetensors", "sizeKB": 100, "hashes": {},
                                                  "downloadUrl": "https://civitai.com/api/download/models/1032782"}]}]}


def read(path, mode="rb"):
    with open(path, mode) as fh:
        return fh.read()


def write(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w") as fh:
        fh.write(data)


def safetensors_bytes(extra=b"", tensors=1):
    header = {"__metadata__": {"k": "v"}}
    for i in range(tensors):
        header["t%d" % i] = {"dtype": "F16", "shape": [2], "data_offsets": [i * 4, i * 4 + 4]}
    raw = json.dumps(header).encode()
    return struct.pack("<Q", len(raw)) + raw + b"\x00" * (4 * tensors) + extra


def fetcher_for(api):
    calls = []

    def fetch(url, headers):
        calls.append((url, dict(headers)))
        return json.dumps(api).encode()
    fetch.calls = calls
    return fetch


def downloader_for(data, seen=None):
    def dl(url, dest, headers, max_bytes, progress):
        if seen is not None:
            seen.append((url, dict(headers)))
        with open(dest, "wb") as fh:
            fh.write(data)
        return len(data)
    return dl


class LinkTest(unittest.TestCase):
    def test_the_four_example_links_on_both_domains(self):
        for model_id, slug in ((922648, "shortstack"), (1564160, "shortstack-concept-ilustrious"), (2085746, "shortstackstyle"), (1715377, "shortstacks")):
            for host in ("civitai.com", "civitai.red", "www.civitai.com"):
                self.assertEqual(mi.parse_link("https://%s/models/%d/%s" % (host, model_id, slug)),
                                 {"site": "civitai", "model_id": model_id, "version_id": None})
        self.assertEqual(mi.parse_link("https://civitai.com/models/5?modelVersionId=77")["version_id"], 77)
        self.assertEqual(mi.parse_link("https://civitai.com/models/5/")["model_id"], 5)

    def test_hugging_face_file_links(self):
        self.assertEqual(mi.parse_link("https://huggingface.co/owner/repo/blob/main/dir/a%20b.safetensors"),
                         {"site": "huggingface", "repo": "owner/repo", "rev": "main", "path": "dir/a b.safetensors"})

    def test_refusals(self):
        for bad in ("", "not a link", "http://civitai.com/models/1", "https://evil.example/models/1", "https://civitai.com/user/x",
                    "https://civitai.com/models/abc", "https://civitai.com.evil.example/models/1", "https://huggingface.co/owner/repo",
                    "ftp://civitai.com/models/1", None):
            with self.assertRaises(mi.ImportError_, msg=repr(bad)):
                mi.parse_link(bad)


class PlanTest(unittest.TestCase):
    def plan(self, api, link="https://civitai.com/models/1564160/x", **kw):
        return mi.fetch_plan(link, fetcher_for(api), models_dir=kw.pop("models_dir", "/nonexistent-models"), **kw)

    def test_plan_from_the_recorded_answer(self):
        p = self.plan(SAMPLE_1564160)
        self.assertEqual((p["type"], p["base"], p["base_model"], p["folder"]), ("LORA", "illustrious", "Illustrious", "loras"))
        self.assertEqual(p["trained_words"], ["ShortStack", "Large_Hips"])
        self.assertEqual(p["file"], {"name": "ShortStack-Concept-Ilustrious.safetensors", "size_bytes": 228467855,
                                     "sha256": "abcdef0123456789", "url": "https://civitai.com/api/download/models/1770005"})
        self.assertEqual(p["destination"], "/nonexistent-models/loras/ShortStack-Concept-Ilustrious.safetensors")
        self.assertEqual(p["name"], "ShortStack-Concept-Ilustrious - IL-ST")
        self.assertEqual(p["warnings"], [])
        self.assertFalse(p["nsfw"])

    def test_commercial_verdicts_come_from_the_image_flag(self):
        self.assertEqual(self.plan(SAMPLE_1564160)["licence"]["commercial_use"], "conditional")
        rent = self.plan(SAMPLE_RENT_ONLY)
        self.assertEqual(rent["licence"]["commercial_use"], "no")
        self.assertIn("RentCivit", rent["licence"]["text"])
        self.assertTrue(rent["nsfw"])
        unknown = dict(SAMPLE_1564160)
        unknown.pop("allowCommercialUse")
        self.assertEqual(self.plan(unknown)["licence"]["commercial_use"], "unverified")
        self.assertEqual(self.plan(dict(SAMPLE_1564160, allowCommercialUse="Image"))["licence"]["commercial_use"], "conditional")

    def test_version_selection(self):
        self.assertEqual(self.plan(SAMPLE_RENT_ONLY)["version_id"], 1201137)             # the newest listed
        p = self.plan(SAMPLE_RENT_ONLY, link="https://civitai.com/models/922648?modelVersionId=1032782")
        self.assertEqual((p["version_id"], p["base"], p["file"]["name"]), (1032782, "pony", "Shortstack-pony.safetensors"))
        with self.assertRaises(mi.ImportError_):
            self.plan(SAMPLE_RENT_ONLY, link="https://civitai.com/models/922648?modelVersionId=5")

    def test_a_file_name_with_a_path_stays_inside_the_folder(self):
        api = json.loads(json.dumps(SAMPLE_1564160))
        api["modelVersions"][0]["files"][0]["name"] = "../../x/evil.safetensors"
        p = self.plan(api)
        self.assertEqual(p["file"]["name"], "evil.safetensors")
        self.assertEqual(p["destination"], "/nonexistent-models/loras/evil.safetensors")

    def test_a_pickle_a_big_file_and_an_unplaceable_type_are_refused(self):
        pickle = json.loads(json.dumps(SAMPLE_1564160))
        pickle["modelVersions"][0]["files"][0]["name"] = "model.ckpt"
        with self.assertRaisesRegex(mi.ImportError_, "safetensors"):
            self.plan(pickle)
        with self.assertRaisesRegex(mi.ImportError_, "limit"):
            self.plan(SAMPLE_1564160, max_bytes=1024)
        with self.assertRaisesRegex(mi.ImportError_, "cannot place"):
            self.plan(dict(SAMPLE_1564160, type="Workflows"))

    def test_an_existing_file_and_an_unknown_base_are_warnings(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "loras"))
            open(os.path.join(d, "loras", "ShortStack-Concept-Ilustrious.safetensors"), "wb").close()
            api = json.loads(json.dumps(SAMPLE_1564160))
            api["modelVersions"][0]["baseModel"] = "Mystery 9"
            p = self.plan(api, models_dir=d)
            self.assertEqual(len(p["warnings"]), 2)
            self.assertTrue(any("already installed" in w for w in p["warnings"]))
            self.assertIsNone(p["base"])

    def test_bad_answers_are_import_errors(self):
        for body in (b"not json", b"[]", b"{}"):
            with self.assertRaises(mi.ImportError_, msg=body):
                mi.fetch_plan("https://civitai.com/models/1", lambda u, h, b=body: b, models_dir="/x")

    def test_the_key_goes_only_in_the_civitai_request(self):
        f = fetcher_for(SAMPLE_1564160)
        mi.fetch_plan("https://civitai.com/models/1564160", f, key="SECRETKEY", models_dir="/x")
        self.assertEqual(f.calls[0][1], {"Authorization": "Bearer SECRETKEY"})
        f2 = fetcher_for(SAMPLE_1564160)
        mi.fetch_plan("https://civitai.com/models/1564160", f2, models_dir="/x")
        self.assertEqual(f2.calls[0][1], {})

    def test_hugging_face_needs_a_type_and_reads_the_tree(self):
        tree = [{"path": "a/b.safetensors", "size": 1000, "lfs": {"oid": "AA11"}}, {"path": "other.txt", "size": 1}]
        link = "https://huggingface.co/o/r/blob/main/a/b.safetensors"
        with self.assertRaisesRegex(mi.ImportError_, "type"):
            mi.fetch_plan(link, lambda u, h: json.dumps(tree).encode(), models_dir="/x")
        p = mi.fetch_plan(link, lambda u, h: json.dumps(tree).encode(), models_dir="/x", type_override="lora")
        self.assertEqual((p["file"]["name"], p["file"]["sha256"], p["folder"], p["licence"]["commercial_use"]), ("b.safetensors", "aa11", "loras", "unverified"))
        with self.assertRaises(mi.ImportError_):
            mi.fetch_plan("https://huggingface.co/o/r/blob/main/nope.safetensors", lambda u, h: json.dumps(tree).encode(), models_dir="/x", type_override="lora")


MULTI_VERSION_SAMPLE = {"id": 4242, "name": "Multi Concept", "type": "LORA", "nsfw": False,
                        "allowCommercialUse": ["Image"],
                        "modelVersions": [
                            {"id": 101, "name": "NAI-1", "baseModel": "NoobAI", "trainedWords": ["multi"],
                             "files": [{"name": "multi-noobai.safetensors", "sizeKB": 100, "primary": True, "hashes": {},
                                        "downloadUrl": "https://civitai.com/api/download/models/101"}]},
                            {"id": 202, "name": "IL-1", "baseModel": "Illustrious", "trainedWords": ["multi"],
                             "files": [{"name": "multi-illustrious.safetensors", "sizeKB": 200, "primary": True, "hashes": {},
                                        "downloadUrl": "https://civitai.com/api/download/models/202"}]}]}


def _is_mismatch(w):
    """True for the base-mismatch warning (wording lives in fetch_plan only)."""
    return "used by no registered checkpoint" in str(w).lower()


class VersionSelectionTest(unittest.TestCase):
    def plan(self, registered, **kw):
        fetch = fetcher_for(MULTI_VERSION_SAMPLE)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(mi, "_registered_bases", return_value=registered):
            p = mi.fetch_plan("https://civitai.com/models/4242", fetch, models_dir=d, **kw)
        self.assertGreaterEqual(len(fetch.calls), 1)
        return p

    def test_default_selects_fitting_base(self):
        p = self.plan({"noobai"})
        self.assertEqual(len(p["versions"]), 2)
        sel = {v["id"]: v["selected"] for v in p["versions"]}
        self.assertEqual(sel, {101: True, 202: False})
        self.assertEqual(p["version_id"], 101)

    def test_explicit_model_version_id_wins(self):
        p = self.plan({"noobai"}, modelVersionId=202)
        self.assertEqual(p["version_id"], 202)
        sel = {v["id"]: v["selected"] for v in p["versions"]}
        self.assertEqual(sel, {101: False, 202: True})

    def test_versions_list_keys(self):
        p = self.plan({"noobai"})
        for v in p["versions"]:
            self.assertEqual(set(v.keys()), {"id", "name", "base_model", "base", "file_name", "size_bytes", "selected"})
        self.assertEqual([(v["id"], v["name"], v["base_model"], v["base"], v["file_name"], v["size_bytes"])
                          for v in p["versions"]],
                         [(101, "NAI-1", "NoobAI", "noobai", "multi-noobai.safetensors", 102400),
                          (202, "IL-1", "Illustrious", "illustrious", "multi-illustrious.safetensors", 204800)])

    def test_mismatch_warning_present(self):
        p = self.plan({"noobai"}, modelVersionId=202)
        self.assertTrue(any(_is_mismatch(w) for w in p["warnings"]), p["warnings"])

    def test_no_warning_when_base_fits(self):
        p = self.plan({"noobai"})
        self.assertFalse(any(_is_mismatch(w) for w in p["warnings"]), p["warnings"])

    def test_no_warning_when_no_checkpoints(self):
        p = self.plan(set())
        self.assertFalse(any(_is_mismatch(w) for w in p["warnings"]), p["warnings"])


def make_plan(data, **kw):
    api = json.loads(json.dumps(SAMPLE_1564160))
    api["modelVersions"][0]["files"][0]["hashes"] = {"SHA256": hashlib.sha256(data).hexdigest()} if kw.pop("good_hash", True) else {"SHA256": "00" * 32}
    api["modelVersions"][0]["files"][0]["sizeKB"] = len(data) / 1024
    return mi.fetch_plan("https://civitai.com/models/1564160", fetcher_for(api), models_dir=kw.pop("models_dir"), **kw)


class ExecuteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.models = os.path.join(self.tmp.name, "models")
        self.reg = os.path.join(self.tmp.name, "data", "loras.json")
        self.lic = os.path.join(self.tmp.name, "data", "model-licenses.json")
        self.data = safetensors_bytes()

    def run_plan(self, data=None, plan=None, **kw):
        data = self.data if data is None else data
        plan = plan or make_plan(data, models_dir=self.models)
        return mi.execute_plan(plan, self.models, downloader_for(data), registry_path=self.reg, licenses_path=self.lic, **kw)

    def tree(self):
        return sorted(os.path.relpath(os.path.join(r, f), self.models) for r, _d, fs in os.walk(self.models) for f in fs)

    def test_a_good_import_places_the_file_and_writes_registry_and_licence(self):
        res = self.run_plan()
        self.assertEqual(res["destination"], os.path.join(self.models, "loras", "ShortStack-Concept-Ilustrious.safetensors"))
        self.assertEqual(read(res["destination"]), self.data)
        self.assertEqual(self.tree(), ["loras/ShortStack-Concept-Ilustrious.safetensors"])             # quarantine is gone
        self.assertFalse(os.path.exists(os.path.join(self.models, ".quarantine")))
        reg = json.loads(read(self.reg))
        e, = reg["loras"]
        self.assertEqual((e["id"], e["file"], e["base"], e["triggers"], e["default_weight"]),
                         ("shortstack_concept_ilustrious_il_st", "ShortStack-Concept-Ilustrious.safetensors", "illustrious", ["ShortStack", "Large_Hips"], 0.8))
        self.assertEqual(res["registry_id"], e["id"])
        lic = json.loads(read(self.lic))["ShortStack-Concept-Ilustrious.safetensors"]
        self.assertEqual((lic["commercial_use"], lic["source_url"]), ("conditional", "https://civitai.com/models/1564160"))
        self.assertIn("Image", lic["licence"])
        self.assertEqual(res["licence"], "conditional")

    def test_a_hash_mismatch_leaves_nothing_behind(self):
        plan = make_plan(self.data, models_dir=self.models, good_hash=False)
        with self.assertRaisesRegex(mi.ImportError_, "SHA256"):
            self.run_plan(plan=plan)
        self.assertEqual(self.tree(), [])
        self.assertFalse(os.path.exists(self.reg))
        self.assertFalse(os.path.exists(self.lic))

    def test_execute_plan_refuses_a_non_safetensors_name_even_in_a_hand_made_plan(self):
        plan = make_plan(self.data, models_dir=self.models)
        for bad in ("evil.ckpt", "evil.pt", "evil", "evil.safetensors.exe"):
            plan["file"]["name"] = bad
            dl = mock.Mock()
            with self.assertRaisesRegex(mi.ImportError_, "safetensors"):
                mi.execute_plan(plan, self.models, dl, registry_path=self.reg, licenses_path=self.lic)
            dl.assert_not_called()
        plan["file"]["name"] = "../../escape.safetensors"                  # a path in the name cannot climb out of the folder
        mi.execute_plan(plan, self.models, downloader_for(self.data), registry_path=self.reg, licenses_path=self.lic)
        self.assertEqual(self.tree(), ["loras/escape.safetensors"])

    def test_a_corrupt_or_pickle_file_is_refused(self):
        for bad in (b"", b"short", b"\x80\x04pickle" + b"x" * 40, struct.pack("<Q", 5) + b"notjs" + b"x" * 10,
                    struct.pack("<Q", 2) + b"[]", struct.pack("<Q", 10_000_000) + b"{}"):
            plan = make_plan(bad or b"x", models_dir=self.models)
            plan["file"]["sha256"] = None
            with self.assertRaises(mi.ImportError_, msg=bad[:12]):
                self.run_plan(data=bad, plan=plan)
            self.assertEqual(self.tree(), [])
        raw = json.dumps({"t": {"dtype": "F16", "shape": [2], "data_offsets": [0, 99999]}}).encode()
        plan = make_plan(b"x", models_dir=self.models)
        plan["file"]["sha256"] = None
        with self.assertRaisesRegex(mi.ImportError_, "outside"):
            self.run_plan(data=struct.pack("<Q", len(raw)) + raw + b"\x00" * 4, plan=plan)

    def test_an_existing_destination_is_never_overwritten(self):
        os.makedirs(os.path.join(self.models, "loras"))
        dest = os.path.join(self.models, "loras", "ShortStack-Concept-Ilustrious.safetensors")
        write(dest, b"MINE")
        with self.assertRaisesRegex(mi.ImportError_, "already installed"):
            self.run_plan()
        dl = mock.Mock()
        with self.assertRaisesRegex(mi.ImportError_, "already installed"):
            mi.execute_plan(make_plan(self.data, models_dir=self.models), self.models, dl, registry_path=self.reg, licenses_path=self.lic)
        dl.assert_not_called()                                           # refused before anything is downloaded
        self.assertEqual(read(dest), b"MINE")
        self.assertFalse(os.path.exists(self.reg))
        # and the file appearing between the pre-check and the move
        os.unlink(dest)
        plan = make_plan(self.data, models_dir=self.models)

        def racing(url, path, headers, max_bytes, progress):
            write(path, self.data)
            write(dest, b"RACE")
        with self.assertRaisesRegex(mi.ImportError_, "already installed"):
            mi.execute_plan(plan, self.models, racing, registry_path=self.reg, licenses_path=self.lic)
        self.assertEqual(read(dest), b"RACE")
        self.assertEqual(self.tree(), ["loras/ShortStack-Concept-Ilustrious.safetensors"])

    def test_a_download_error_and_an_oversize_body_leave_nothing(self):
        plan = make_plan(self.data, models_dir=self.models)

        def boom(*a):
            raise OSError("connection reset")
        with self.assertRaisesRegex(mi.ImportError_, "download failed"):
            mi.execute_plan(plan, self.models, boom, registry_path=self.reg, licenses_path=self.lic)
        with self.assertRaisesRegex(mi.ImportError_, "limit"):
            mi.execute_plan(plan, self.models, downloader_for(self.data), registry_path=self.reg, licenses_path=self.lic, max_bytes=8)
        self.assertEqual(self.tree(), [])
        self.assertFalse(os.path.isdir(os.path.join(self.models, ".quarantine")))

    def test_a_second_import_updates_instead_of_duplicating(self):
        self.run_plan()
        os.unlink(os.path.join(self.models, "loras", "ShortStack-Concept-Ilustrious.safetensors"))
        self.run_plan()
        self.assertEqual(len(json.loads(read(self.reg))["loras"]), 1)
        self.assertEqual(len(json.loads(read(self.lic))), 1)

    def test_two_loras_with_the_same_name_get_distinct_ids_and_existing_registry_keys_survive(self):
        os.makedirs(os.path.dirname(self.reg))
        write(self.reg, json.dumps({"loras": [{"id": "shortstack_concept_ilustrious_il_st", "file": "other.safetensors"}], "checkpoints": {"a.safetensors": "pony"}}))
        self.run_plan()
        reg = json.loads(read(self.reg))
        self.assertEqual([e["id"] for e in reg["loras"]], ["shortstack_concept_ilustrious_il_st", "shortstack_concept_ilustrious_il_st_2"])
        self.assertEqual(reg["checkpoints"], {"a.safetensors": "pony"})

    def test_a_checkpoint_records_its_base_and_an_embedding_only_lands_in_its_folder(self):
        api = json.loads(json.dumps(SAMPLE_1564160))
        api["type"] = "Checkpoint"
        api["modelVersions"][0]["files"][0].update(name="cp.safetensors", hashes={}, sizeKB=1)
        plan = mi.fetch_plan("https://civitai.com/models/1", fetcher_for(api), models_dir=self.models)
        mi.execute_plan(plan, self.models, downloader_for(self.data), registry_path=self.reg, licenses_path=self.lic)
        self.assertTrue(os.path.isfile(os.path.join(self.models, "checkpoints", "cp.safetensors")))
        reg = json.loads(read(self.reg))
        self.assertEqual((reg["loras"], reg["checkpoints"]), ([], {"cp.safetensors": "illustrious"}))
        api["type"] = "TextualInversion"
        api["modelVersions"][0]["files"][0]["name"] = "emb.safetensors"
        plan = mi.fetch_plan("https://civitai.com/models/1", fetcher_for(api), models_dir=self.models)
        mi.execute_plan(plan, self.models, downloader_for(self.data), registry_path=self.reg, licenses_path=self.lic)
        self.assertTrue(os.path.isfile(os.path.join(self.models, "embeddings", "emb.safetensors")))
        self.assertEqual(len(json.loads(read(self.reg))["loras"]), 0)

    def test_the_key_reaches_the_download_only_on_a_civitai_url(self):
        seen = []
        plan = make_plan(self.data, models_dir=self.models)
        mi.execute_plan(plan, self.models, downloader_for(self.data, seen), registry_path=self.reg, licenses_path=self.lic, key="SECRETKEY")
        self.assertEqual(seen[0][1], {"Authorization": "Bearer SECRETKEY"})
        os.unlink(os.path.join(self.models, "loras", "ShortStack-Concept-Ilustrious.safetensors"))
        plan["file"]["url"] = "https://cdn.example.net/file.safetensors"
        seen.clear()
        mi.execute_plan(plan, self.models, downloader_for(self.data, seen), registry_path=self.reg, licenses_path=self.lic, key="SECRETKEY")
        self.assertEqual(seen[0][1], {})


class SizeCapTest(unittest.TestCase):
    """The size cap depends on the model type: 6 GiB for a lora or embedding, 12 GiB for a checkpoint (a full SDXL / Illustrious checkpoint is about 6.9 GB),
    and an import needs the file's size plus 2 GiB free on the models disk."""
    CHECKPOINT_BYTES = 6938504356          # the real size Civitai reported for Nova Flat XL (2026-11-13)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.models = os.path.join(self.tmp.name, "models")

    def api(self, model_type, size_bytes, name="big.safetensors"):
        api = json.loads(json.dumps(SAMPLE_1564160))
        api["type"] = model_type
        api["modelVersions"][0]["files"][0].update(name=name, hashes={}, sizeKB=size_bytes / 1024)
        return api

    def plan(self, model_type, size_bytes, **kw):
        return mi.fetch_plan("https://civitai.com/models/1", fetcher_for(self.api(model_type, size_bytes)), models_dir=self.models, **kw)

    def test_the_caps_per_type(self):
        self.assertEqual(mi.max_bytes_for("Checkpoint"), 12 * 1024 ** 3)
        self.assertEqual(mi.max_bytes_for("checkpoint"), 12 * 1024 ** 3)
        for other in ("LORA", "lora", "TextualInversion", "embedding", None, ""):
            self.assertEqual(mi.max_bytes_for(other), 6 * 1024 ** 3)

    def test_a_6_9_gb_checkpoint_plan_is_accepted_and_the_same_size_lora_is_refused(self):
        p = self.plan("Checkpoint", self.CHECKPOINT_BYTES)
        self.assertEqual((p["folder"], p["file"]["size_bytes"]), ("checkpoints", self.CHECKPOINT_BYTES))
        with self.assertRaisesRegex(mi.ImportError_, "limit"):
            self.plan("LORA", self.CHECKPOINT_BYTES)

    def test_a_13_gb_checkpoint_is_refused(self):
        with self.assertRaisesRegex(mi.ImportError_, "limit"):
            self.plan("Checkpoint", 13 * 1024 ** 3)

    def test_an_explicit_max_bytes_still_wins(self):
        with self.assertRaisesRegex(mi.ImportError_, "limit"):
            self.plan("Checkpoint", 5000, max_bytes=1024)

    def test_a_type_override_picks_the_cap(self):
        p = self.plan("LORA", self.CHECKPOINT_BYTES, type_override="checkpoint")
        self.assertEqual(p["folder"], "checkpoints")

    def test_hugging_face_uses_the_type_you_gave(self):
        tree = [{"path": "m.safetensors", "size": self.CHECKPOINT_BYTES}]
        link = "https://huggingface.co/o/r/blob/main/m.safetensors"
        fetch = lambda u, h: json.dumps(tree).encode()  # noqa: E731
        p = mi.fetch_plan(link, fetch, models_dir=self.models, type_override="checkpoint")
        self.assertEqual(p["folder"], "checkpoints")
        with self.assertRaisesRegex(mi.ImportError_, "limit"):
            mi.fetch_plan(link, fetch, models_dir=self.models, type_override="lora")
        with self.assertRaisesRegex(mi.ImportError_, "limit"):
            mi.fetch_plan(link, lambda u, h: json.dumps([{"path": "m.safetensors", "size": 13 * 1024 ** 3}]).encode(), models_dir=self.models, type_override="checkpoint")

    def run_import(self, model_type, seen, free_bytes=None, size_bytes=None):
        """execute_plan on a small real file whose plan CLAIMS size_bytes; the downloader records the cap it is given."""
        data = safetensors_bytes()
        plan = self.plan(model_type, size_bytes or self.CHECKPOINT_BYTES)

        def dl(url, dest, headers, max_bytes, progress):
            seen.append(max_bytes)
            with open(dest, "wb") as fh:
                fh.write(data)
            return len(data)
        reg, lic = os.path.join(self.tmp.name, "r.json"), os.path.join(self.tmp.name, "l.json")
        if free_bytes is None:
            return mi.execute_plan(plan, self.models, dl, registry_path=reg, licenses_path=lic)
        with mock.patch.object(mi.shutil, "disk_usage", return_value=mock.Mock(free=free_bytes)):
            return mi.execute_plan(plan, self.models, dl, registry_path=reg, licenses_path=lic)

    def test_the_downloader_is_given_the_checkpoint_cap(self):
        seen = []
        self.run_import("Checkpoint", seen, free_bytes=10 * 1024 ** 3 + self.CHECKPOINT_BYTES)
        self.assertEqual(seen, [12 * 1024 ** 3])

    def test_the_downloader_is_given_the_lora_cap_for_a_lora(self):
        seen = []
        self.run_import("LORA", seen, free_bytes=10 * 1024 ** 3, size_bytes=1000)
        self.assertEqual(seen, [6 * 1024 ** 3])

    def test_execute_plan_refuses_a_13_gb_checkpoint_plan_before_downloading(self):
        plan = self.plan("Checkpoint", 5000)
        plan["file"]["size_bytes"] = 13 * 1024 ** 3
        seen = []
        with self.assertRaisesRegex(mi.ImportError_, "limit"):
            mi.execute_plan(plan, self.models, lambda *a: seen.append(a), registry_path=os.path.join(self.tmp.name, "r.json"), licenses_path=os.path.join(self.tmp.name, "l.json"))
        self.assertEqual(seen, [])

    def test_too_little_free_space_refuses_and_leaves_nothing(self):
        seen = []
        with self.assertRaisesRegex(mi.ImportError_, "free disk space"):
            self.run_import("Checkpoint", seen, free_bytes=self.CHECKPOINT_BYTES + 2 * 1024 ** 3 - 1)
        self.assertEqual(seen, [])
        self.assertFalse(os.path.exists(os.path.join(self.models, ".quarantine")))
        self.assertFalse(os.path.exists(os.path.join(self.models, "checkpoints")))

    def test_exactly_enough_free_space_is_enough(self):
        seen = []
        res = self.run_import("Checkpoint", seen, free_bytes=self.CHECKPOINT_BYTES + 2 * 1024 ** 3)
        self.assertTrue(os.path.isfile(res["destination"]))


class KeyTest(unittest.TestCase):
    def test_the_key_is_read_from_the_config_file_and_never_leaks(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "civitai.json")
            self.assertIsNone(mi.load_key(p))
            write(p, json.dumps({"api_key": " SECRETKEY "}))
            self.assertEqual(mi.load_key(p), "SECRETKEY")
            for junk in ("not json", "[]", json.dumps({"api_key": ""}), json.dumps({"api_key": 5})):
                write(p, junk)
                self.assertIsNone(mi.load_key(p))

    def test_the_key_never_appears_in_errors_or_output(self):
        def leaky(url, headers):
            raise OSError("failed with header %s" % headers.get("Authorization"))
        with self.assertRaises(mi.ImportError_) as cm:
            mi.fetch_plan("https://civitai.com/models/1", leaky, key="SECRETKEY", models_dir="/x")
        self.assertNotIn("SECRETKEY", str(cm.exception))
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            code = mi.main(["https://civitai.com/models/1"], fetcher=leaky, out=out, key="SECRETKEY")
        self.assertEqual(code, 1)
        self.assertNotIn("SECRETKEY", out.getvalue() + err.getvalue())

    def test_a_redirect_to_another_host_drops_the_authorization_header(self):
        h = mi._NoAuthOnRedirect()
        req = urllib.request.Request("https://civitai.com/api/download/models/1", headers={"Authorization": "Bearer SECRETKEY"})
        same = h.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://civitai.com/other")
        self.assertEqual(same.get_header("Authorization"), "Bearer SECRETKEY")
        other = h.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://cdn.example.net/file")
        self.assertIsNone(other.get_header("Authorization"))
        self.assertNotIn("authorization", {k.lower() for k in other.header_items() and dict(other.header_items())})


class CliTest(unittest.TestCase):
    def test_a_dry_run_prints_the_plan_and_downloads_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            out = io.StringIO()
            dl = mock.Mock()
            code = mi.main(["https://civitai.com/models/1564160", "--models-dir", d], fetcher=fetcher_for(SAMPLE_1564160), downloader=dl, out=out, key=None)
            self.assertEqual(code, 0)
            dl.assert_not_called()
            text = out.getvalue()
            for needle in ("ShortStack-Concept-Ilustrious", "base Illustrious", "ShortStack, Large_Hips", "conditional", "dry run"):
                self.assertIn(needle, text)
            self.assertEqual(os.listdir(d), [])

    def test_yes_installs(self):
        with tempfile.TemporaryDirectory() as d:
            data = safetensors_bytes()
            api = json.loads(json.dumps(SAMPLE_1564160))
            api["modelVersions"][0]["files"][0].update(hashes={"SHA256": hashlib.sha256(data).hexdigest()}, sizeKB=1)
            out = io.StringIO()
            with mock.patch.object(mi, "REGISTRY_PATH", os.path.join(d, "r.json")), mock.patch.object(mi, "LICENSES_PATH", os.path.join(d, "l.json")):
                code = mi.main(["https://civitai.com/models/1564160", "--models-dir", d, "--yes"], fetcher=fetcher_for(api), downloader=downloader_for(data), out=out, key=None)
            self.assertEqual(code, 0)
            self.assertTrue(os.path.isfile(os.path.join(d, "loras", "ShortStack-Concept-Ilustrious.safetensors")))
            self.assertIn("installed", out.getvalue())

    def test_errors_exit_1(self):
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.assertEqual(mi.main(["https://example.com/x"], out=io.StringIO(), key=None), 1)
        self.assertIn("error", err.getvalue())


if __name__ == "__main__":
    unittest.main()
