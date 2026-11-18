"""Card-scoped Forge lease (GPU broker piece G4a): the lease PromptForge writes names the Ollama endpoint(s) it holds, so only the pipeline
lanes on those cards yield. Agent Manager reads `endpoints` in src/gpu-guard.js (leaseHeldFor); absent or empty means every lane yields.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
from core import comfy_proc as cp  # noqa: E402
from core import gpu_session as gs  # noqa: E402
from test_gpu_session import Base  # noqa: E402

P40 = "http://192.168.122.29:11434"
RX580 = "http://100.64.0.2:11434"


class LeaseEndpointsTest(Base):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("FORGE_LEASE_ENDPOINTS", None)

    def lease_data(self):
        with open(self.lease) as f:
            return json.load(f)

    def test_default_scope_is_the_ollama_that_begin_evicts_from(self):
        self.assertEqual(cp.lease_endpoints(), [cp.OLLAMA_URL])
        gs.write_lease("promptforge")
        self.assertEqual(self.lease_data()["endpoints"], [cp.OLLAMA_URL])

    def test_env_lists_endpoints_trimmed_and_without_empties(self):
        os.environ["FORGE_LEASE_ENDPOINTS"] = f" {P40} , ,{RX580},"
        self.assertEqual(cp.lease_endpoints(), [P40, RX580])
        gs.write_lease("promptforge")
        self.assertEqual(self.lease_data()["endpoints"], [P40, RX580])

    def test_all_means_unscoped_and_the_key_is_omitted(self):
        for word in ("all", " ALL "):
            os.environ["FORGE_LEASE_ENDPOINTS"] = word
            self.assertEqual(cp.lease_endpoints(), [])
            gs.write_lease("promptforge")
            self.assertNotIn("endpoints", self.lease_data())

    def test_a_list_with_no_usable_url_is_unscoped_never_an_empty_key(self):
        os.environ["FORGE_LEASE_ENDPOINTS"] = " , "
        gs.write_lease("promptforge")
        self.assertNotIn("endpoints", self.lease_data())

    def test_the_eviction_path_lease_has_the_same_endpoints(self):
        os.environ["FORGE_LEASE_ENDPOINTS"] = P40
        self.assertTrue(cp.lease_acquire("job-1"))
        d = self.lease_data()
        self.assertEqual((d["holder"], d["endpoints"]), ("promptforge", [P40]))

    def test_refresh_keeps_the_endpoints(self):
        os.environ["FORGE_LEASE_ENDPOINTS"] = P40
        cp.lease_acquire("job-1")
        self.assertTrue(cp.lease_refresh("job-2"))
        self.assertEqual(self.lease_data()["endpoints"], [P40])

    def test_begin_and_every_keeper_tick_write_the_scoped_lease(self):
        os.environ["FORGE_LEASE_ENDPOINTS"] = RX580
        self.begin()
        self.assertEqual(self.lease_data()["endpoints"], [RX580])
        with open(self.lease, "w") as f:                       # same holder, stale shape: the keeper must restore the scope
            json.dump({"holder": "promptforge", "family": "forge", "owner": "x", "acquiredAt": "2026-01-01T00:00:00Z", "refreshedAt": "2026-01-01T00:00:00Z"}, f)
        self.assertEqual(gs.keeper_tick(), "refreshed")
        self.assertEqual(self.lease_data()["endpoints"], [RX580])


if __name__ == "__main__":
    unittest.main()
