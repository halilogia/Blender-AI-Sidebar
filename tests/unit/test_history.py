"""Pure Python unit tests for RuntimeHistory.

Zero Blender dependencies.
"""

import unittest
from agent.history import HistoryItem, HistoryKind, RuntimeHistory


class TestRuntimeHistory(unittest.TestCase):
    """Test suite for RuntimeHistory."""

    def test_add_and_query_history(self):
        history = RuntimeHistory()
        self.assertEqual(len(history), 0)

        item1 = history.add(
            item_id="1_user",
            turn_id="turn_1",
            kind=HistoryKind.USER,
            title="User: Hello",
            status="SENT",
            summary="Hello",
            detail="Hello world",
        )

        self.assertEqual(len(history), 1)
        self.assertEqual(item1.item_id, "1_user")
        self.assertEqual(history.get_by_id("1_user"), item1)
        self.assertEqual(history.get_by_index(0), item1)
        self.assertIsNone(history.get_by_id("nonexistent"))
        self.assertIsNone(history.get_by_index(5))

    def test_clear_history(self):
        history = RuntimeHistory()
        history.add("1", "t1", HistoryKind.USER, "Test")
        history.add("2", "t1", HistoryKind.ASSISTANT, "Reply")
        self.assertEqual(len(history), 2)

        history.clear()
        self.assertEqual(len(history), 0)
        self.assertIsNone(history.get_by_index(0))

    def test_update_existing_item(self):
        history = RuntimeHistory()
        history.add("queued_1", "queued_1", HistoryKind.USER, "Queued: test", status="QUEUED")
        updated = history.update(
            "queued_1", turn_id="turn_1", title="User: test", status="RUNNING", summary="test"
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.turn_id, "turn_1")
        self.assertEqual(updated.status, "RUNNING")
        self.assertIsNone(history.update("missing", status="ERROR"))

    def test_history_item_to_dict(self):
        item = HistoryItem(
            item_id="item_1",
            turn_id="turn_1",
            kind=HistoryKind.TOOL,
            title="Tool: inspect_scene",
            status="OK",
            summary="Objects: 3",
            detail="Detailed output...",
            timestamp=123.456,
        )
        d = item.to_dict()
        self.assertEqual(d["item_id"], "item_1")
        self.assertEqual(d["kind"], "TOOL")
        self.assertEqual(d["status"], "OK")
        self.assertEqual(d["timestamp"], 123.456)


if __name__ == "__main__":
    unittest.main()
