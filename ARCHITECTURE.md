# Architecture & System Design — Blender AI Sidebar

## 1. Core Architectural Invariants

Blender AI Sidebar is designed around five non-negotiable principles:

1. **Strict Thread Boundary**:
   - **Blender Main Thread**: The Blender Python API (`bpy`) is strictly single-threaded. Any access to datablocks, contexts, scenes, collections, or materials outside the main thread results in undefined behavior, memory corruption, or hard crashes. Therefore, all tool execution and UI updates run exclusively on the main thread.
   - **Worker Background Thread**: Network requests (HTTP calls, Server-Sent Events parsing, socket I/O) are inherently blocking. All provider network I/O runs in a background thread (`AISidebarWorker`), ensuring 60 FPS viewport rendering and zero UI freezing.
2. **Zero Blender Dependencies in Core**:
   - All core components (`agent/`, `core/`, `tools/`) are pure Python standard library code.
   - Blender-specific access is entirely encapsulated within `adapter/blender_adapter.py` and `ui/`.
   - Pure Python components can be tested and verified in milliseconds outside Blender.
3. **Deterministic Non-Destructive Grounding**:
   - Grounding tools (`tools/read_only/`) strictly observe scene state without modifying datablocks.
   - Tool outputs are structured, deterministic, and JSON-serializable dictionaries.
4. **Canonical Event-Driven Messaging**:
   - No direct cross-thread function calls. The worker thread communicates with the main thread strictly by enqueueing immutable event objects into `ThreadSafeEventQueue`.
5. **OpenAI Chat Completions Protocol Standardization**:
   - The provider layer targets the standardized Chat Completions streaming protocol (`POST /v1/chat/completions`). Any OpenAI-compatible backend (9Router, LM Studio, Ollama, OpenRouter, OpenAI) is natively supported without proprietary hacks.

---

## 2. Component Hierarchy & Data Flow

```text
[ USER INPUT ]
     │
     ▼
[ AgentRuntime.submit_prompt ] (Main Thread)
     │
     ├── 1. Allocates monotonic turn_id ("turn_1")
     ├── 2. State transition: IDLE -> PROCESSING
     ├── 3. Records ChatMessage(role=USER, content=...) in Conversation
     ├── 4. Builds initial ProviderRequestContext via ContextBuilder
     └── 5. Enqueues generation task to AgentWorker
             │
             ▼
      [ AgentWorker ] (Background Thread)
             │
             ├── Calls OpenAICompatibleProvider.stream_chat(context, turn_id)
             │      │
             │      ├── POST /v1/chat/completions (stream=True) via HttpClient
             │      ├── Receives raw byte chunks from socket
             │      ├── Chunks parsed by SSEParser into data payloads
             │      ├── Text deltas streamed as StreamingTextDeltaEvent
             │      ├── Tool call deltas accumulated in ToolCallAccumulator
             │      └── On finish_reason="tool_calls", completes reassembly
             │
             └── Enqueues ProviderResponseReadyEvent(tool_calls=[...]) to queue
                     │
                     ▼
             [ TimerBridge.tick ] (Main Thread / bpy.app.timers)
                     │
                     ▼
             [ AgentRuntime.process_event ] (Main Thread)
                     │
                     ├── 1. Loop Guard: checks _current_tool_round <= max_tool_rounds
                     ├── 2. Records ChatMessage(role=ASSISTANT, tool_calls=...)
                     ├── 3. State transition: PROCESSING -> EXECUTING_TOOL
                     ├── 4. ToolDispatcher.dispatch(tool_call) executes tool
                     │         └─> BlenderAdapter reads bpy datablocks
                     ├── 5. Produces standardized ToolResult
                     ├── 6. Records ChatMessage(role=TOOL, content=JSON(data))
                     ├── 7. State transition: EXECUTING_TOOL -> PROCESSING
                     ├── 8. Builds updated ProviderRequestContext with tool results
                     └── 9. Submits next turn task to AgentWorker
                             │
                             ▼
                      [ AgentWorker ] (2nd LLM Call)
                             │
                             └── Streams final assistant text synthesis
                                     │
                                     ▼
                      [ AgentRuntime.process_event ] (Main Thread)
                             │
                             ├── 1. Records final ChatMessage(role=ASSISTANT)
                             ├── 2. State transition: PROCESSING -> IDLE
                             ├── 3. Emits FinalResponseReadyEvent
                             └── 4. Syncs WindowManager properties & UI history
```

---

## 3. Subsystem Breakdown

### 3.1. Thread-Safe Event Boundary (`core/`)
- **`ThreadSafeEventQueue`**: Thread-safe FIFO queue backed by `queue.Queue` with bounded batch draining (`drain_batch(max_items, max_time_sec)`). Guarantees the main thread timer never starves Blender's rendering loop.
- **`EventType` & Event Dataclasses**:
  - `PromptSubmittedEvent`: Signals start of generation.
  - `StreamingTextDeltaEvent`: Incremental text token received from streaming SSE.
  - `ProviderResponseReadyEvent`: Provider completed a generation step (contains `ProviderResponse` with assistant text or `tool_calls`).
  - `ToolResultReadyEvent`: Dispatched on main thread when a tool completes execution.
  - `FinalResponseReadyEvent`: Signifies successful completion of entire turn.
  - `AgentErrorEvent`: Normalizes worker exceptions, provider HTTP errors, and tool failures.
  - `CancelRequestedEvent`: Signals turn cancellation.
  - `ShutdownEvent`: Signals extension unregistration and worker teardown.

### 3.2. Agent Runtime & State Machine (`agent/runtime.py`, `agent/state_machine.py`)
- **`AgentStateMachine`**: Enforces legal transitions:
  - `IDLE -> PROCESSING`
  - `PROCESSING -> EXECUTING_TOOL`
  - `EXECUTING_TOOL -> PROCESSING`
  - `PROCESSING -> IDLE`
  - Any state -> `ERROR`
  - `ERROR / ANY -> IDLE` (reset)
- **`AgentRuntime`**: Owns session state, turn lifecycle, active turn cancellation tokens, and the canonical `Conversation`. Enforces:
  - **Stale Event Protection**: Rejects events whose `turn_id` does not match the active `_current_turn_id`.
  - **Loop Guard**: `max_tool_rounds` (default: 5) prevents infinite LLM-tool ping-pong loops.
  - **Cancellation Hygiene**: Immediate abort of pending tools and discarding of late-arriving provider completions.

### 3.3. Provider Protocol Engine (`agent/openai_provider.py`, `agent/sse_parser.py`, `agent/http_client.py`)
- **`HttpClient`**: Pure Python streaming HTTP client using `urllib.request`. Reads responses in arbitrary byte chunks (e.g. 1024-4096 bytes) supporting immediate abort via `cancel_event`.
- **`SSEParser`**: Deterministic byte-level Server-Sent Events parser adhering to the W3C EventSource specification. Handles arbitrary chunk fragmentation across character boundaries (multibyte UTF-8, LF, CRLF, `:` split, double newline delimiters) with strict event size guards (`max_event_size=65536`).
- **`ToolCallAccumulator`**: Reassembles fragmented streaming tool-call deltas (`index`, `id`, `name`, `arguments`) into complete, validated `ToolCall` objects.
- **`ContextBuilder`**: Assembles system prompt, conversation history, and tool schemas into an immutable `ProviderRequestContext` while enforcing character safety caps (`MAX_CONTEXT_CHARS=15000`).

### 3.4. Grounding Tools & Adapter (`tools/`, `adapter/`)
- **`BlenderAdapter`**: Strictly verifies execution on the Blender main thread (`threading.current_thread() == main_thread`). Exposes safe, read-only queries for:
  - Scene summary (`get_scene_summary`)
  - Selection hierarchy (`get_selection`)
  - Object metadata, parentage, and modifier stack (`get_object_details`)
  - Shader nodes & Principled BSDF values (`get_material_details`)
  - Mesh topology, UV channels, and world bounds (`get_mesh_details`)
- **`ToolRegistry` & `ToolDispatcher`**: Validates incoming tool calls against JSON Schema definitions (`required`, `properties`, `additionalProperties: false`) before execution.

### 3.5. Native UI & Timer Bridge (`ui/`)
- **`TimerBridge`**: Registers with `bpy.app.timers`. Each tick drains up to `max_events_per_tick` (10) within `max_tick_seconds` (5 ms), processes events on the main thread, synchronizes `WindowManager` RNA properties, and calls `tag_redraw()` on visible 3D Viewports.
- **`AddonPreferences`**: Securely handles user configuration (`base_url`, `model`, `api_key`, `timeout_seconds`), automatically saves to user resource directory (`config.json`), and respects environment variable overrides (`OPENAI_BASE_URL`, `BLENDER_AI_API_KEY`).

---

## 4. Error Handling & Resilience Matrix

| Failure Mode | Detection Layer | Handling & Recovery |
| :--- | :--- | :--- |
| **HTTP 401 / 403 (Auth)** | `HttpClient` -> `OpenAICompatibleProvider` | Normalized to `ProviderError(AUTH_ERROR)` -> `AgentErrorEvent` -> `AgentState.ERROR`. UI displays error; user updates key in Preferences. |
| **HTTP 429 (Rate Limit)** | `HttpClient` -> `OpenAICompatibleProvider` | Normalized to `ProviderError(RATE_LIMIT)`. Safe error response; no retry storms. |
| **Connection Refused / Offline** | `HttpClient` (urllib.error.URLError) | Normalized to `ProviderError(NETWORK_ERROR)`. Clear diagnostic message in UI. |
| **Malformed SSE / Bad JSON** | `SSEParser` / `ToolCallAccumulator` | Emits `ProviderError(INVALID_RESPONSE)` or `TOOL_CALL_PARSE_ERROR`. Discards corrupted stream. |
| **Missing / Invalid Tool Arg** | `ToolDispatcher` schema validator | Returns structured `ToolResult.fail("INVALID_ARGUMENT")`. Sent back to LLM for correction. |
| **Non-existent Blender Object** | `BlenderAdapter` object lookup | Returns structured `ToolResult.fail("OBJECT_NOT_FOUND")`. Handled gracefully without Python exception. |
| **Cross-Thread Access Attempt** | `BlenderAdapter` thread guard | Raises `ThreadSafetyViolationError`. Aborts operation immediately before datablock corruption. |
| **Infinite Tool Call Loop** | `AgentRuntime._current_tool_round` | Triggers `MAX_TOOL_ROUNDS_EXCEEDED` when `_current_tool_round > max_tool_rounds`. Transitions to `ERROR`. |
| **User Turn Cancellation** | `AgentRuntime.cancel_current_turn` | Sets `cancel_event`, drops worker stream, invalidates `turn_id`, resets state to `IDLE`. |

---

## 5. Security & Privacy Guarantees

1. **Localhost Network Policy**: Local loopback endpoints (`http://localhost:*`, `http://127.0.0.1:*`) are permitted without restriction, enabling completely offline, local AI operation (e.g. via local 9Router, Ollama, LM Studio).
2. **Credential Hygiene**:
   - API keys are marked as `PASSWORD` RNA subtype in Blender UI.
   - Keys are never logged in plain text or saved to `.blend` files.
   - Masked strings (`sk-1...abcd`) are used in history and serialization.
3. **No Unapproved Mutations**: Grounding tools are strictly read-only.
