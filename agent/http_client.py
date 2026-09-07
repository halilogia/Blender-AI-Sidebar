"""Pure Python HTTP client for streaming LLM requests.

Handles HTTP POST, URL joining, headers, streaming response chunks,
timeouts, cancellation, and deterministic HTTP error handling using exclusively
Python standard library modules (urllib.request, http.client, socket, ssl).

Zero Blender (bpy) dependencies. Zero JSON/SSE parsing dependencies.
"""

import http.client
import os
import socket
import ssl
import threading
from typing import Dict, Iterator, Mapping, Optional
import urllib.error
import urllib.request

DEFAULT_CHUNK_SIZE: int = 8192
DEFAULT_TIMEOUT_SECONDS: float = 30.0
MAX_ERROR_SNIPPET_LENGTH: int = 500


class NetworkError(Exception):
    """Base exception for transport/network failures."""

    def __init__(self, message: str, details: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.details = details


class HttpTimeoutError(NetworkError):
    """Raised when connection or read operation times out."""


class HttpConnectionError(NetworkError):
    """Raised when connection fails (DNS resolution, refused, reset)."""


class HttpError(Exception):
    """Raised on non-2xx HTTP responses."""

    def __init__(
        self,
        status_code: int,
        message: str,
        headers: Optional[Dict[str, str]] = None,
        body_snippet: str = "",
    ):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.headers = headers or {}
        self.body_snippet = body_snippet


class HttpResponse:
    """Streaming response abstraction wrapping urllib/http.client response."""

    def __init__(
        self,
        raw_response: http.client.HTTPResponse,
        status_code: int,
        headers: Dict[str, str],
        url: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        cancel_event: Optional[threading.Event] = None,
    ):
        self.raw_response = raw_response
        self.status_code = status_code
        self.headers = headers
        self.url = url
        self.chunk_size = chunk_size
        self.cancel_event = cancel_event
        self._closed = False
        self._lock = threading.Lock()

    @property
    def is_closed(self) -> bool:
        """Return True if the underlying response has been closed."""
        return self._closed

    def close(self) -> None:
        """Close the underlying HTTP socket and response."""
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self.raw_response.close()
                except Exception:
                    pass

    def __enter__(self) -> "HttpResponse":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __iter__(self) -> Iterator[bytes]:
        """Yield raw bytes chunks until stream is exhausted or cancelled."""
        try:
            while True:
                # 1. Check cancellation before blocking read
                if self.cancel_event and self.cancel_event.is_set():
                    self.close()
                    break

                if self._closed:
                    break

                # 2. Perform streaming chunk read (using read1 for single-system-call streaming)
                try:
                    if hasattr(self.raw_response, "read1"):
                        chunk = self.raw_response.read1(self.chunk_size)
                    else:
                        chunk = self.raw_response.read(self.chunk_size)
                except (OSError, ValueError, http.client.HTTPException):
                    # Connection closed, aborted, or socket closed by another thread
                    break

                if not chunk:
                    break

                yield chunk

                # 3. Check cancellation after read
                if self.cancel_event and self.cancel_event.is_set():
                    self.close()
                    break
        finally:
            self.close()


class HttpClient:
    """Independent streaming HTTP client built purely on urllib.request."""

    def __init__(
        self,
        base_url: str = "",
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ):
        self.base_url = base_url.strip()
        self.timeout = timeout
        self.chunk_size = chunk_size

    @staticmethod
    def join_url(base_url: str, endpoint: str) -> str:
        """Deterministically joins base_url and endpoint without double slashes."""
        base = base_url.strip().rstrip("/")
        ep = endpoint.strip().lstrip("/")
        if not base:
            return ep
        if not ep:
            return base
        return f"{base}/{ep}"

    def post(
        self,
        endpoint: str,
        payload: bytes,
        headers: Optional[Mapping[str, str]] = None,
        cancel_event: Optional[threading.Event] = None,
        timeout: Optional[float] = None,
    ) -> HttpResponse:
        """Execute HTTP POST with streaming response.

        Args:
            endpoint: URL path or full URL.
            payload: Raw bytes body to send.
            headers: Optional mapping of HTTP headers.
            cancel_event: Optional threading.Event to signal cancellation.
            timeout: Optional per-request timeout in seconds (defaults to self.timeout).

        Returns:
            HttpResponse object supporting streaming bytes iteration.

        Raises:
            HttpError: When server returns non-2xx status code.
            HttpTimeoutError: When connection or read times out.
            HttpConnectionError: When network/connection fails.
        """
        # 1. Construct target URL
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            full_url = endpoint
        else:
            full_url = self.join_url(self.base_url, endpoint)

        # 2. Prepare and sanitize headers
        req_headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        if headers:
            for key, val in headers.items():
                k_lower = key.lower()
                if k_lower == "authorization":
                    # Only include Authorization if value is non-empty and not a placeholder
                    cleaned_val = val.strip() if val else ""
                    if cleaned_val and cleaned_val not in ("Bearer", "Bearer None", "None"):
                        req_headers[key] = cleaned_val
                else:
                    req_headers[key] = val

        # 3. Create urllib Request
        req = urllib.request.Request(
            url=full_url,
            data=payload,
            headers=req_headers,
            method="POST",
        )

        effective_timeout = timeout if timeout is not None else self.timeout

        # 4. Configure TLS context for HTTPS
        ssl_context: Optional[ssl.SSLContext] = None
        if full_url.startswith("https://"):
            ssl_context = ssl.create_default_context()

        # 5. Execute request
        try:
            raw_response = urllib.request.urlopen(
                req,
                timeout=effective_timeout,
                context=ssl_context,
            )
        except urllib.error.HTTPError as err:
            # Read snippet of error response safely without leaking secrets
            snippet = ""
            try:
                raw_err_body = err.read(MAX_ERROR_SNIPPET_LENGTH)
                snippet = raw_err_body.decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            finally:
                try:
                    err.close()
                except Exception:
                    pass

            raise HttpError(
                status_code=err.code,
                message=str(err.reason),
                headers=dict(err.headers) if err.headers else {},
                body_snippet=snippet,
            ) from err

        except urllib.error.URLError as err:
            reason = err.reason
            reason_str = str(reason)
            if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in reason_str.lower():
                raise HttpTimeoutError(
                    message=f"Request to {full_url} timed out after {effective_timeout}s.",
                    details=reason_str,
                ) from err
            else:
                raise HttpConnectionError(
                    message=f"Failed to connect to {full_url}: {reason_str}",
                    details=reason_str,
                ) from err

        except (socket.timeout, TimeoutError) as err:
            raise HttpTimeoutError(
                message=f"Request to {full_url} timed out after {effective_timeout}s.",
                details=str(err),
            ) from err

        except OSError as err:
            err_str = str(err)
            if "timed out" in err_str.lower():
                raise HttpTimeoutError(
                    message=f"Request to {full_url} timed out after {effective_timeout}s.",
                    details=err_str,
                ) from err
            else:
                raise HttpConnectionError(
                    message=f"Network error connecting to {full_url}: {err_str}",
                    details=err_str,
                ) from err

        # 6. Extract response metadata
        status_code = getattr(raw_response, "status", 200)
        resp_headers = dict(raw_response.headers) if raw_response.headers else {}

        return HttpResponse(
            raw_response=raw_response,
            status_code=status_code,
            headers=resp_headers,
            url=full_url,
            chunk_size=self.chunk_size,
            cancel_event=cancel_event,
        )
