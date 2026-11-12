"""API tests for app.py on a temporary database (no network, no real caller)."""
import json
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import app
import calls
import db

LISTING = {"source": "s", "id": "1", "url": "http://x", "title": "Dom", "price": 1, "phone": "+48123456789"}


class AppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "t.db"
        patch = mock.patch.object(app, "DB_PATH", path)
        patch.start()
        self.addCleanup(patch.stop)
        with db.connect(path) as c:
            c.execute("""INSERT INTO listings (source, id, url, title, phone, data, first_seen, last_seen)
                         VALUES ('s', '1', 'http://x', 'Dom', '+48123456789', '{}', 'a', 'a')""")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}{path}", data=data, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_mark_list_unmark(self):
        self.assertEqual(self.req("POST", "/api/calls", {"source": "s", "id": "1", "questions": ["Q?"]})[0], 200)
        status, body = self.req("GET", "/api/calls")
        self.assertEqual(body["calls"][0]["questions"], ["Q?"])
        self.assertEqual(body["default_questions"], calls.DEFAULT_QUESTIONS)
        self.assertFalse(body["running"])
        self.assertEqual(self.req("GET", "/api/listings")[1][0]["call_status"], "to_call")
        self.assertEqual(self.req("DELETE", "/api/calls?source=s&id=1")[0], 200)
        self.assertEqual(self.req("GET", "/api/calls")[1]["calls"], [])
        self.assertIsNone(self.req("GET", "/api/listings")[1][0]["call_status"])

    def test_default_questions_and_errors(self):
        self.req("POST", "/api/calls", {"source": "s", "id": "1"})
        self.assertEqual(self.req("GET", "/api/calls")[1]["calls"][0]["questions"], calls.DEFAULT_QUESTIONS)
        self.assertEqual(self.req("POST", "/api/calls", {"source": "s", "id": "nope"})[0], 404)
        self.assertEqual(self.req("POST", "/api/calls", {"source": "s"})[0], 400)
        self.assertEqual(self.req("POST", "/api/calls", {"source": "s", "id": "1", "questions": " "})[0], 400)
        self.assertEqual(self.req("DELETE", "/api/calls")[0], 400)

    def test_mode(self):
        with mock.patch.dict("os.environ", {"CALL_MODE": "real"}):
            self.assertEqual(self.req("GET", "/api/calls")[1]["mode"], "real")
        with mock.patch.dict("os.environ", {"CALL_MODE": "x"}):
            self.assertEqual(self.req("GET", "/api/calls")[1]["mode"], "simulate")

    def test_run_single_flight_and_survives_errors(self):
        release = threading.Event()
        fake = types.SimpleNamespace(run_pending=release.wait)
        with mock.patch.dict(sys.modules, {"caller": fake}):
            self.assertEqual(self.req("POST", "/api/calls/run")[0], 202)
            self.assertTrue(self.req("GET", "/api/calls")[1]["running"])
            self.assertEqual(self.req("POST", "/api/calls/run")[0], 409)
            release.set()
            app.run_thread.join(5)
        self.assertFalse(self.req("GET", "/api/calls")[1]["running"])

        def boom():
            raise RuntimeError("boom")
        with mock.patch.dict(sys.modules, {"caller": types.SimpleNamespace(run_pending=boom)}):
            self.assertEqual(self.req("POST", "/api/calls/run")[0], 202)
            app.run_thread.join(5)
        self.assertEqual(self.req("GET", "/api/calls")[0], 200)


if __name__ == "__main__":
    unittest.main()
