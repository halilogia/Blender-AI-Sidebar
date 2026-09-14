"""Tests for the stateless AgentRuntime event classification boundary."""

import unittest

from agent.event_router import EventRoute, EventRouter
from agent.models import ProviderCompleted, ProviderError, TextDelta, ToolCallDelta
from core.events import AgentErrorEvent, ProviderResponseReadyEvent, StreamingTextDeltaEvent
from agent.models import ProviderResponse


class TestEventRouter(unittest.TestCase):
    def setUp(self):
        self.router = EventRouter()

    def test_classifies_existing_runtime_event_branches(self):
        events = [
            (StreamingTextDeltaEvent(delta="x", turn_id="t"), EventRoute.STREAMING_TEXT_DELTA),
            (TextDelta(turn_id="t", text="x"), EventRoute.TEXT_DELTA),
            (
                ToolCallDelta(turn_id="t", index=0, tool_name_delta="create_primitive"),
                EventRoute.TOOL_CALL_DELTA,
            ),
            (ProviderError(turn_id="t", type="NETWORK_ERROR", message="failed"), EventRoute.PROVIDER_ERROR),
            (ProviderCompleted(turn_id="t", finish_reason="stop"), EventRoute.PROVIDER_COMPLETED),
            (
                ProviderResponseReadyEvent(
                    response=ProviderResponse(assistant_text="done", tool_calls=[], is_final=True),
                    turn_id="t",
                ),
                EventRoute.PROVIDER_RESPONSE_READY,
            ),
            (AgentErrorEvent(error_type="TEST", message="failed", turn_id="t"), EventRoute.AGENT_ERROR),
        ]

        for event, expected in events:
            with self.subTest(event=type(event).__name__):
                self.assertEqual(self.router.route(event), expected)

    def test_unknown_events_are_unhandled_without_side_effects(self):
        self.assertEqual(self.router.route(object()), EventRoute.UNHANDLED)

    def test_router_has_no_runtime_state(self):
        self.assertEqual(EventRouter().__slots__, ())


if __name__ == "__main__":
    unittest.main()
