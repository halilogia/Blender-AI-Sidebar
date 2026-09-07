"""Pure Python Server-Sent Events (SSE) stream parser.

Decodes raw bytes chunks into normalized SSE data payloads.
Handles arbitrary TCP/HTTP chunk boundaries, multibyte UTF-8 splits,
CRLF/LF normalization, comments, multi-line data, and size limits.

Zero Blender (bpy) dependencies. Zero network dependencies. Pure Python.
"""

import codecs
from typing import List, Optional

DEFAULT_MAX_EVENT_SIZE: int = 1_048_576  # 1 MB safety cap
SSE_DONE_MARKER: str = "[DONE]"


class SSEParseError(Exception):
    """Raised when SSE stream decoding or parsing encounters an unrecoverable error."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class SSEParser:
    """Incremental, streaming SSE parser operating on raw bytes."""

    def __init__(self, max_event_size: int = DEFAULT_MAX_EVENT_SIZE):
        self.max_event_size = max_event_size
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._buffer: str = ""
        self._current_data_lines: List[str] = []
        self._is_done: bool = False

    @property
    def is_done(self) -> bool:
        """True if a data: [DONE] marker was encountered."""
        return self._is_done

    def feed(self, chunk: bytes) -> List[str]:
        """Feed a chunk of raw bytes and return any complete SSE event payloads.

        Args:
            chunk: Raw bytes from the network or stream.

        Returns:
            List of string data payloads extracted from this chunk.

        Raises:
            SSEParseError: On invalid UTF-8 or if event size exceeds max_event_size.
        """
        if not chunk:
            return []

        # 1. Incremental UTF-8 decode
        try:
            text = self._decoder.decode(chunk, final=False)
        except UnicodeDecodeError as exc:
            raise SSEParseError(
                code="INVALID_UTF8",
                message=f"Invalid UTF-8 sequence encountered at byte {exc.start}: {exc.reason}",
            ) from exc

        self._buffer += text

        # 2. Check buffer size limit
        if len(self._buffer) > self.max_event_size:
            raise SSEParseError(
                code="EVENT_TOO_LARGE",
                message=f"SSE buffer exceeded maximum size of {self.max_event_size} characters.",
            )

        # 3. Extract complete lines and process events
        lines = self._extract_lines()
        return self._process_lines(lines)

    def reset(self) -> None:
        """Reset the parser state for a new stream."""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._buffer = ""
        self._current_data_lines.clear()
        self._is_done = False

    def close(self) -> List[str]:
        """Flush the incremental decoder and return any remaining complete or EOF event payloads."""
        events: List[str] = []

        # 1. Finalize incremental decoder
        try:
            final_text = self._decoder.decode(b"", final=True)
            if final_text:
                self._buffer += final_text
        except UnicodeDecodeError as exc:
            raise SSEParseError(
                code="INVALID_UTF8",
                message=f"Unterminated UTF-8 sequence at stream end: {exc.reason}",
            ) from exc

        # 2. Process any remaining lines, including incomplete trailing line at EOF
        if self._buffer:
            normalized = self._buffer.replace("\r\n", "\n").replace("\r", "\n")
            self._buffer = ""
            lines = normalized.split("\n")
            events.extend(self._process_lines(lines))

        # 3. If there are still accumulated data lines at EOF without a terminating blank line,
        # dispatch the final event per W3C SSE stream termination recovery semantics
        if self._current_data_lines:
            payload = "\n".join(self._current_data_lines)
            if payload == SSE_DONE_MARKER:
                self._is_done = True
            events.append(payload)
            self._current_data_lines.clear()

        return events

    def _extract_lines(self) -> List[str]:
        """Extract complete lines from self._buffer, normalizing CRLF and LF."""
        buf = self._buffer

        # If buffer ends with '\r', keep it pending because the next chunk might deliver '\n'
        if buf.endswith("\r"):
            pending_r = "\r"
            buf = buf[:-1]
        else:
            pending_r = ""

        # Normalize CRLF and standalone CR to LF
        normalized = buf.replace("\r\n", "\n").replace("\r", "\n")

        if "\n" in normalized:
            parts = normalized.split("\n")
            # All parts except the last one are complete lines
            lines = parts[:-1]
            self._buffer = parts[-1] + pending_r
            return lines
        else:
            self._buffer = normalized + pending_r
            return []

    def _process_lines(self, lines: List[str]) -> List[str]:
        """Process extracted lines according to the W3C SSE event specification."""
        events: List[str] = []

        for line in lines:
            # Empty line -> dispatches the currently accumulated event
            if line == "":
                if self._current_data_lines:
                    payload = "\n".join(self._current_data_lines)
                    if payload == SSE_DONE_MARKER:
                        self._is_done = True
                    events.append(payload)
                    self._current_data_lines.clear()
                continue

            # Comment line -> ignore
            if line.startswith(":"):
                continue

            # Parse field and value
            if ":" in line:
                field, _, value = line.partition(":")
                # Per SSE spec: if value starts with a space, strip one leading space
                if value.startswith(" "):
                    value = value[1:]
            else:
                field = line
                value = ""

            if field == "data":
                self._current_data_lines.append(value)
                # Check accumulated event payload size limit
                current_size = sum(len(line) for line in self._current_data_lines)
                if current_size > self.max_event_size:
                    raise SSEParseError(
                        code="EVENT_TOO_LARGE",
                        message=f"Accumulated SSE data exceeded limit of {self.max_event_size} characters.",
                    )
            # Other fields (id, event, retry) are safely ignored without crashing

        return events
