"""Unit tests for Pure Python SSEParser (Server-Sent Events).

Covers the full M2.4 test matrix:
- Basic event parsing (single, multiple, empty, [DONE])
- Arbitrary chunk boundaries (prefix, payload, newline, separator splits)
- Line endings (LF, CRLF, mixed)
- UTF-8 decoding (Turkish characters, emoji, multibyte chunk splits)
- Keep-alive / comments (: ping, mixed comments)
- Robustness (unknown fields, malformed lines, partial events, limits, invalid UTF-8)
- Stream close / flush semantics
- Deterministic random chunk-splitting (property-style fuzzing with fixed seed)

Zero Blender (bpy) dependencies. Zero network dependencies.
"""

import random
import unittest
from typing import List

from agent.sse_parser import SSEParser, SSEParseError, DEFAULT_MAX_EVENT_SIZE


class TestSSEParserBasic(unittest.TestCase):
    """Test basic SSE event parsing."""

    def setUp(self):
        self.parser = SSEParser()

    def test_single_event(self):
        """Test parsing a single complete SSE event."""
        payload = b'data: {"message": "hello"}\n\n'
        events = self.parser.feed(payload)
        self.assertEqual(events, ['{"message": "hello"}'])
        self.assertFalse(self.parser.is_done)

    def test_multiple_events_in_single_feed(self):
        """Test parsing multiple SSE events in a single chunk."""
        payload = b'data: {"id": 1}\n\ndata: {"id": 2}\n\ndata: {"id": 3}\n\n'
        events = self.parser.feed(payload)
        self.assertEqual(events, ['{"id": 1}', '{"id": 2}', '{"id": 3}'])

    def test_empty_data_event(self):
        """Test event with empty data field."""
        payload = b"data:\n\n"
        events = self.parser.feed(payload)
        self.assertEqual(events, [""])

        payload_with_space = b"data: \n\n"
        events = self.parser.feed(payload_with_space)
        self.assertEqual(events, [""])

    def test_done_sentinel(self):
        """Test that data: [DONE] sets is_done and returns [DONE] payload."""
        payload = b"data: [DONE]\n\n"
        events = self.parser.feed(payload)
        self.assertEqual(events, ["[DONE]"])
        self.assertTrue(self.parser.is_done)

    def test_empty_input_chunk(self):
        """Feeding empty bytes returns empty list and does not alter state."""
        events = self.parser.feed(b"")
        self.assertEqual(events, [])


class TestSSEParserChunkBoundaries(unittest.TestCase):
    """Test arbitrary TCP / HTTP chunk boundaries."""

    def setUp(self):
        self.parser = SSEParser()

    def test_prefix_split(self):
        """Test chunk split across the 'data:' prefix."""
        events = []
        events.extend(self.parser.feed(b"da"))
        events.extend(self.parser.feed(b"ta: {\"status\": \"ok\"}\n\n"))
        self.assertEqual(events, ['{"status": "ok"}'])

    def test_colon_split(self):
        """Test chunk split between 'data' and ':'."""
        events = []
        events.extend(self.parser.feed(b"data"))
        events.extend(self.parser.feed(b": 123\n\n"))
        self.assertEqual(events, ["123"])

    def test_payload_split(self):
        """Test chunk split within the payload body."""
        events = []
        events.extend(self.parser.feed(b'data: {"chunk": '))
        events.extend(self.parser.feed(b'"part1", '))
        events.extend(self.parser.feed(b'"part2"}\n\n'))
        self.assertEqual(events, ['{"chunk": "part1", "part2"}'])

    def test_newline_split(self):
        """Test chunk split across the single newline."""
        events = []
        events.extend(self.parser.feed(b"data: line\n"))
        self.assertEqual(events, [])  # Event not complete yet
        events.extend(self.parser.feed(b"\n"))
        self.assertEqual(events, ["line"])

    def test_separator_split(self):
        """Test chunk split in the middle of double newlines."""
        e1 = self.parser.feed(b"data: event1\n")
        self.assertEqual(e1, [])
        e2 = self.parser.feed(b"\ndata: event2\n")
        self.assertEqual(e2, ["event1"])
        e3 = self.parser.feed(b"\n")
        self.assertEqual(e3, ["event2"])


class TestSSEParserLineEndings(unittest.TestCase):
    """Test LF, CRLF, and mixed line endings."""

    def setUp(self):
        self.parser = SSEParser()

    def test_pure_lf(self):
        """Test pure LF line endings."""
        payload = b"data: item1\n\ndata: item2\n\n"
        events = self.parser.feed(payload)
        self.assertEqual(events, ["item1", "item2"])

    def test_pure_crlf(self):
        """Test pure CRLF line endings."""
        payload = b"data: item1\r\n\r\ndata: item2\r\n\r\n"
        events = self.parser.feed(payload)
        self.assertEqual(events, ["item1", "item2"])

    def test_mixed_line_endings(self):
        """Test stream with mixed LF and CRLF."""
        payload = b"data: item1\r\n\ndata: item2\n\r\n"
        events = self.parser.feed(payload)
        self.assertEqual(events, ["item1", "item2"])

    def test_crlf_split_across_chunks(self):
        """Test chunk boundary splitting \\r and \\n in CRLF."""
        events = []
        events.extend(self.parser.feed(b"data: split_crlf\r"))
        self.assertEqual(events, [])
        events.extend(self.parser.feed(b"\n\r"))
        self.assertEqual(events, [])
        events.extend(self.parser.feed(b"\n"))
        self.assertEqual(events, ["split_crlf"])


class TestSSEParserUTF8(unittest.TestCase):
    """Test UTF-8 decoding, Turkish characters, emojis, and multibyte splits."""

    def setUp(self):
        self.parser = SSEParser()

    def test_turkish_characters(self):
        """Test UTF-8 encoding with Turkish special characters."""
        text = "data: Türkçe karakterler: ç, ğ, ı, ö, ş, ü, İ, Ğ, Ş, Ö, Ç\n\n"
        events = self.parser.feed(text.encode("utf-8"))
        self.assertEqual(events, ["Türkçe karakterler: ç, ğ, ı, ö, ş, ü, İ, Ğ, Ş, Ö, Ç"])

    def test_emojis(self):
        """Test UTF-8 encoding with 4-byte emoji characters."""
        text = 'data: {"status": "🚀 Blender 🤖 AI ✨"}\n\n'
        events = self.parser.feed(text.encode("utf-8"))
        self.assertEqual(events, ['{"status": "🚀 Blender 🤖 AI ✨"}'])

    def test_multibyte_utf8_split_2byte(self):
        """Test 2-byte character ('ç' = 0xC3 0xA7) split across two chunks."""
        # 'data: ' + 0xC3 | 0xA7 + '\n\n'
        c_bytes = "ç".encode("utf-8")
        self.assertEqual(len(c_bytes), 2)
        chunk1 = b"data: " + c_bytes[:1]
        chunk2 = c_bytes[1:] + b"\n\n"

        events = []
        events.extend(self.parser.feed(chunk1))
        self.assertEqual(events, [])
        events.extend(self.parser.feed(chunk2))
        self.assertEqual(events, ["ç"])

    def test_multibyte_utf8_split_4byte_emoji(self):
        """Test 4-byte emoji ('🚀' = 0xF0 0x9F 0x9a 0x80) split across multiple chunks."""
        rocket = "🚀".encode("utf-8")
        self.assertEqual(len(rocket), 4)

        # Feed 1 byte at a time
        events = []
        events.extend(self.parser.feed(b"data: "))
        for b in rocket:
            events.extend(self.parser.feed(bytes([b])))
        events.extend(self.parser.feed(b"\n\n"))
        self.assertEqual(events, ["🚀"])


class TestSSEParserMultilineAndFormatting(unittest.TestCase):
    """Test W3C SSE multiline data and space-stripping semantics."""

    def setUp(self):
        self.parser = SSEParser()

    def test_multiline_data(self):
        """Multiple data lines in one event are joined with newline per SSE spec."""
        payload = b"data: first line\ndata: second line\ndata: third line\n\n"
        events = self.parser.feed(payload)
        self.assertEqual(events, ["first line\nsecond line\nthird line"])

    def test_leading_space_stripping(self):
        """Only the first leading space after colon is stripped per SSE spec."""
        payload = (
            b"data:no_space\n\n"
            b"data: one_space\n\n"
            b"data:  two_spaces\n\n"
            b"data:\ttab\n\n"
        )
        events = self.parser.feed(payload)
        self.assertEqual(events, [
            "no_space",
            "one_space",
            " two_spaces",
            "\ttab",
        ])

    def test_data_without_colon(self):
        """Line with 'data' and no colon per spec sets field='data' and value=''."""
        payload = b"data\n\n"
        events = self.parser.feed(payload)
        self.assertEqual(events, [""])


class TestSSEParserComments(unittest.TestCase):
    """Test keep-alive / heartbeat comments (: ping)."""

    def setUp(self):
        self.parser = SSEParser()

    def test_standalone_ping(self):
        """Comment lines starting with colon do not emit events."""
        payload = b": ping\n\n"
        events = self.parser.feed(payload)
        self.assertEqual(events, [])

    def test_comments_mixed_with_data(self):
        """Comments interleaved between data lines are ignored."""
        payload = (
            b": keep-alive heartbeat\n"
            b"data: message 1\n"
            b": another comment\n"
            b"\n"
            b": ping\n"
            b"data: message 2\n\n"
        )
        events = self.parser.feed(payload)
        self.assertEqual(events, ["message 1", "message 2"])


class TestSSEParserRobustnessAndErrors(unittest.TestCase):
    """Test robustness against malformed input, limits, and errors."""

    def test_unknown_fields_safely_ignored(self):
        """Fields like id, event, retry are ignored without crashing."""
        parser = SSEParser()
        payload = (
            b"id: 12345\n"
            b"event: update\n"
            b"retry: 3000\n"
            b"custom_field: whatever\n"
            b"data: actual payload\n\n"
        )
        events = parser.feed(payload)
        self.assertEqual(events, ["actual payload"])

    def test_invalid_utf8_raises_sse_parse_error(self):
        """Invalid UTF-8 sequence raises SSEParseError with code INVALID_UTF8."""
        parser = SSEParser()
        invalid_bytes = b"data: \xff\xfe invalid\n\n"
        with self.assertRaises(SSEParseError) as ctx:
            parser.feed(invalid_bytes)
        self.assertEqual(ctx.exception.code, "INVALID_UTF8")

    def test_unterminated_utf8_at_close(self):
        """Unfinished multibyte sequence when close() is called raises INVALID_UTF8."""
        parser = SSEParser()
        # Feed first byte of 2-byte sequence
        parser.feed(b"data: " + b"\xc3")
        with self.assertRaises(SSEParseError) as ctx:
            parser.close()
        self.assertEqual(ctx.exception.code, "INVALID_UTF8")

    def test_buffer_size_limit_exceeded(self):
        """Buffer exceeding max_event_size raises SSEParseError with EVENT_TOO_LARGE."""
        parser = SSEParser(max_event_size=100)
        oversized = b"data: " + (b"x" * 150) + b"\n\n"
        with self.assertRaises(SSEParseError) as ctx:
            parser.feed(oversized)
        self.assertEqual(ctx.exception.code, "EVENT_TOO_LARGE")

    def test_accumulated_multiline_size_limit_exceeded(self):
        """Accumulated multi-line event data exceeding max_event_size raises EVENT_TOO_LARGE."""
        parser = SSEParser(max_event_size=100)
        # Multiple smaller lines that together exceed limit
        payload = (
            b"data: " + (b"a" * 60) + b"\n"
            b"data: " + (b"b" * 60) + b"\n\n"
        )
        with self.assertRaises(SSEParseError) as ctx:
            parser.feed(payload)
        self.assertEqual(ctx.exception.code, "EVENT_TOO_LARGE")


class TestSSEParserCloseBehavior(unittest.TestCase):
    """Test stream close() / flush behavior under various EOF conditions."""

    def test_close_with_already_completed_events(self):
        """close() on cleanly terminated stream returns empty list."""
        parser = SSEParser()
        events = parser.feed(b"data: done_clean\n\n")
        self.assertEqual(events, ["done_clean"])
        trailing = parser.close()
        self.assertEqual(trailing, [])

    def test_close_flushes_single_newline_trailing_event(self):
        """Stream terminating with single newline flushes complete accumulated event."""
        parser = SSEParser()
        events = parser.feed(b"data: final_event\n")
        self.assertEqual(events, [])
        trailing = parser.close()
        self.assertEqual(trailing, ["final_event"])

    def test_close_flushes_no_newline_trailing_event(self):
        """Stream abruptly ending without any trailing newline flushes the event."""
        parser = SSEParser()
        events = parser.feed(b"data: abrupt_end")
        self.assertEqual(events, [])
        trailing = parser.close()
        self.assertEqual(trailing, ["abrupt_end"])

    def test_close_flushes_done_marker(self):
        """Stream ending with data: [DONE] without double newline marks is_done on close()."""
        parser = SSEParser()
        events = parser.feed(b"data: [DONE]")
        self.assertEqual(events, [])
        self.assertFalse(parser.is_done)
        trailing = parser.close()
        self.assertEqual(trailing, ["[DONE]"])
        self.assertTrue(parser.is_done)

    def test_parser_reset(self):
        """reset() clears internal state and allows reusing parser."""
        parser = SSEParser()
        parser.feed(b"data: partial")
        parser.reset()
        events = parser.feed(b"data: fresh\n\n")
        self.assertEqual(events, ["fresh"])
        self.assertFalse(parser.is_done)


class TestSSEParserPropertyAndChunkSplitting(unittest.TestCase):
    """Deterministic property-based test: chunk-splitting fuzzing."""

    def test_deterministic_random_chunk_splitting(self):
        """The exact same SSE payload split into arbitrary random chunk slices

        must ALWAYS produce the exact identical list of events.
        """
        raw_stream = (
            ": connection established\n"
            'data: {"token": "Hello"}\n\n'
            ": ping\n"
            'data: {"token": " "}\n\n'
            'data: {"token": "world!"}\n\n'
            "id: 99\n"
            "data: Türkçe: şçöğüIİ\n\n"
            "data: Emojis: 🚀 🤖 ✨\n\n"
            "data: line1\ndata: line2\n\n"
            "data: [DONE]\n\n"
        ).encode("utf-8")

        # Baseline: Feed as one single chunk
        baseline_parser = SSEParser()
        expected_events = baseline_parser.feed(raw_stream)
        expected_events.extend(baseline_parser.close())
        self.assertTrue(baseline_parser.is_done)
        self.assertEqual(len(expected_events), 7)

        # 1. Test 1-byte chunks (extreme byte-by-byte boundary)
        p1 = SSEParser()
        e1 = []
        for i in range(len(raw_stream)):
            e1.extend(p1.feed(raw_stream[i : i + 1]))
        e1.extend(p1.close())
        self.assertEqual(e1, expected_events, "Failed on 1-byte chunking")
        self.assertTrue(p1.is_done)

        # 2. Test fixed-size chunks of various prime/odd sizes
        for chunk_size in [2, 3, 5, 7, 13, 17, 31, 64]:
            p = SSEParser()
            events = []
            for i in range(0, len(raw_stream), chunk_size):
                events.extend(p.feed(raw_stream[i : i + chunk_size]))
            events.extend(p.close())
            self.assertEqual(
                events,
                expected_events,
                f"Failed on fixed chunk_size={chunk_size}",
            )
            self.assertTrue(p.is_done)

        # 3. Test 20 deterministic randomized chunk splits using fixed seed
        rng = random.Random(42)
        for trial in range(20):
            p = SSEParser()
            events = []
            pos = 0
            while pos < len(raw_stream):
                step = rng.randint(1, 15)
                chunk = raw_stream[pos : pos + step]
                events.extend(p.feed(chunk))
                pos += step
            events.extend(p.close())
            self.assertEqual(
                events,
                expected_events,
                f"Failed on randomized trial={trial}",
            )
            self.assertTrue(p.is_done)


if __name__ == "__main__":
    unittest.main()
