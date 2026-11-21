"""Guard test: web/app.js must keep showing the ComfyUI start failure reason and log tail, and label a pipeline GPU guard stop.
Static file read only: no GPU, network, timers or models involved.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import unittest
from pathlib import Path

APP_JS = Path(__file__).resolve().parent.parent / "web" / "app.js"
START_ROUTE = "/api/comfy/start"


class ComfyStartToastTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = APP_JS.read_text(encoding="utf-8")

    def start_region(self):
        idx = self.text.find(START_ROUTE)
        self.assertNotEqual(idx, -1, "web/app.js no longer references %r" % START_ROUTE)
        return self.text[idx:]

    def test_start_handler_shows_the_failure_reason(self):
        self.assertIn("reason", self.start_region(),
                      "'reason' missing after %r in web/app.js: the start-failure reason toast was removed" % START_ROUTE)

    def test_start_handler_shows_the_log_tail(self):
        self.assertIn("log_tail", self.start_region(),
                      "'log_tail' missing after %r in web/app.js: the start-failure log-tail toast was removed" % START_ROUTE)

    def test_guard_stop_is_labelled_in_the_ui(self):
        self.assertIn("pipeline GPU guard", self.text,
                      "'pipeline GPU guard' missing from web/app.js: the guard-stop message was removed")


if __name__ == "__main__":
    unittest.main()
