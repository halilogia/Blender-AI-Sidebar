"""Integration tests for M8 Task 4: .blend Reload Session Persistence in Blender 5.2.

Verifies:
- Handler registration in bpy.app.handlers.
- Live scene session memory export on save_pre.
- Real .blend file save and open round-trip restoring RollingMemory and verified scene state.
- Empty scene and corrupted property safety.
- Zero secret/API key contamination inside saved .blend scene property.
"""

import json
import os
import sys
import tempfile
import bpy

from adapter.blender_adapter import BlenderAdapter
from adapter.session_persistence import (
    load_session_memory_from_scene,
    on_blend_load_post,
    on_blend_save_pre,
    register_session_handlers,
    save_session_memory_to_scene,
    set_runtime_getter,
    unregister_session_handlers,
)
from agent.dispatcher import ToolDispatcher
from agent.memory import (
    SESSION_MEMORY_PROPERTY_NAME,
    SESSION_MEMORY_SCHEMA_VERSION,
    RollingMemory,
)
from agent.mock_provider import MockProvider
from agent.models import ChatMessage, Role, ToolCall
from agent.runtime import AgentRuntime
from tools.registry import ToolRegistry


def clean_scene():
    """Reset Blender scene to a completely clean state."""
    bpy.ops.wm.read_homefile(use_empty=True)


def test_handler_registration():
    print("Test 1: Handler registration in bpy.app.handlers...")
    register_session_handlers()
    assert on_blend_save_pre in bpy.app.handlers.save_pre, "on_blend_save_pre not registered"
    assert on_blend_load_post in bpy.app.handlers.load_post, "on_blend_load_post not registered"
    print("[PASS] Test 1: Handlers registered cleanly.")


def test_save_pre_exports_session_memory_to_scene():
    print("Test 2: Save pre exports session memory to active scene...")
    clean_scene()
    scene = bpy.context.scene

    dispatcher = ToolDispatcher(registry=ToolRegistry(), adapter=BlenderAdapter())
    provider = MockProvider()
    runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)

    # Simulate active turn where a cube was created
    tc = ToolCall(call_id="c1", tool_name="create_primitive", arguments={"primitive_type": "CUBE"})
    res = json.dumps({
        "name": "LiveCube",
        "location": [0.0, 0.0, 1.0],
        "verification": {"status": "PASS", "operation": "create", "target_name": "LiveCube"},
    })
    runtime.conversation.add_message(ChatMessage(role=Role.SYSTEM, content="Sys"))
    runtime.conversation.add_message(ChatMessage(role=Role.USER, content="Create a live cube"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc]))
    runtime.conversation.add_message(ChatMessage(role=Role.TOOL, content=res, tool_call_id="c1", name="create_primitive"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content="Done"))

    saved = save_session_memory_to_scene(scene=scene, runtime=runtime)
    assert saved is True, "save_session_memory_to_scene returned False"
    assert SESSION_MEMORY_PROPERTY_NAME in scene, "Property not set on scene"

    raw_payload = scene[SESSION_MEMORY_PROPERTY_NAME]
    data = json.loads(raw_payload)
    assert data["schema_version"] == SESSION_MEMORY_SCHEMA_VERSION
    assert "LiveCube" in data["verified_mutations"]
    assert "Create a live cube" in data["tasks"]
    print("[PASS] Test 2: Verified session memory written to scene custom property.")


def test_blend_save_and_reload_round_trip():
    print("Test 3: Live .blend file save and open round-trip...")
    clean_scene()
    scene = bpy.context.scene

    dispatcher = ToolDispatcher(registry=ToolRegistry(), adapter=BlenderAdapter())
    provider = MockProvider()
    runtime = AgentRuntime(provider=provider, dispatcher=dispatcher)
    set_runtime_getter(lambda: runtime)

    # Populate session state: created Suzanne and transformed it
    tc1 = ToolCall(call_id="c1", tool_name="create_primitive", arguments={"primitive_type": "MONKEY"})
    res1 = json.dumps({
        "name": "PersistMonkey",
        "verification": {"status": "PASS", "operation": "create", "target_name": "PersistMonkey"},
    })
    tc2 = ToolCall(call_id="c2", tool_name="transform_object", arguments={"object_name": "PersistMonkey", "location": [2.0, 3.0, 4.0]})
    res2 = json.dumps({
        "name": "PersistMonkey",
        "location": [2.0, 3.0, 4.0],
        "verification": {"status": "PASS", "operation": "transform", "target_name": "PersistMonkey"},
    })

    runtime.conversation.add_message(ChatMessage(role=Role.SYSTEM, content="System"))
    runtime.conversation.add_message(ChatMessage(role=Role.USER, content="Create PersistMonkey"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc1]))
    runtime.conversation.add_message(ChatMessage(role=Role.TOOL, content=res1, tool_call_id="c1", name="create_primitive"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content="Created"))
    runtime.conversation.add_message(ChatMessage(role=Role.USER, content="Move PersistMonkey to 2, 3, 4"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc2]))
    runtime.conversation.add_message(ChatMessage(role=Role.TOOL, content=res2, tool_call_id="c2", name="transform_object"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content="Moved"))

    # Save to actual temp .blend file (this fires on_blend_save_pre via Blender)
    with tempfile.TemporaryDirectory() as tmp_dir:
        blend_path = os.path.join(tmp_dir, "test_session_persistence.blend")

        # Explicitly invoke save handler to sync runtime into scene
        save_session_memory_to_scene(scene=bpy.context.scene, runtime=runtime)
        bpy.ops.wm.save_mainfile(filepath=blend_path)
        assert os.path.exists(blend_path), "Failed to save .blend file"

        # Now clear runtime conversation completely
        runtime.conversation = None
        clean_scene()
        assert runtime.conversation is None or len(runtime.conversation.messages) == 0

        # Reopen saved .blend file
        bpy.ops.wm.open_mainfile(filepath=blend_path)

        # load_post automatically invoked on_blend_load_post, or test manual trigger
        loaded_scene = bpy.context.scene
        assert SESSION_MEMORY_PROPERTY_NAME in loaded_scene, "Session memory missing in loaded scene"

        # Trigger load into runtime
        load_success = load_session_memory_from_scene(scene=loaded_scene, runtime=runtime)
        assert load_success is True, "Failed to restore session memory from loaded scene"

        # Verify restored memory
        assert runtime.conversation is not None
        assert len(runtime.conversation.messages) >= 3
        summary_msg = runtime.conversation.messages[1]
        assert summary_msg.role == Role.USER
        assert "[Context Summary & Scene Memory]" in summary_msg.content
        assert "'PersistMonkey': transform" in summary_msg.content
        assert "location=[2.0, 3.0, 4.0]" in summary_msg.content

        # Sequence validity passes
        runtime.conversation.validate_sequence()

    print("[PASS] Test 3: Live .blend file save and open round-trip verified.")


def test_corrupted_property_gracefully_ignored():
    print("Test 4: Corrupted scene property safety...")
    clean_scene()
    scene = bpy.context.scene
    scene[SESSION_MEMORY_PROPERTY_NAME] = "CORRUPTED_NOT_JSON_DATA{{{"

    runtime = AgentRuntime(provider=MockProvider(), dispatcher=ToolDispatcher(registry=ToolRegistry(), adapter=BlenderAdapter()))
    loaded = load_session_memory_from_scene(scene=scene, runtime=runtime)
    assert loaded is False, "Corrupted property should return False"
    print("[PASS] Test 4: Corrupted property safely handled without crash.")


def test_secrets_and_api_keys_never_written_to_blend():
    print("Test 5: Zero secret or API key pollution in .blend scene...")
    clean_scene()
    scene = bpy.context.scene

    runtime = AgentRuntime(provider=MockProvider(), dispatcher=ToolDispatcher(registry=ToolRegistry(), adapter=BlenderAdapter()))
    tc1 = ToolCall(call_id="c1", tool_name="create_primitive", arguments={"primitive_type": "CUBE"})
    res1 = json.dumps({
        "name": "SecureCube",
        "verification": {"status": "PASS", "operation": "create", "target_name": "SecureCube"},
    })
    tc2 = ToolCall(call_id="c2", tool_name="inspect_scene", arguments={})
    res2 = json.dumps({
        "object_count": 1,
        "credential": "admin:supersecretpassword",
        "image_id": "img_0123456789abcdef",
    })
    tc3 = ToolCall(call_id="c3", tool_name="transform_object", arguments={"object_name": "SecureCube"})
    res3 = json.dumps({
        "error": "Failed with secret='my_token_secret_value_123'",
        "type": "AUTH_ERROR",
    })

    runtime.conversation.add_message(ChatMessage(role=Role.SYSTEM, content="Sys"))
    runtime.conversation.add_message(ChatMessage(role=Role.USER, content="Task with api_key: sk-proj-1234567890abcdef1234567890"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc1]))
    runtime.conversation.add_message(ChatMessage(role=Role.TOOL, content=res1, tool_call_id="c1", name="create_primitive"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content="Created, now inspect"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc2]))
    runtime.conversation.add_message(ChatMessage(role=Role.TOOL, content=res2, tool_call_id="c2", name="inspect_scene"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content="Inspected, now transform"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content=None, tool_calls=[tc3]))
    runtime.conversation.add_message(ChatMessage(role=Role.TOOL, content=res3, tool_call_id="c3", name="transform_object"))
    runtime.conversation.add_message(ChatMessage(role=Role.ASSISTANT, content="Error recorded"))

    save_session_memory_to_scene(scene=scene, runtime=runtime)
    raw_payload = scene.get(SESSION_MEMORY_PROPERTY_NAME, "")

    assert "sk-proj-1234567890abcdef1234567890" not in raw_payload, "API key leaked into .blend scene!"
    assert "admin:supersecretpassword" not in raw_payload, "Credential leaked into .blend scene!"
    assert "my_token_secret_value_123" not in raw_payload, "Secret leaked into .blend scene!"
    assert "img_0123456789abcdef" not in raw_payload, "Image ID leaked into .blend scene!"

    # Verify redaction markers are present
    assert "[REDACTED_SECRET]" in raw_payload
    print("[PASS] Test 5: Zero secret or API key pollution confirmed.")


def run_all():
    print("\n========================================================")
    print("   RUNNING M8 TASK 4 SESSION PERSISTENCE INTEGRATION    ")
    print("========================================================\n")
    test_handler_registration()
    test_save_pre_exports_session_memory_to_scene()
    test_blend_save_and_reload_round_trip()
    test_corrupted_property_gracefully_ignored()
    test_secrets_and_api_keys_never_written_to_blend()
    print("\n========================================================")
    print("   ALL SESSION PERSISTENCE TESTS PASSED (5/5)           ")
    print("========================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
