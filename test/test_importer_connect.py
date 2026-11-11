"""IPv4-first connections and the metadata deadline of the model importer (no network: sockets and the resolver are faked)."""
import os
import sys
import socket
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
from core import model_importer as mi  # noqa: E402

V6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606::1", 443, 0, 0))
V4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))


class FakeSock:
    log = []

    def __init__(self, family, kind, proto):
        self.family = family
        self.timeouts = []
        self.closed = False

    def settimeout(self, t):
        self.timeouts.append(t)

    def connect(self, addr):
        FakeSock.log.append(addr[0])
        if self.family == socket.AF_INET6:
            raise socket.timeout("timed out")

    def close(self):
        self.closed = True


class ConnectTest(unittest.TestCase):
    def setUp(self):
        FakeSock.log = []

    def test_ipv4_tried_before_ipv6_and_succeeds(self):
        with mock.patch.object(socket, "getaddrinfo", return_value=[V6, V4]), mock.patch.object(socket, "socket", FakeSock):
            s = mi._connect_ipv4_first("civitai.com", 443, 30)
        self.assertEqual(FakeSock.log, ["1.2.3.4"])
        self.assertEqual(s.family, socket.AF_INET)
        self.assertEqual(s.timeouts[-1], 30)

    def test_dead_ipv6_only_costs_the_short_timeout(self):
        made = []

        class Rec(FakeSock):
            def __init__(self, *a):
                super().__init__(*a)
                made.append(self)

            def connect(self, addr):
                FakeSock.log.append(addr[0])
                raise socket.timeout("timed out")

        with mock.patch.object(socket, "getaddrinfo", return_value=[V6, V4]), mock.patch.object(socket, "socket", Rec):
            with self.assertRaises(OSError):
                mi._connect_ipv4_first("civitai.com", 443, 60)
        self.assertEqual(FakeSock.log, ["1.2.3.4", "2606::1"])
        self.assertTrue(all(m.timeouts[0] == mi.CONNECT_TIMEOUT for m in made))
        self.assertTrue(all(m.closed for m in made))

    def test_short_overall_timeout_caps_the_per_address_timeout(self):
        made = []

        class Rec(FakeSock):
            def __init__(self, *a):
                super().__init__(*a)
                made.append(self)

        with mock.patch.object(socket, "getaddrinfo", return_value=[V4]), mock.patch.object(socket, "socket", Rec):
            mi._connect_ipv4_first("h", 443, 1.5)
        self.assertEqual(made[0].timeouts[0], 1.5)

    def test_no_address_is_an_oserror(self):
        with mock.patch.object(socket, "getaddrinfo", return_value=[]):
            with self.assertRaises(OSError):
                mi._connect_ipv4_first("h", 443, 5)

    def test_opener_uses_the_ipv4_first_handler(self):
        names = [type(h).__name__ for h in mi._opener().handlers]
        self.assertIn("_Ipv4FirstHTTPSHandler", names)
        self.assertIn("_NoAuthOnRedirect", names)
        self.assertNotIn("HTTPSHandler", names)


class DeadlineTest(unittest.TestCase):
    def test_hanging_fetch_becomes_importer_error(self):
        def hang(url, headers, timeout):
            time.sleep(5)
            return b"x"

        t0 = time.time()
        with mock.patch.object(mi, "_read_all", hang):
            with self.assertRaises(mi.ImportError_) as cm:
                mi.default_fetcher("https://civitai.com/x", {}, deadline=0.2)
        self.assertLess(time.time() - t0, 2)
        self.assertIn("did not answer in 0.2 s", str(cm.exception))

    def test_answer_and_error_pass_through(self):
        with mock.patch.object(mi, "_read_all", lambda u, h, t: b"body"):
            self.assertEqual(mi.default_fetcher("https://civitai.com/x"), b"body")
        def boom(u, h, t):
            raise OSError("refused")
        with mock.patch.object(mi, "_read_all", boom):
            with self.assertRaises(OSError):
                mi.default_fetcher("https://civitai.com/x")

    def test_default_deadline_is_twenty_seconds(self):
        seen = []
        with mock.patch.object(mi, "_read_all", lambda u, h, t: seen.append(t) or b""):
            mi.default_fetcher("https://civitai.com/x")
        self.assertEqual(seen, [20.0])


if __name__ == "__main__":
    unittest.main()
