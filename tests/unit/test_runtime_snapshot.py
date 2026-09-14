"""Tests for the UI-neutral runtime projection."""

import unittest
from unittest.mock import MagicMock

from agent.history import HistoryKind
from agent.runtime import AgentRuntime
from agent.runtime_snapshot import RuntimeSnapshot


class TestRuntimeSnapshot(unittest.TestCase):
    def test_snapshot_is_consistent_and_contains_queue_and_history(self):
        runtime = AgentRuntime(
            provider=object(),
            dispatcher=MagicMock(),
            worker=MagicMock(),
        )
        runtime.history.add(
            item_id="queued_1",
            turn_id="queued_1",
            kind=HistoryKind.USER,
            title="Queued: inspect scene",
            status="QUEUED",
            summary="Inspect scene",
        )
        runtime.prompt_queue.enqueue("Inspect scene")

        snapshot = runtime.snapshot()
        self.assertIsInstance(snapshot, RuntimeSnapshot)
        self.assertEqual(snapshot.state, "IDLE")
        self.assertEqual(snapshot.queued_count, 1)
        self.assertEqual(snapshot.history[0]["status"], "QUEUED")
        self.assertIsNone(snapshot.pending_approval)


if __name__ == "__main__":
    unittest.main()
