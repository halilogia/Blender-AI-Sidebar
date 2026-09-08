"""Unit tests for core/web_server.py LocalWebServer and SSE event bridge."""

import json
import os
import sys
import tempfile
import time
import unittest
import urllib.request
import urllib.error

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.web_server import LocalWebServer


class TestLocalWebServer(unittest.TestCase):
    """Test suite for local Web UI HTTP server, SSE streaming, and security token auth."""

    def setUp(self):
        # Create temp static dir with test index.html
        self.temp_dir = tempfile.TemporaryDirectory()
        self.index_file = os.path.join(self.temp_dir.name, "index.html")
        with open(self.index_file, "w", encoding="utf-8") as f:
            f.write("<!DOCTYPE html><html><body><h1>Hello Test AI</h1></body></html>")

        self.server = LocalWebServer(static_dir=self.temp_dir.name, host="127.0.0.1", port=0)
        self.server.start()
        time.sleep(0.05)

    def tearDown(self):
        self.server.stop()
        self.temp_dir.cleanup()

    def test_startup_and_token_generation(self):
        """Test server starts on 127.0.0.1 and generates secure 32-char hex session token."""
        self.assertTrue(self.server.is_running)
        self.assertTrue(self.server.base_url.startswith("http://127.0.0.1:"))
        self.assertEqual(len(self.server.session_token), 32)
        self.assertIn("token=", self.server.app_url)

    def test_unauthorized_access_rejected(self):
        """Requests without session token must be rejected with 403 Forbidden."""
        # 1. Root index.html without token
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{self.server.base_url}/")
        self.assertEqual(ctx.exception.code, 403)

        # 2. Status API without token
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{self.server.base_url}/api/status")
        self.assertEqual(ctx.exception.code, 403)

        # 3. Prompt API without token
        req = urllib.request.Request(
            f"{self.server.base_url}/api/prompt",
            data=b'{"prompt": "test"}',
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 403)

    def test_static_file_serving_with_token(self):
        """Valid token in query params serves index.html successfully."""
        url = f"{self.server.base_url}/?token={self.server.session_token}"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get_content_type(), "text/html")
            content = resp.read().decode("utf-8")
            self.assertIn("Hello Test AI", content)

    def test_prompt_post_callback(self):
        """POST /api/prompt delivers prompt to registered callback."""
        received = []
        self.server.set_prompt_callback(lambda p: received.append(p))

        req = urllib.request.Request(
            f"{self.server.base_url}/api/prompt?token={self.server.session_token}",
            data=json.dumps({"prompt": "Sahneyi incele."}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "ok")

        self.assertEqual(received, ["Sahneyi incele."])

    def test_cancel_post_callback(self):
        """POST /api/cancel triggers cancel callback."""
        cancelled = []
        self.server.set_cancel_callback(lambda: cancelled.append(True))

        req = urllib.request.Request(
            f"{self.server.base_url}/api/cancel?token={self.server.session_token}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "ok")

        self.assertEqual(cancelled, [True])

    def test_status_api_provider(self):
        """GET /api/status returns live status provided by status callback."""
        self.server.set_status_provider(lambda: {"agent_status": "IDLE", "model": "test-model"})

        url = f"{self.server.base_url}/api/status?token={self.server.session_token}"
        with urllib.request.urlopen(url) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "online")
            self.assertEqual(data["agent_status"], "IDLE")
            self.assertEqual(data["model"], "test-model")

    def test_bearer_authorization_header(self):
        """Authorization: Bearer <token> is accepted as valid authentication."""
        req = urllib.request.Request(
            f"{self.server.base_url}/api/status",
            headers={"Authorization": f"Bearer {self.server.session_token}"},
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(data["status"], "online")


if __name__ == "__main__":
    unittest.main()
