"""Tests for user-level FIFO prompt queueing."""

import unittest

from agent.prompt_queue import PromptQueue


class TestPromptQueue(unittest.TestCase):
    def test_fifo_order_and_ids(self):
        queue = PromptQueue(maxsize=2)
        first = queue.enqueue("first")
        second = queue.enqueue("second")
        self.assertEqual([item.prompt for item in queue.items], ["first", "second"])
        self.assertEqual(first.queue_id, "queued_1")
        self.assertEqual(second.queue_id, "queued_2")
        self.assertEqual(queue.pop().prompt, "first")
        self.assertEqual(queue.pop().prompt, "second")
        self.assertIsNone(queue.pop())

    def test_bounded_and_clear(self):
        queue = PromptQueue(maxsize=1)
        queue.enqueue("only")
        with self.assertRaises(OverflowError):
            queue.enqueue("overflow")
        removed = queue.clear()
        self.assertEqual([item.prompt for item in removed], ["only"])
        self.assertEqual(len(queue), 0)


if __name__ == "__main__":
    unittest.main()
