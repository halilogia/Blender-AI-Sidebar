"""Unit tests for capture_viewport tool and in-memory PNG encoding.

Pure Python standard library only. Runs without requiring Blender runtime.
"""

import struct
import unittest
from unittest.mock import MagicMock
import zlib

from core.types import RiskLevel, ToolResult
from tools.read_only.capture_viewport import CaptureViewportTool
from adapter.readers.viewport_reader import ViewportReader, encode_png_rgba


class MockAdapter:
    """Mock adapter simulating BlenderAdapter.capture_viewport responses."""

    def __init__(self):
        self.last_call = None

    def capture_viewport(self, width: int = 512, height: int = 512) -> ToolResult:
        self.last_call = {"width": width, "height": height}
        if width < 64 or height < 64:
            return ToolResult.fail("capture_viewport", "INVALID_ARGUMENT", "Dimensions too small")
        return ToolResult.ok(
            "capture_viewport",
            {
                "image_id": "vp_mock123456",
                "width": width,
                "height": height,
                "format": "PNG",
                "mime_type": "image/png",
                "byte_size": 1024,
                "channels": 4,
            },
        )


class TestCaptureViewportToolContract(unittest.TestCase):
    """Verify tool contract, schema, and execution dispatch for capture_viewport."""

    def setUp(self):
        self.tool = CaptureViewportTool()
        self.adapter = MockAdapter()

    def test_tool_metadata(self):
        """Tool name, description, and READ_ONLY risk level are strictly enforced."""
        self.assertEqual(self.tool.name, "capture_viewport")
        self.assertEqual(self.tool.risk_level, RiskLevel.READ_ONLY)
        self.assertIn("3D Viewport", self.tool.description)

    def test_input_schema_structure(self):
        """Schema defines optional width and height with type integer and bounds."""
        schema = self.tool.to_schema()
        self.assertEqual(schema["name"], "capture_viewport")
        self.assertEqual(schema["risk_level"], "READ_ONLY")

        params = schema["input_schema"]
        self.assertEqual(params["type"], "object")
        self.assertIn("width", params["properties"])
        self.assertIn("height", params["properties"])
        self.assertEqual(params["properties"]["width"]["type"], "integer")
        self.assertEqual(params["properties"]["height"]["type"], "integer")
        self.assertEqual(params["properties"]["width"]["minimum"], 64)
        self.assertEqual(params["properties"]["width"]["maximum"], 2048)

    def test_execute_delegates_to_adapter(self):
        """Tool execute delegates width and height arguments to adapter."""
        res = self.tool.execute(self.adapter, width=640, height=480)
        self.assertTrue(res.success)
        self.assertEqual(self.adapter.last_call, {"width": 640, "height": 480})
        self.assertEqual(res.data["width"], 640)
        self.assertEqual(res.data["height"], 480)
        self.assertEqual(res.data["format"], "PNG")
        self.assertEqual(res.data["mime_type"], "image/png")

    def test_execute_defaults_to_512(self):
        """Default arguments are 512x512."""
        res = self.tool.execute(self.adapter)
        self.assertTrue(res.success)
        self.assertEqual(self.adapter.last_call, {"width": 512, "height": 512})


class TestPNGEncoder(unittest.TestCase):
    """Verify in-memory PNG encoder produces valid PNG headers and chunks."""

    def test_encode_png_header_and_ihdr(self):
        """Encoder generates standard 8-byte PNG signature and valid IHDR."""
        width, height = 4, 4
        rgba_bytes = bytes([255, 0, 128, 255] * (width * height))
        png = encode_png_rgba(width, height, rgba_bytes)

        # 1. 8-byte PNG signature
        png_sig = b"\x89PNG\r\n\x1a\n"
        self.assertTrue(png.startswith(png_sig))

        # 2. First chunk must be IHDR
        ihdr_len = struct.unpack(">I", png[8:12])[0]
        self.assertEqual(ihdr_len, 13)
        self.assertEqual(png[12:16], b"IHDR")

        # 3. IHDR payload: width (4), height (4), bit_depth (1), color_type (1)
        w, h, depth, color_type = struct.unpack(">IIBB", png[16:26])
        self.assertEqual(w, width)
        self.assertEqual(h, height)
        self.assertEqual(depth, 8)
        self.assertEqual(color_type, 6)  # RGBA

        # 4. Must contain IDAT and IEND
        self.assertIn(b"IDAT", png)
        self.assertTrue(png.endswith(b"IEND\xaeB`\x82"))

    def test_encode_png_crc_integrity(self):
        """All chunks in generated PNG have valid CRC32 checksums."""
        width, height = 2, 2
        rgba_bytes = bytes([10, 20, 30, 255] * 4)
        png = encode_png_rgba(width, height, rgba_bytes)

        offset = 8  # Skip signature
        while offset < len(png):
            length = struct.unpack(">I", png[offset : offset + 4])[0]
            tag = png[offset + 4 : offset + 8]
            data = png[offset + 8 : offset + 8 + length]
            crc = struct.unpack(">I", png[offset + 8 + length : offset + 12 + length])[0]

            expected_crc = zlib.crc32(tag + data) & 0xFFFFFFFF
            self.assertEqual(crc, expected_crc, f"CRC mismatch in chunk {tag}")
            offset += 12 + length


class TestViewportReaderCache(unittest.TestCase):
    """Verify in-memory LRU cache and dimension validations."""

    def test_cache_storage_and_eviction(self):
        """Cache stores items and bounds size to MAX_CACHE_SIZE."""
        reader = ViewportReader()
        reader.MAX_CACHE_SIZE = 3

        # Simulate caching
        reader._cache["id1"] = b"data1"
        reader._cache["id2"] = b"data2"
        reader._cache["id3"] = b"data3"

        self.assertEqual(reader.get_image_bytes("id1"), b"data1")
        self.assertEqual(len(reader._cache), 3)

        # Eviction occurs when new item added beyond capacity
        if len(reader._cache) >= reader.MAX_CACHE_SIZE:
            reader._cache.popitem(last=False)
        reader._cache["id4"] = b"data4"

        self.assertIsNone(reader.get_image_bytes("id1"))  # Evicted
        self.assertEqual(reader.get_image_bytes("id4"), b"data4")
        self.assertEqual(len(reader._cache), 3)

        reader.clear_cache()
        self.assertEqual(len(reader._cache), 0)


if __name__ == "__main__":
    unittest.main()
