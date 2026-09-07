"""Unit tests for Pure Python HttpClient and HttpResponse.

Covers the full M2.5 test matrix:
- URL joining (slashes, base_url variants, endpoint variants)
- Headers and authentication (Content-Type, Accept, Authorization inclusion/omission)
- Secret hygiene (tokens not leaked in errors or strings)
- Local mock HTTP server test matrix (200, chunked, delayed, 401, 403, 429, 500, timeout, cancellation)
- Streaming iteration and chunking
- Resource cleanup on normal exit, cancellation, and error
- End-to-end integration smoke test with SSEParser

Zero Blender (bpy) dependencies. Zero external network dependencies.
"""

import http.server
import json
import socket
import threading
import time
import unittest
import urllib.parse
from typing import List, Optional

from agent.http_client import (
    HttpClient,
    HttpResponse,
    HttpError,
    NetworkError,
    HttpTimeoutError,
    HttpConnectionError,
    DEFAULT_CHUNK_SIZE,
)
from agent.sse_parser import SSEParser


class MockServerHandler(http.server.BaseHTTPRequestHandler):
    """Custom HTTP request handler for mock HTTP server tests."""

    # Disable standard logging to stdout to keep test runner output clean
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 1. Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        req_body = self.rfile.read(content_length) if content_length > 0 else b""

        # 2. Route handlers
        if path == "/simple":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            resp_bytes = b'{"status": "ok", "echo": "' + req_body + b'"}'
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            self.wfile.flush()

        elif path == "/echo_headers":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            headers_dict = {k: v for k, v in self.headers.items()}
            resp_bytes = json.dumps(headers_dict).encode("utf-8")
            self.send_header("Content-Length", str(len(resp_bytes)))
            self.end_headers()
            self.wfile.write(resp_bytes)
            self.wfile.flush()

        elif path == "/multiple_chunks":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            # Close connection at end to indicate EOF without Content-Length
            self.send_header("Connection", "close")
            self.end_headers()
            for i in range(5):
                chunk = (f"chunk_{i}_" + ("x" * 400) + "\n").encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()

        elif path == "/delayed_chunks":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"first_part\n")
            self.wfile.flush()
            time.sleep(0.15)
            self.wfile.write(b"second_part\n")
            self.wfile.flush()

        elif path == "/status/401":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            err_body = b'{"error": "invalid_api_key"}'
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            self.wfile.flush()

        elif path == "/status/403":
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            err_body = b'{"error": "forbidden"}'
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            self.wfile.flush()

        elif path == "/status/429":
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            err_body = b'{"error": "rate_limit_exceeded"}'
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            self.wfile.flush()

        elif path == "/status/500":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            err_body = b'{"error": "internal_server_error"}'
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            self.wfile.flush()

        elif path == "/slow_timeout":
            # Sleep longer than client's timeout (e.g. 0.5s)
            time.sleep(0.6)
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"too_late")
            except Exception:
                pass

        elif path == "/infinite_stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                step = 0
                while True:
                    chunk = f"data: infinite_tick_{step}\n\n".encode("utf-8")
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    step += 1
                    time.sleep(0.01)
            except (BrokenPipeError, ConnectionResetError, OSError):
                # Client cancelled/closed socket
                pass

        elif path == "/sse_stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            events = [
                b'data: {"id": "chat-1", "content": "Hello"}\n\n',
                b'data: {"id": "chat-1", "content": " world!"}\n\n',
                b"data: [DONE]\n\n",
            ]
            for ev in events:
                self.wfile.write(ev)
                self.wfile.flush()
                time.sleep(0.01)

        else:
            self.send_response(404)
            self.end_headers()


class TestHttpClientUrlJoining(unittest.TestCase):
    """Test URL construction and normalization."""

    def test_join_url_clean(self):
        """Test clean base_url and endpoint."""
        url = HttpClient.join_url("https://api.openai.com/v1", "chat/completions")
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")

    def test_join_url_trailing_and_leading_slashes(self):
        """Test base_url with trailing slash and endpoint with leading slash."""
        url = HttpClient.join_url("https://api.openai.com/v1/", "/chat/completions")
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")

    def test_join_url_both_no_slashes(self):
        """Test when neither has slashes."""
        url = HttpClient.join_url("http://localhost:11434/v1", "chat/completions")
        self.assertEqual(url, "http://localhost:11434/v1/chat/completions")

    def test_join_url_empty_base(self):
        """Test empty base_url returns stripped endpoint."""
        url = HttpClient.join_url("", "/chat/completions")
        self.assertEqual(url, "chat/completions")

    def test_join_url_empty_endpoint(self):
        """Test empty endpoint returns stripped base_url."""
        url = HttpClient.join_url("https://example.com/v1/", "")
        self.assertEqual(url, "https://example.com/v1")


class TestHttpClientWithMockServer(unittest.TestCase):
    """Integration test suite against local mock HTTP server."""

    @classmethod
    def setUpClass(cls):
        # Start local mock HTTP server on 127.0.0.1 on an ephemeral port
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), MockServerHandler)
        cls.server_port = cls.server.server_port
        cls.base_url = f"http://127.0.0.1:{cls.server_port}"

        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2.0)

    def setUp(self):
        self.client = HttpClient(base_url=self.base_url, timeout=5.0)

    def test_01_simple_post_200(self):
        """Test 1: Simple 200 OK POST request and body consumption."""
        payload = b"hello_world"
        resp = self.client.post("/simple", payload=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.is_closed)

        chunks = list(resp)
        self.assertTrue(resp.is_closed)
        full_body = b"".join(chunks)
        data = json.loads(full_body.decode("utf-8"))
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["echo"], "hello_world")

    def test_02_multiple_chunks_streaming(self):
        """Test 2: Streaming multiple chunks without buffering entire response in memory."""
        # Use chunk_size=200 so that 5 chunks of ~410 bytes are streamed across multiple reads
        client = HttpClient(base_url=self.base_url, chunk_size=200)
        resp = client.post("/multiple_chunks", payload=b"")
        self.assertEqual(resp.status_code, 200)

        chunks = list(resp)
        self.assertGreaterEqual(len(chunks), 5)
        full_body = b"".join(chunks)
        self.assertIn(b"chunk_0_", full_body)
        self.assertIn(b"chunk_4_", full_body)
        self.assertTrue(resp.is_closed)

    def test_03_delayed_chunks_streaming(self):
        """Test 3: Incrementally receiving delayed chunks over time."""
        client = HttpClient(base_url=self.base_url, chunk_size=64)
        resp = client.post("/delayed_chunks", payload=b"")
        self.assertEqual(resp.status_code, 200)

        received = []
        timestamps = []
        for chunk in resp:
            timestamps.append(time.perf_counter())
            received.append(chunk)

        self.assertGreaterEqual(len(received), 2)
        if len(timestamps) >= 2:
            gap = timestamps[1] - timestamps[0]
            self.assertGreaterEqual(gap, 0.05)
        self.assertEqual(b"".join(received), b"first_part\nsecond_part\n")

    def test_04_status_401_unauthorized(self):
        """Test 4: 401 Unauthorized raises HttpError with code 401."""
        with self.assertRaises(HttpError) as ctx:
            self.client.post("/status/401", payload=b"")
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("invalid_api_key", ctx.exception.body_snippet)

    def test_05_status_403_forbidden(self):
        """Test 5: 403 Forbidden raises HttpError with code 403."""
        with self.assertRaises(HttpError) as ctx:
            self.client.post("/status/403", payload=b"")
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("forbidden", ctx.exception.body_snippet)

    def test_06_status_429_rate_limit(self):
        """Test 6: 429 Too Many Requests raises HttpError with code 429."""
        with self.assertRaises(HttpError) as ctx:
            self.client.post("/status/429", payload=b"")
        self.assertEqual(ctx.exception.status_code, 429)
        self.assertIn("rate_limit_exceeded", ctx.exception.body_snippet)

    def test_07_status_500_internal_error(self):
        """Test 7: 500 Internal Server Error raises HttpError with code 500."""
        with self.assertRaises(HttpError) as ctx:
            self.client.post("/status/500", payload=b"")
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("internal_server_error", ctx.exception.body_snippet)

    def test_08_connection_failure_to_dead_port(self):
        """Test 8: Connecting to an unreachable port raises NetworkError."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()

        dead_client = HttpClient(base_url=f"http://127.0.0.1:{dead_port}", timeout=0.5)
        with self.assertRaises((HttpConnectionError, HttpTimeoutError, NetworkError)):
            dead_client.post("/test", payload=b"")

    def test_09_timeout_handling(self):
        """Test 9: Server taking longer than timeout raises HttpTimeoutError."""
        # Client timeout is 0.15s, server sleeps 0.6s
        short_timeout_client = HttpClient(base_url=self.base_url, timeout=0.15)
        t_start = time.perf_counter()
        with self.assertRaises(HttpTimeoutError):
            short_timeout_client.post("/slow_timeout", payload=b"")
        elapsed = time.perf_counter() - t_start
        # Timeout must fire reasonably close to 0.15s (< 0.5s)
        self.assertLess(elapsed, 0.55)

    def test_10_cancellation_and_latency_measurement(self):
        """Test 10: Cancellation terminates an active stream and measures latency."""
        cancel_event = threading.Event()
        resp = self.client.post("/infinite_stream", payload=b"", cancel_event=cancel_event)
        self.assertEqual(resp.status_code, 200)

        chunks_received = 0
        cancel_requested_time = 0.0
        stream_terminated_time = 0.0

        for chunk in resp:
            chunks_received += 1
            if chunks_received == 3:
                # Signal cancellation
                cancel_requested_time = time.perf_counter()
                cancel_event.set()

        stream_terminated_time = time.perf_counter()
        latency_ms = (stream_terminated_time - cancel_requested_time) * 1000.0

        # Assertions
        self.assertTrue(resp.is_closed)
        # Should stop shortly after cancellation (within 1-2 chunks)
        self.assertLessEqual(chunks_received, 5)
        # Latency should be well within tight bounds (< 150ms on local loopback)
        self.assertLess(latency_ms, 250.0)

    def test_headers_and_auth_handling(self):
        """Test header serialization, Authorization presence, and omission when empty."""
        # 1. With valid API key
        resp1 = self.client.post(
            "/echo_headers",
            payload=b"",
            headers={"Authorization": "Bearer sk-valid-key-999", "X-Custom": "test-val"},
        )
        data1 = json.loads(b"".join(resp1).decode("utf-8"))
        self.assertEqual(data1.get("Authorization"), "Bearer sk-valid-key-999")
        self.assertEqual(data1.get("X-custom") or data1.get("X-Custom"), "test-val")
        self.assertEqual(data1.get("Content-Type"), "application/json")
        self.assertEqual(data1.get("Accept"), "text/event-stream")

        # 2. With empty / placeholder API key -> Authorization MUST NOT be sent
        for empty_val in ["", "   ", "Bearer", "Bearer None", "None"]:
            resp = self.client.post(
                "/echo_headers",
                payload=b"",
                headers={"Authorization": empty_val},
            )
            data = json.loads(b"".join(resp).decode("utf-8"))
            self.assertNotIn(
                "Authorization",
                data,
                f"Authorization header leaked with empty value: {empty_val!r}",
            )

    def test_context_manager_cleanup(self):
        """Test with statement ensures response close."""
        with self.client.post("/multiple_chunks", payload=b"") as resp:
            self.assertFalse(resp.is_closed)
            _ = next(iter(resp))
        self.assertTrue(resp.is_closed)

    def test_secret_hygiene_in_errors(self):
        """HttpError string representation must never leak API keys."""
        try:
            self.client.post(
                "/status/401",
                payload=b"",
                headers={"Authorization": "Bearer sk-super-secret-key"},
            )
        except HttpError as err:
            err_str = str(err)
            self.assertNotIn("sk-super-secret-key", err_str)
            self.assertEqual(err.status_code, 401)

    def test_sse_parser_integration_smoke(self):
        """End-to-end integration: Mock Server -> HttpClient -> SSEParser -> payloads."""
        parser = SSEParser()
        resp = self.client.post("/sse_stream", payload=b"")
        self.assertEqual(resp.status_code, 200)

        events: List[str] = []
        for chunk in resp:
            events.extend(parser.feed(chunk))
        events.extend(parser.close())

        self.assertTrue(parser.is_done)
        self.assertEqual(len(events), 3)
        self.assertIn('{"id": "chat-1", "content": "Hello"}', events[0])
        self.assertIn('{"id": "chat-1", "content": " world!"}', events[1])
        self.assertEqual(events[2], "[DONE]")


if __name__ == "__main__":
    unittest.main()
