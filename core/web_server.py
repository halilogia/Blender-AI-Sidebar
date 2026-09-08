"""Local Web Server and SSE Event Bridge for Blender AI Sidebar Embedded Web UI.

Pure Python. Zero external dependencies.
Binds strictly to 127.0.0.1 with session token validation.
"""

import http.server
import json
import os
import queue
import secrets
import socketserver
import threading
import time
import urllib.parse
from typing import Any, Callable, Dict, List, Optional


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded HTTP server allowing concurrent long-lived SSE connections and API calls."""
    daemon_threads = True
    allow_reuse_address = True


class LocalWebServer:
    """Manages local HTTP and SSE server lifecycle for Web UI bridge."""

    def __init__(
        self,
        static_dir: Optional[str] = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ):
        self.host = host
        self.port = port
        self.session_token = secrets.token_hex(16)
        self.static_dir = static_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"
        )
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._is_running = False

        # Active SSE client queues
        self._sse_clients_lock = threading.Lock()
        self._sse_clients: List[queue.Queue] = []

        # Callbacks registered by AgentRuntime / UI bridge
        self._prompt_callback: Optional[Callable[[str], None]] = None
        self._cancel_callback: Optional[Callable[[], None]] = None
        self._status_provider: Optional[Callable[[], Dict[str, Any]]] = None

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def base_url(self) -> str:
        if self._server:
            actual_port = self._server.server_address[1]
            return f"http://{self.host}:{actual_port}"
        return f"http://{self.host}:{self.port}"

    @property
    def app_url(self) -> str:
        return f"{self.base_url}/?token={self.session_token}"

    def set_prompt_callback(self, cb: Callable[[str], None]) -> None:
        self._prompt_callback = cb

    def set_cancel_callback(self, cb: Callable[[], None]) -> None:
        self._cancel_callback = cb

    def set_status_provider(self, provider: Callable[[], Dict[str, Any]]) -> None:
        self._status_provider = provider

    def start(self) -> None:
        """Start the local web server in a background daemon thread."""
        if self._is_running:
            return

        server_instance = self

        class BridgeHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                # Suppress noisy standard request logging
                pass

            def _validate_auth(self) -> bool:
                """Validate session token from URL query or Authorization header."""
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                token_param = params.get("token", [None])[0]
                auth_header = self.headers.get("Authorization", "")
                bearer_token = (
                    auth_header[7:].strip()
                    if auth_header.startswith("Bearer ")
                    else None
                )

                valid_token = server_instance.session_token
                return token_param == valid_token or bearer_token == valid_token

            def _send_json(self, status_code: int, data: Dict[str, Any]):
                payload = json.dumps(data).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                self.end_headers()
                self.wfile.write(payload)

            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
                self.end_headers()

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path

                # 1. API: Server-Sent Events stream (/api/events)
                if path == "/api/events":
                    if not self._validate_auth():
                        self.send_error(403, "Invalid or missing session token")
                        return

                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()

                    client_q: queue.Queue = queue.Queue(maxsize=100)
                    with server_instance._sse_clients_lock:
                        server_instance._sse_clients.append(client_q)

                    try:
                        # Initial handshake event
                        init_msg = (
                            f"data: {json.dumps({'event': 'connected', 'message': 'Hello Blender AI Bridge Online'})}\n\n"
                        )
                        self.wfile.write(init_msg.encode("utf-8"))
                        self.wfile.flush()

                        while server_instance._is_running:
                            try:
                                msg = client_q.get(timeout=1.0)
                                self.wfile.write(msg.encode("utf-8"))
                                self.wfile.flush()
                            except queue.Empty:
                                # Send keep-alive ping comment
                                self.wfile.write(b": ping\n\n")
                                self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        with server_instance._sse_clients_lock:
                            if client_q in server_instance._sse_clients:
                                server_instance._sse_clients.remove(client_q)
                    return

                # 2. API: Status check (/api/status)
                if path == "/api/status":
                    if not self._validate_auth():
                        self.send_error(403, "Invalid session token")
                        return
                    status_info = {
                        "status": "online",
                        "server_time": round(time.time(), 3),
                    }
                    if server_instance._status_provider:
                        status_info.update(server_instance._status_provider())
                    self._send_json(200, status_info)
                    return

                # 3. Static Web UI files
                if path == "/" or path == "/index.html":
                    if not self._validate_auth():
                        self.send_error(403, "Session token required to access Web UI")
                        return
                    file_path = os.path.join(server_instance.static_dir, "index.html")
                else:
                    safe_rel = os.path.normpath(path.lstrip("/")).replace("\\", "/")
                    if safe_rel.startswith(".."):
                        self.send_error(403, "Forbidden path")
                        return
                    file_path = os.path.join(server_instance.static_dir, safe_rel)

                if os.path.isfile(file_path):
                    content_type = "text/html"
                    if file_path.endswith(".css"):
                        content_type = "text/css"
                    elif file_path.endswith(".js"):
                        content_type = "application/javascript"
                    elif file_path.endswith(".json"):
                        content_type = "application/json"
                    elif file_path.endswith(".svg"):
                        content_type = "image/svg+xml"

                    with open(file_path, "rb") as f:
                        content = f.read()

                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                else:
                    self.send_error(404, "File Not Found")

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path

                if not self._validate_auth():
                    self.send_error(403, "Invalid session token")
                    return

                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len)
                try:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                except Exception:
                    self._send_json(400, {"error": "Invalid JSON payload"})
                    return

                if path == "/api/prompt":
                    prompt = payload.get("prompt", "").strip()
                    if not prompt:
                        self._send_json(400, {"error": "Empty prompt"})
                        return
                    if server_instance._prompt_callback:
                        server_instance._prompt_callback(prompt)
                    self._send_json(200, {"status": "ok", "action": "prompt_submitted"})
                    return

                if path == "/api/cancel":
                    if server_instance._cancel_callback:
                        server_instance._cancel_callback()
                    self._send_json(200, {"status": "ok", "action": "turn_cancelled"})
                    return

                self.send_error(404, "Unknown API endpoint")

        self._server = ThreadingHTTPServer((self.host, self.port), BridgeHandler)
        self._is_running = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="BlenderAI-WebServer"
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the local web server and cleanly terminate background thread."""
        if not self._is_running:
            return
        self._is_running = False
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None
        with self._sse_clients_lock:
            self._sse_clients.clear()

    def broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Push an event to all connected SSE clients."""
        payload = {"event": event_type, "data": data, "timestamp": round(time.time(), 3)}
        msg = f"data: {json.dumps(payload)}\n\n"
        with self._sse_clients_lock:
            for q in list(self._sse_clients):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass
