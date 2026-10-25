"""Docs/GPU_FAMILY_PROTOCOL.md is a pointer to the broker repo's copy (G7b): it must say so, stay short, and describe only what PromptForge itself still
does, with the client variables it names really read by gpu_client.py. Run: python -m unittest discover -s tests -v"""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class PointerTest(unittest.TestCase):
    def test_the_doc_points_at_the_broker_repo_and_stays_short(self):
        doc = read("Docs", "GPU_FAMILY_PROTOCOL.md")
        self.assertIn("agent-manager-gpu-broker", doc)
        self.assertIn("Docs/GPU_FAMILY_PROTOCOL.md", doc)
        self.assertLess(len(doc.splitlines()), 40, "the protocol itself lives in the broker repo; keep this a pointer")

    def test_client_variables_the_pointer_names_are_read_by_the_client(self):
        doc, client = read("Docs", "GPU_FAMILY_PROTOCOL.md"), read("server", "core", "gpu_client.py")
        names = set(re.findall(r"`(FORGE_BROKER_[A-Z_]+)`", doc))
        self.assertEqual(names, {"FORGE_BROKER_STEP_POLL_S", "FORGE_BROKER_HEARTBEAT_S", "FORGE_BROKER_ACQUIRE_TIMEOUT_S"})
        self.assertEqual([n for n in sorted(names) if n not in client], [])

    def test_code_and_readme_no_longer_claim_this_file_is_the_contract(self):
        for path in (("server", "app.py"), ("server", "core", "gpu_client.py")):
            self.assertIn("agent-manager-gpu-broker Docs/GPU_FAMILY_PROTOCOL.md", read(*path), path)
        self.assertIn("is a pointer", read("README.md"))


if __name__ == "__main__":
    unittest.main()
