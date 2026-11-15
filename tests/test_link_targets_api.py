'''Stub-server tests for link_targets.assign failure paths (stdlib only; run with python -I).'''
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('link_targets', ROOT / 'server' / 'core' / 'link_targets.py')
lt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lt)

DESCRIPTOR = {'forge': 'characterforge', 'label': 'CharacterForge', 'entries_path': '/api/entries',
              'images_path': '/api/characters/{slug}/images', 'roles': ['face'], 'verdicts': ['good', 'needs-work']}


class StubTarget:
    '''A tiny HTTP stand-in for CharacterForge: scripted POST replies per slug, every request recorded.'''

    def __init__(self):
        self.replies = {}
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                raw = json.dumps(body).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                stub.requests.append(('GET', self.path))
                if self.path == '/api/link-target':
                    self._send(200, DESCRIPTOR)
                else:
                    self._send(404, {'error': 'not found'})

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length') or 0))
                stub.requests.append(('POST', self.path))
                slug = self.path.split('/')[3]
                status, body = stub.replies.get(slug, (201, {'id': 'img-1', 'file': 'ada.png'}))
                self._send(status, body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.base = 'http://127.0.0.1:%d' % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def posts(self):
        return [p for m, p in self.requests if m == 'POST']


class AssignFailurePaths(unittest.TestCase):
    def setUp(self):
        self.stub = StubTarget()
        self.stub.start()
        self.addCleanup(self.stub.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.image = os.path.join(self.dir, 'ada.png')
        with open(self.image, 'wb') as fh:
            fh.write(b'PNG fake bytes')

    def sidecar(self, links=None):
        path = os.path.join(self.dir, 'ada.png.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump({'name': 'Ada', 'links': links or []}, fh)
        return path

    def read(self, path):
        with open(path, 'rb') as fh:
            return fh.read()

    def assign(self, sidecar, slugs):
        body = {'tag': 't', 'note': 'n', 'verdict': 'good',
                'targets': [{'slug': s, 'role': 'face'} for s in slugs]}
        return lt.assign(self.stub.base, 'gallery/ada.png', self.image, sidecar, body)

    def test_success_records_link_in_sidecar(self):
        side = self.sidecar()
        [r] = self.assign(side, ['ada'])
        self.assertTrue(r['ok'])
        self.assertEqual(r['imageId'], 'img-1')
        links = lt.read_sidecar(side)['links']
        self.assertEqual([(l['slug'], l['role'], l['imageId']) for l in links], [('ada', 'face', 'img-1')])

    def test_409_role_full_passes_count_and_entries_through(self):
        side = self.sidecar()
        before = self.read(side)
        self.stub.replies['ada'] = (409, {'count': 10, 'entries': [{'slug': 'bob', 'name': 'Bob'}]})
        [r] = self.assign(side, ['ada'])
        self.assertFalse(r['ok'])
        self.assertEqual(r['status'], 409)
        self.assertEqual(r['count'], 10)
        self.assertEqual(r['entries'], [{'slug': 'bob', 'name': 'Bob'}])
        self.assertEqual(self.read(side), before)

    def test_non_2xx_failure_reports_status_and_error(self):
        side = self.sidecar()
        before = self.read(side)
        self.stub.replies['ada'] = (503, {'error': 'busy'})
        [r] = self.assign(side, ['ada'])
        self.assertFalse(r['ok'])
        self.assertEqual(r['status'], 503)
        self.assertEqual(r['error'], 'busy')
        self.assertEqual(self.read(side), before)

    def test_already_linked_target_is_not_resent(self):
        side = self.sidecar([{'forge': 'characterforge', 'slug': 'ada', 'role': 'face', 'imageId': 'img-9'}])
        before = self.read(side)
        [r] = self.assign(side, ['ada'])
        self.assertTrue(r['ok'])
        self.assertTrue(r['already'])
        self.assertEqual(r['imageId'], 'img-9')
        self.assertEqual(self.stub.posts(), [])
        self.assertEqual(self.read(side), before)

    def test_failed_targets_leave_sidecar_byte_identical(self):
        side = self.sidecar()
        before = self.read(side)
        self.stub.replies['ada'] = (500, {'error': 'boom'})
        self.stub.replies['bob'] = (409, {'count': 10, 'entries': []})
        results = self.assign(side, ['ada', 'bob'])
        self.assertEqual([r['status'] for r in results], [500, 409])
        self.assertEqual(self.read(side), before)


if __name__ == '__main__':
    unittest.main()
