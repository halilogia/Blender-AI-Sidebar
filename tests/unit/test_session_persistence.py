"""Unit tests for M8 Task 4: .blend Reload Session Persistence.

Verifies:
- serialize / deserialize round-trip.
- empty state serialization and restoration.
- missing or None property handling.
- corrupted JSON / invalid payload handling without exceptions.
- unsupported / future schema version rejection.
- deleted_entities preservation across persistence cycle.
- verified scene state preservation across persistence cycle.
- visual verification metadata preservation across persistence cycle.
- secrets and API keys are strictly excluded from persisted state.
- AgentRuntime export_session_memory / restore_session_memory cycle.
- Conversation sequence validity after restore.
- Mock scene save / load cycle.
"""

import json
import unittest
from unittest.mock import MagicMock

from agent.memory import (
    SESSION_MEMORY_PROPERTY_NAME,
    SESSION_MEMORY_SCHEMA_VERSION,
    RollingMemory,
    deserialize_session_memory,
    serialize_session_memory,
)
from agent.models import ChatMessage, Conversation, Role, ToolCall
from agent.runtime import AgentRuntime
from adapter.session_persistence import (
    load_session_memory_from_scene,
    save_session_memory_to_scene,
)


class MockScene(dict):
    """Dictionary-backed mock for Blender Scene datablock."""
    pass


class TestSessionPersistence(unittest.TestCase):
    """Test suite covering M8 Task 4 Session Persistence requirements."""

    def test_serialize_deserialize_round_trip(self):
        """Verify RollingMemory round-trips through serialize/deserialize perfectly."""
        mem = RollingMemory()
        mem.tasks = ["Task 1: Create a red cube", "Task 2: Inspect scene"]
        mem.verified_mutations = {
            "Cube": {
                "operation": "create",
                "target": "Cube",
                "status": "PASS",
                "properties": {"location": [0.0, 0.0, 1.0]},
            }
        }
        mem.deleted_entities = ["OldCylinder"]
        mem.inspections = ["inspect_scene(1 objects)"]
        mem.errors = ["set_material ('Cube'): VERIFICATION_FAILED"]
        mem.last_visual_verification = {
            "decision": "PASS",
            "rationale": "Red cube is centered in viewport.",
        }

        serialized_str = serialize_session_memory(mem)
        self.assertIsInstance(serialized_str, str)

        parsed_json = json.loads(serialized_str)
        self.assertEqual(parsed_json["schema_version"], SESSION_MEMORY_SCHEMA_VERSION)
        self.assertEqual(len(parsed_json["tasks"]), 2)
        self.assertIn("Cube", parsed_json["verified_mutations"])

        restored_mem = deserialize_session_memory(serialized_str)
        self.assertIsNotNone(restored_mem)
        self.assertEqual(restored_mem.tasks, mem.tasks)
        self.assertEqual(restored_mem.verified_mutations, mem.verified_mutations)
        self.assertEqual(restored_mem.deleted_entities, ["OldCylinder"])
        self.assertEqual(restored_mem.inspections, ["inspect_scene(1 objects)"])
        self.assertEqual(restored_mem.errors, mem.errors)
        self.assertEqual(restored_mem.last_visual_verification, mem.last_visual_verification)

    def test_empty_state_handling(self):
        """Empty RollingMemory produces valid schema dict and deserializes cleanly."""
        mem = RollingMemory()
        serialized_str = serialize_session_memory(mem)
        restored = deserialize_session_memory(serialized_str)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.tasks, [])
        self.assertEqual(restored.verified_mutations, {})
        self.assertEqual(restored.deleted_entities, [])
        self.assertEqual(restored.inspections, [])
        self.assertEqual(restored.errors, [])
        self.assertIsNone(restored.last_visual_verification)

    def test_missing_or_none_property_returns_none(self):
        """Missing or None payload returns None without error."""
        self.assertIsNone(deserialize_session_memory(None))
        self.assertIsNone(deserialize_session_memory(""))
        self.assertIsNone(deserialize_session_memory({}))

    def test_corrupted_json_payload_handled_safely(self):
        """Corrupted or invalid JSON string does not raise unhandled exception."""
        corrupted_payloads = [
            "NOT_JSON_DATA",
            "{bad_json: True",
            "{\"schema_version\": 1, \"tasks\": [unclosed]",
            12345,
            [1, 2, 3],
        ]
        for bad in corrupted_payloads:
            restored = deserialize_session_memory(bad)
            self.assertIsNone(restored)

    def test_unsupported_or_future_schema_version_rejected(self):
        """Payload with missing, negative, or higher schema version is safely rejected."""
        bad_versions = [
            {"tasks": ["Do task"]},  # Missing schema_version
            {"schema_version": 0, "tasks": []},
            {"schema_version": -1, "tasks": []},
            {"schema_version": 999, "tasks": []},  # Incompatible future version
            {"schema_version": "1", "tasks": []},  # Non-int version
        ]
        for payload in bad_versions:
            self.assertIsNone(RollingMemory.from_dict(payload))
            self.assertIsNone(deserialize_session_memory(json.dumps(payload)))

    def test_deleted_entities_preservation(self):
        """Deleted entities list is preserved cleanly across serialization."""
        mem = RollingMemory()
        mem.deleted_entities = ["ObjectA", "ObjectB"]
        serialized = serialize_session_memory(mem)
        restored = deserialize_session_memory(serialized)
        self.assertIsNotNone(restored)
        self.assertEqual(sorted(restored.deleted_entities), ["ObjectA", "ObjectB"])

    def test_verified_scene_state_preservation(self):
        """Verified mutation records are preserved cleanly across serialization."""
        mem = RollingMemory()
        mem.verified_mutations = {
            "Suzanne": {
                "operation": "create",
                "target": "Suzanne",
                "status": "PASS",
                "properties": {"location": [1.0, 2.0, 3.0]},
            }
        }
        serialized = serialize_session_memory(mem)
        restored = deserialize_session_memory(serialized)
        self.assertIsNotNone(restored)
        self.assertIn("Suzanne", restored.verified_mutations)
        self.assertEqual(restored.verified_mutations["Suzanne"]["properties"]["location"], [1.0, 2.0, 3.0])

    def test_visual_verification_metadata_preservation(self):
        """Visual verification decision and rationale are preserved."""
        mem = RollingMemory()
        mem.last_visual_verification = {
            "decision": "PASS",
            "rationale": "Object aligned properly.",
        }
        serialized = serialize_session_memory(mem)
        restored = deserialize_session_memory(serialized)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.last_visual_verification["decision"], "PASS")
        self.assertEqual(restored.last_visual_verification["rationale"], "Object aligned properly.")

    def test_secrets_and_api_keys_strictly_excluded(self):
        """Ensure no API keys, secrets, or raw bytes can ever be persisted or restored."""
        malicious_dict = {
            "schema_version": 1,
            "tasks": ["Normal task"],
            "verified_mutations": {
                "Cube": {
                    "operation": "create",
                    "target": "Cube",
                    "status": "PASS",
                    "api_key": "sk-SECRET-KEY-12345",
                    "secret": "super_secret_token",
                    "image_id": "image_999",
                    "bytes": b"fake_bytes",
                }
            },
            "deleted_entities": [],
            "inspections": [],
            "errors": [],
        }
        restored = RollingMemory.from_dict(malicious_dict)
        self.assertIsNotNone(restored)
        cube_data = restored.verified_mutations["Cube"]
        self.assertNotIn("api_key", cube_data)
        self.assertNotIn("secret", cube_data)
        self.assertNotIn("image_id", cube_data)
        self.assertNotIn("bytes", cube_data)

    def test_runtime_export_session_memory_active_conversation(self):
        """AgentRuntime extracts valid RollingMemory from an active conversation."""
        dispatcher = MagicMock()
        provider = MagicMock()
        runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)

        tc = ToolCall(call_id="c1", tool_name="create_primitive", arguments={"type": "CUBE"})
        res = json.dumps({
            "name": "Cube",
            "location": [0.0, 0.0, 0.0],
            "verification": {"status": "PASS", "operation": "create", "target_name": "Cube"},
        })
        runtime.conversation.add_message(ChatMessage(role=Role.SYSTEM, content="System"))
        runtime.conversation.add_message(ChatMessage(role=Role.USER, content="Make a cube"))
        runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc]))
        runtime.conversation.add_message(ChatMessage(role=Role.TOOL, content=res, tool_call_id="c1", name="create_primitive"))
        runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content="Done"))

        exported = runtime.export_session_memory()
        self.assertIsNotNone(exported)
        self.assertIn("Cube", exported.verified_mutations)
        self.assertIn("Make a cube", exported.tasks)

    def test_runtime_export_session_memory_empty_conversation(self):
        """Empty conversation returns None from export_session_memory."""
        dispatcher = MagicMock()
        provider = MagicMock()
        runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)
        self.assertIsNone(runtime.export_session_memory())

    def test_runtime_restore_session_memory_builds_valid_conversation(self):
        """AgentRuntime restore_session_memory populates conversation with summary messages."""
        dispatcher = MagicMock()
        provider = MagicMock()
        runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)

        mem = RollingMemory()
        mem.tasks = ["Build tower"]
        mem.verified_mutations = {
            "Base": {"operation": "create", "target": "Base", "status": "PASS"}
        }

        success = runtime.restore_session_memory(mem)
        self.assertTrue(success)

        # Conversation should have: SYSTEM, summary_user, ack_assistant
        self.assertEqual(len(runtime.conversation.messages), 3)
        self.assertEqual(runtime.conversation.messages[0].role, Role.SYSTEM)
        self.assertEqual(runtime.conversation.messages[1].role, Role.USER)
        self.assertIn("[Context Summary & Scene Memory]", runtime.conversation.messages[1].content)
        self.assertIn("'Base': create", runtime.conversation.messages[1].content)
        self.assertEqual(runtime.conversation.messages[2].role, Role.ASSISTANT)

        # Validate sequence integrity
        runtime.conversation.validate_sequence()

    def test_save_and_load_session_memory_with_mock_scene(self):
        """End-to-end test of save_session_memory_to_scene and load_session_memory_from_scene."""
        dispatcher = MagicMock()
        provider = MagicMock()
        runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)

        tc = ToolCall(call_id="c1", tool_name="create_primitive", arguments={"type": "SPHERE"})
        res = json.dumps({
            "name": "Sphere",
            "verification": {"status": "PASS", "operation": "create", "target_name": "Sphere"},
        })
        runtime.conversation.add_message(ChatMessage(role=Role.SYSTEM, content="System"))
        runtime.conversation.add_message(ChatMessage(role=Role.USER, content="Create sphere"))
        runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc]))
        runtime.conversation.add_message(ChatMessage(role=Role.TOOL, content=res, tool_call_id="c1", name="create_primitive"))
        runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content="Done"))

        scene = MockScene()

        # Save to scene
        saved = save_session_memory_to_scene(scene=scene, runtime=runtime)
        self.assertTrue(saved)
        self.assertIn(SESSION_MEMORY_PROPERTY_NAME, scene)

        # Simulate fresh session in a new runtime
        new_runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)
        self.assertEqual(len(new_runtime.conversation.messages), 0)

        # Load from scene
        loaded = load_session_memory_from_scene(scene=scene, runtime=new_runtime)
        self.assertTrue(loaded)

        # Verified state is restored in new_runtime
        summary_content = new_runtime.conversation.messages[1].content
        self.assertIn("'Sphere': create", summary_content)
        new_runtime.conversation.validate_sequence()

    def test_task_containing_api_key_is_sanitized(self):
        """User task containing API key or secret token is deterministically redacted."""
        mem = RollingMemory()
        mem.tasks = [
            "Create a torus using api_key: sk-proj-1234567890abcdef1234567890 in scene",
            "Fetch texture with apiKey='AIzaSyD-1234567890abcdef1234567890abcdef'",
        ]
        serialized = serialize_session_memory(mem)
        self.assertNotIn("sk-proj-1234567890abcdef1234567890", serialized)
        self.assertNotIn("AIzaSyD-1234567890abcdef1234567890abcdef", serialized)
        self.assertIn("[REDACTED_SECRET]", serialized)
        self.assertIn("Create a torus using api_key: [REDACTED_SECRET] in scene", serialized)

    def test_error_containing_secret_is_sanitized(self):
        """Error messages containing secret parameters or Bearer tokens are redacted."""
        mem = RollingMemory()
        mem.errors = [
            "Network error with secret='my_super_secret_token_123' at endpoint",
            "Authorization failed: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 expired",
        ]
        serialized = serialize_session_memory(mem)
        self.assertNotIn("my_super_secret_token_123", serialized)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", serialized)
        self.assertIn("secret='[REDACTED_SECRET]'", serialized)
        self.assertIn("Bearer [REDACTED_SECRET]", serialized)

    def test_inspection_containing_credential_is_sanitized(self):
        """Inspection results containing credentials, image_ids, or raw byte blobs are redacted."""
        mem = RollingMemory()
        mem.inspections = [
            "inspect_object: found credential='admin:hunter2' and token: ghp_123456789012345678901234567890",
            "capture_viewport: image_id='img_0123456789abcdef' data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        ]
        serialized = serialize_session_memory(mem)
        self.assertNotIn("admin:hunter2", serialized)
        self.assertNotIn("ghp_123456789012345678901234567890", serialized)
        self.assertNotIn("img_0123456789abcdef", serialized)
        self.assertNotIn("data:image/png;base64", serialized)
        self.assertIn("credential='[REDACTED_SECRET]'", serialized)
        self.assertIn("[REDACTED_IMAGE_ID]", serialized)
        self.assertIn("[REDACTED_IMAGE_BYTES]", serialized)

    def test_normal_task_error_inspection_text_preserved(self):
        """Normal descriptive texts for tasks, errors, and inspections are preserved verbatim."""
        mem = RollingMemory()
        mem.tasks = ["Create a blue cylinder at [0.0, 1.0, 2.0] and scale by 2.0"]
        mem.errors = ["ValueError: Mesh has 0 vertices and cannot be subdivided"]
        mem.inspections = ["inspect_mesh('Cylinder'): 64 verts, 32 faces"]

        serialized = serialize_session_memory(mem)
        restored = deserialize_session_memory(serialized)
        self.assertIsNotNone(restored)

        self.assertEqual(restored.tasks, ["Create a blue cylinder at [0.0, 1.0, 2.0] and scale by 2.0"])
        self.assertEqual(restored.errors, ["ValueError: Mesh has 0 vertices and cannot be subdivided"])
        self.assertEqual(restored.inspections, ["inspect_mesh('Cylinder'): 64 verts, 32 faces"])

    def test_serialize_deserialize_round_trip_eliminates_all_sensitive_data(self):
        """Full round-trip test ensuring sensitive tokens never survive in persisted or restored state."""
        mem = RollingMemory()
        mem.tasks = ["Set password: superSecretPassword123 on asset"]
        mem.errors = ["Auth token: glpat-12345678901234567890 rejected"]
        mem.inspections = ["Raw buffer: b'\\x89PNG\\r\\n\\x1a\\n\\x00\\x00\\x00\\rIHDR\\x00\\x00'"]
        mem.verified_mutations = {
            "SensitiveCube": {
                "operation": "create",
                "target": "SensitiveCube",
                "api_key": "sk-1234567890abcdef1234",
                "token": "ghp_secrettoken1234567890",
                "status": "PASS",
                "properties": {"location": [0.0, 0.0, 0.0]},
            }
        }
        mem.deleted_entities = ["OldSecretMesh"]

        # 1. Serialize to JSON string
        raw_json = serialize_session_memory(mem)

        # 2. Assert raw JSON contains zero sensitive secrets
        self.assertNotIn("superSecretPassword123", raw_json)
        self.assertNotIn("glpat-12345678901234567890", raw_json)
        self.assertNotIn("sk-1234567890abcdef1234", raw_json)
        self.assertNotIn("ghp_secrettoken1234567890", raw_json)
        self.assertNotIn("\\x89PNG", raw_json)

        # 3. Deserialize back
        restored = deserialize_session_memory(raw_json)
        self.assertIsNotNone(restored)

        # 4. Assert restored state is sanitized
        self.assertIn("password: [REDACTED_SECRET]", restored.tasks[0])
        self.assertIn("token: [REDACTED_SECRET]", restored.errors[0])
        self.assertIn("[REDACTED_BYTES]", restored.inspections[0])
        self.assertNotIn("api_key", restored.verified_mutations["SensitiveCube"])
        self.assertNotIn("token", restored.verified_mutations["SensitiveCube"])
        self.assertEqual(restored.verified_mutations["SensitiveCube"]["properties"]["location"], [0.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
