# Project Engineering Knowledge Base — Blender AI Sidebar

This document serves as the persistent engineering knowledge repository for **Blender AI Sidebar**. Any developer or AI agent modifying this codebase must adhere strictly to the invariants, patterns, and lessons documented here.

---

## 1. Architectural Invariants (Non-Negotiable)

### 1.1. Main Thread vs Worker Thread Boundary
- **Blender Python API (`bpy`) is strictly single-threaded.**
  - Accessing any `bpy.data`, `bpy.context`, or `bpy.ops` from a background thread will corrupt Blender's memory space and cause segmentation faults.
  - **Rule**: `BlenderAdapter` must assert `threading.current_thread() == self._main_thread`. If called off-thread, it raises `ThreadSafetyViolationError`.
  - **Rule**: Tool execution (`dispatcher.dispatch()`) must ONLY happen on the main thread inside `AgentRuntime.process_event()`, driven by `TimerBridge.tick()`.
  - **Rule**: Network operations (HTTP requests, SSE streaming, sockets) must ONLY run in the background worker (`AgentWorker`), never on the main thread.

### 1.2. Zero External Dependencies (Pure Python Standard Library)
- Blender extensions must run seamlessly across any user installation without requiring `pip install` or external binaries.
- All code in `adapter/`, `agent/`, `core/`, and `tools/` relies exclusively on Python standard library modules (`urllib.request`, `http.client`, `json`, `threading`, `queue`, `dataclasses`, `pathlib`, `typing`, `unittest`).

### 1.3. Non-Destructive Grounding (Grounding Tools)
- Grounding tools (`tools/read_only/`) must NEVER mutate scene state, transform objects, add keyframes, or alter materials.
- Return values must be structured, deterministic `ToolResult` instances with serializable dictionaries.
- Missing entities (e.g. object not found, material slot empty) must return structured `ToolResult.fail(...)` rather than raising uncaught exceptions.

### 1.4. OpenAI-Compatible Protocol Standardization
- Target the standardized `POST /v1/chat/completions` API (`stream=True`).
- Do NOT introduce vendor-specific adapters or special cases (e.g. do not write custom code for 9Router, LM Studio, or Ollama). The system works through standardized OpenAI Chat Completions formatting.
- `OpenAIRequestMapper` normalizes `ProviderRequestContext` into OpenAI's payload format with `tools` and `parallel_tool_calls: False`.
- In streaming, `ToolCallAccumulator` aggregates fragmented deltas indexed by `index` and finalizes them upon `ProviderCompleted`.

---

## 2. Key Component Mechanics

### 2.1. ThreadSafeEventQueue & TimerBridge
- `ThreadSafeEventQueue` wraps `queue.Queue` with bounded batch draining (`drain_batch`).
- `TimerBridge` binds to `bpy.app.timers`.
  - **Critical Quirk**: Creating bound methods like `self._timer_callback` repeatedly generates new object references, which can cause `bpy.app.timers.unregister` to fail.
  - **Pattern**: Store `self._callback_ref = self._timer_callback` in `__init__` and pass `self._callback_ref` to both `register()` and `unregister()`.
  - Timer callbacks must return `poll_interval` (e.g. 0.02s) to reschedule, or `None` to unregister.

### 2.2. SSEParser Implementation
- TCP/HTTP chunk boundaries never align with event boundaries. A single chunk may contain half a UTF-8 character, half a line, or multiple SSE events.
- `SSEParser` operates directly on raw `bytes`.
- Maintains an internal byte buffer, searching for `\n\n` or `\r\n\r\n` event separators.
- Strips only the first space after colon per W3C EventSource spec: `data: hello` -> `hello`, `data:  hello` -> ` hello`.
- Recognizes `[DONE]` sentinel string.
- Enforces `max_event_size` (64 KB) to protect against memory exhaustion from misconfigured servers.

### 2.3. ToolCallAccumulator
- LLMs emit tool calls as incremental chunks:
  - Chunk 1: `tool_calls: [{"index": 0, "id": "call_123", "function": {"name": "inspect_"}}]`
  - Chunk 2: `tool_calls: [{"index": 0, "function": {"name": "scene"}}]`
  - Chunk 3: `tool_calls: [{"index": 0, "function": {"arguments": "{\"foo\":"}}]`
  - Chunk 4: `tool_calls: [{"index": 0, "function": {"arguments": "\"bar\"}"}}]`
- `ToolCallAccumulator` maps by integer `index`.
- Concatenates name fragments and arguments fragments.
- Validates on `finalize()`: verifies non-empty `call_id`, non-empty `tool_name`, and parses `arguments` as a valid JSON dictionary.

### 2.4. ContextBuilder & Conversation
- `Conversation` is an ordered list of `ChatMessage` objects.
- Supported roles: `SYSTEM`, `USER`, `ASSISTANT`, `TOOL`.
- `validate_sequence()` ensures tool call round-trip consistency:
  - An `ASSISTANT` message requesting tool calls must be followed by `TOOL` messages matching every requested `call_id`.
  - No new non-tool messages may appear while tool calls remain pending.
- `ContextBuilder.build(...)` enforces a safety limit on prompt length (`MAX_CONTEXT_CHARS=15000`) to prevent context overflow.

### 2.5. Loop Guards & Cancellation
- `AgentRuntime.max_tool_rounds` (default: 5) prevents infinite LLM tool-calling loops.
  - When `_current_tool_round > max_tool_rounds`, the runtime transitions to `AgentState.ERROR` with `MAX_TOOL_ROUNDS_EXCEEDED` and halts.
- In-flight cancellation sets `threading.Event` on the active turn.
  - `HttpClient` immediately closes the connection socket.
  - `AgentRuntime.process_event` drops any late-arriving completion events and never runs pending tools after cancellation.
  - Stale events with mismatched `turn_id` are tracked in `_stale_events_count` and safely dropped.

---

## 3. Blender 5.2 Extension Specifics

### 3.1. Manifest Configuration (`blender_manifest.toml`)
- Requires `schema_version = "1.0.0"`.
- Must specify `type = "add-on"`.
- Uses `id = "blender_ai_sidebar"` and `version = "0.2.0"`.
- Tagged with `"3D View"`, `"AI"`, `"Pipeline"`.

### 3.2. Addon Preferences & Security
- Addon preferences class inherits from `bpy.types.AddonPreferences`.
- `bl_idname` must match the top-level addon package name.
- `api_key` property uses `subtype='PASSWORD'` to prevent shoulder-surfing in the Blender interface.
- Configuration is saved to `bpy.utils.user_resource('CONFIG') / 'blender_ai_sidebar' / 'config.json'`.
- Environment variables (`OPENAI_BASE_URL`, `BLENDER_AI_API_KEY`, etc.) override file settings.

---

## 4. Testing & Verification Standard

1. **Pure Python Unit Tests**:
   - Must run completely offline without Blender installed: `python tests/run_unit_tests.py`.
   - Uses test doubles (`DummySceneTool`, local `HTTPServer` on port 0) to verify full protocol round-trips.
2. **Headless Blender Integration Tests**:
   - Must execute in headless Blender: `blender.exe --background --python tests/run_all_blender_tests.py`.
   - Validates live Blender datablock reading (`bpy.data.scenes`, `bpy.data.objects`), UIList collections, and timer pumps.
3. **Always Return Non-Zero Exit Code on Test Failure**:
   - Blender headless scripts do not automatically exit with a non-zero exit code if an `assert` fails inside a custom function unless caught and passed to `sys.exit(1)`.
   - Wrap headless integration test runners with `try...except Exception: sys.exit(1)`.
