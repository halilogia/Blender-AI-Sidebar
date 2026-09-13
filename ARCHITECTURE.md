# Architecture & System Design — Blender AI Copilot

## 1. Core Architectural Invariants

Blender AI Copilot is designed around seven non-negotiable principles:

1. **Strict Thread Boundary**:
   - **Blender Main Thread**: The Blender Python API (`bpy`) is strictly single-threaded. Any access to datablocks, contexts, scenes, collections, or materials outside the main thread results in undefined behavior, memory corruption, or hard crashes. Therefore, all tool execution, scene mutations, undo pushes, viewport captures, and UI updates run exclusively on the main thread.
   - **Worker Background Thread**: Network requests (HTTP calls, Server-Sent Events parsing, socket I/O) are inherently blocking. All provider network I/O runs in a background thread (`AgentWorker`), ensuring 60 FPS viewport rendering and zero UI freezing.
2. **Zero Blender Dependencies in Core**:
   - All core components (`agent/`, `core/`, `tools/`) are pure Python standard library code.
   - Blender-specific access is entirely encapsulated within `adapter/blender_adapter.py` and `ui/`.
   - Pure Python components can be tested and verified in milliseconds outside Blender.
3. **Deterministic Grounding & Safe Mutations**:
   - Grounding tools (`tools/read_only/`) strictly observe scene state without modifying datablocks.
   - Mutation tools (`tools/mutations/`) execute localized, validated modifications primarily using Blender's Data API, strictly avoiding fragile screen context hacks.
4. **Lossless Atomic Undo Integration**:
   - Every mutation immediately issues `bpy.ops.ed.undo_push()`, seamlessly integrating with Blender's native `Ctrl+Z` / `Ctrl+Shift+Z` undo stack.
5. **Deterministic Approval Gate (No Blind Destructive Actions)**:
   - Tool execution risk is governed by a programmatic `ApprovalPolicy` (`RiskLevel.READ_ONLY`, `LOW`, `MEDIUM`, `HIGH`).
   - Destructive actions (such as `delete_object`) are trapped in `AgentState.PENDING_APPROVAL` and require explicit user approval via Viewport HUD or N-Panel before execution. The approval decision is never delegated to the LLM.
6. **Deterministic Mutation Verification (Closed-Loop Reality Check)**:
   - Every mutating tool invocation is verified deterministically by `ChangeVerifier` before results reach the LLM.
   - The expected state is derived strictly from tool call arguments, while the actual state is read directly from live Blender RNA datablocks.
   - If actual Blender state diverges from expected values beyond strict epsilon tolerances, the runtime halts with `VERIFICATION_FAILED`. The verification engine is 100% pure Python and completely free of `bpy` dependencies.
7. **OpenAI Chat Completions Protocol Standardization**:
   - The provider layer targets the standardized Chat Completions streaming protocol (`POST /v1/chat/completions`). Any OpenAI-compatible backend (9Router, LM Studio, Ollama, OpenRouter, OpenAI) is natively supported without proprietary hacks.

---

## 2. Component Hierarchy & Data Flow

```text
[ USER INPUT ] (HUD or N-Panel)
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
                     ├── 3. Evaluates ApprovalPolicy.evaluate(tool_call, tool)
                     │
                     ├── [IF REQUIRES APPROVAL (Risk >= MEDIUM)]:
                     │      ├── State transition: PROCESSING -> PENDING_APPROVAL
                     │      ├── Generates single-use cryptographic approval token
                     │      ├── Emits ApprovalRequiredEvent (HUD renders amber card)
                     │      └── AWAITS USER ACTION:
                     │             ├── [USER APPROVES]:
                     │             │      └── runtime.approve(token) -> transitions to EXECUTING_TOOL
                     │             └── [USER REJECTS]:
                     │                    └── runtime.reject(token) -> produces ToolResult.fail("USER_REJECTED")
                     │                        Records ChatMessage(role=TOOL) -> transitions to PROCESSING
                     │                        Re-submits to AgentWorker (LLM acknowledges rejection)
                     │
                     ├── [IF AUTO-APPROVED (Risk <= LOW)]:
                     │      └── State transition: PROCESSING -> EXECUTING_TOOL
                     │
                     ├── 4. ToolDispatcher.dispatch(tool_call) executes tool
                     │      └─> BlenderAdapter executes mutation on Main Thread
                     │          └─> Reader/Mutator captures live Blender snapshot (before/actual)
                     │          └─> push_undo_step() records atomic undo
                     ├── 5. Produces raw ToolResult with live RNA snapshot
                     ├── 6. build_change_set_from_result(tool_name, arguments, result_data) -> ChangeSet
                     ├── 7. ChangeVerifier.verify(change_set) [Pure Python, Zero bpy]
                     │      │
                     │      ├── [IF VERIFICATION PASSES]:
                     │      │      ├── Attaches verification metadata to ToolResult.ok
                     │      │      ├── Records ChatMessage(role=TOOL, content=JSON(data))
                     │      │      ├── State transition: EXECUTING_TOOL -> PROCESSING
                     │      │      ├── Builds updated ProviderRequestContext with verified results
                     │      │      └── Submits next turn task to AgentWorker (Next LLM Turn)
                     │      │
                     │      └── [IF VERIFICATION FAILS]:
                     │             ├── Produces ToolResult.fail("VERIFICATION_FAILED")
                     │             │   with exact property mismatches
                     │             ├── State transition: EXECUTING_TOOL -> ERROR
                     │             ├── Emits AgentErrorEvent
                     │             └── Safely terminates turn without sending unverified state to LLM
                     │
                     └── Next Turn Synthesis / Completion
                             │
                             ▼
                      [ AgentWorker ] (Next LLM Call)
                             │
                             └── Streams final assistant text synthesis
                                     │
                                     ▼
                      [ AgentRuntime.process_event ] (Main Thread)
                             │
                             ├── 1. Records final ChatMessage(role=ASSISTANT)
                             ├── 2. State transition: PROCESSING -> IDLE
                             ├── 3. Emits FinalResponseReadyEvent
                             └── 4. Syncs WindowManager properties, HUD drawer & UI history
```

---

## 3. Subsystem Breakdown

### 3.1. Thread-Safe Event Boundary (`core/`)
- **`ThreadSafeEventQueue`**: Thread-safe FIFO queue backed by `queue.Queue` with bounded batch draining (`drain_batch(max_items, max_time_sec)`). Guarantees the main thread timer never starves Blender's rendering loop.
- **`EventType` & Event Dataclasses**:
  - `PromptSubmittedEvent`: Signals start of generation.
  - `StreamingTextDeltaEvent`: Incremental text token received from streaming SSE.
  - `ProviderResponseReadyEvent`: Provider completed a generation step (contains `ProviderResponse` with assistant text or `tool_calls`).
  - `ApprovalRequiredEvent`: Dispatched when a destructive or medium/high-risk tool call requires explicit confirmation.
  - `ToolResultReadyEvent`: Dispatched on main thread when a tool completes execution.
  - `FinalResponseReadyEvent`: Signifies successful completion of entire turn.
  - `AgentErrorEvent`: Normalizes worker exceptions, provider HTTP errors, and tool failures.
  - `CancelRequestedEvent`: Signals turn cancellation.
  - `ShutdownEvent`: Signals extension unregistration and worker teardown.

### 3.2. Agent Runtime & State Machine (`agent/runtime.py`, `agent/state_machine.py`)
- **`AgentStateMachine`**: Enforces legal transitions:
  - `IDLE -> PROCESSING`
  - `PROCESSING -> EXECUTING_TOOL`
  - `PROCESSING -> PENDING_APPROVAL`
  - `PENDING_APPROVAL -> EXECUTING_TOOL` (approved)
  - `PENDING_APPROVAL -> PROCESSING` (rejected)
  - `EXECUTING_TOOL -> PROCESSING`
  - `PROCESSING -> IDLE`
  - Any state -> `ERROR`
  - `ERROR / ANY -> IDLE` (reset)
- **`AgentRuntime`**: Owns session state, turn lifecycle, active turn cancellation tokens, and the canonical `Conversation`. Enforces:
  - **Stale Event Protection**: Rejects events whose `turn_id` does not match the active `_current_turn_id`.
  - **Loop Guard**: `max_tool_rounds` (default: 5) prevents infinite LLM-tool ping-pong loops.
  - **Cancellation Hygiene**: Immediate abort of pending tools, invalidation of pending approvals, and discarding of late-arriving provider completions.
  - **Closed-Loop Verification Hook**: Integrates `_execute_and_verify` to intercept every mutation and validate actual Blender RNA state against expected parameters.

### 3.3. Deterministic Approval Gate (`agent/policy.py`)
- **`ApprovalPolicy`**: Programmatic gate that inspects `ToolCall` and the registered `BaseTool.risk_level`.
  - `RiskLevel.READ_ONLY`: Always auto-executed.
  - `RiskLevel.LOW`: Auto-executed (e.g. non-destructive transforms).
  - `RiskLevel.MEDIUM` & `HIGH`: Intercepted before dispatch (e.g. `delete_object`).
- **`PendingApproval`**: Immutable token container containing UUID `approval_id`, tool name, validated arguments, and a human-readable action description.
- **One-Time Token Semantics**: Upon execution (`runtime.approve`), the token is consumed and invalidated to prevent replay or double-execution bugs.

### 3.4. Safe Scene Mutations & Undo (`tools/mutations/`, `adapter/mutators/`)
- **`create_primitive`**: Spawns `CUBE`, `SPHERE`, or `PLANE` via Blender Data API with deterministic naming and placement.
- **`transform_object`**: Applies translation, rotation, and scaling coordinates to existing objects in absolute or relative coordinates.
- **`delete_object`**: Unlinks objects from all scenes and collections and purges their datablocks safely.
- **`set_material` (M6)**: Mutates Principled BSDF shader socket properties (`base_color`, `metallic`, `roughness`, `emission_color`, `emission_strength`, `alpha`). Automatically normalizes 3-element RGB to 4-element RGBA and clamps inputs outside [0, 1].
- **`assign_material` (M6)**: Binds an existing or newly created material to an object's material slot. Features automatic slot expansion when targeting higher slot indices.
- **`push_undo_step(description)`**: Invokes `bpy.ops.ed.undo_push()` after every successful mutation, integrating seamlessly into Blender's history.

### 3.5. Grounding Tools & Viewport Capture (`tools/read_only/`, `adapter/readers/`)
- **`BlenderAdapter`**: Strictly verifies execution on the Blender main thread (`threading.current_thread() == main_thread`). Exposes safe, read-only queries.
- **5 Non-Destructive Grounding Tools**:
  - `inspect_scene`: Hierarchy, active camera, render engine, counts.
  - `inspect_selection`: Current selection and active object transform.
  - `inspect_object`: Object metadata, transform matrices, modifier stack, material slots.
  - `inspect_material`: Principled BSDF shader parameters and material slots.
  - `inspect_mesh`: Topology diagnostics, vertex/edge/face counts, UV channels, world bounds.
- **`capture_viewport` (M7 Task 1)**:
  - Captures active 3D Viewport rendered state via `gpu.types.GPUOffScreen` with `do_color_management=True`.
  - In-memory pure Python PNG encoder (`encode_png_rgba`) using `zlib` and `struct`.
  - Zero scene mutation (no datablocks created, selection preserved).
  - Bounded in-memory LRU cache (max 10 images) returning machine-readable metadata (`image_id`, `width`, `height`, `format`, `mime_type`, `byte_size`) without polluting conversation logs.

### 3.6. Deterministic Mutation Verification Subsystem (`core/change_set.py`, `agent/verifier.py`)
- **Decoupled Pure Python Engine**: The verification engine has **zero `bpy` imports** and runs identically in unit tests and live Blender sessions.
- **`ChangeSet` Data Container**:
  - `operation`: Action name (`create`, `transform`, `delete`, `set_material`, `assign_material`).
  - `target_name`: Target object or material identifier.
  - `before`: Snapshot prior to mutation (captured by reader/mutator).
  - `expected_after`: Expected state derived directly from tool call parameters via `build_change_set_from_result`.
  - `actual_after`: Live snapshot read directly from Blender RNA datablocks post-mutation by reader/mutator.
- **Numeric & Geometric Tolerances**:
  - Floating point scalar and vector comparisons use strict epsilon tolerance (`1e-3`).
  - Rotational verification uses shortest angular difference with full Euler circular wrapping in `[-pi, pi]`.
- **Partial Material Verification**: Verifies only the explicitly mutated shader properties, ensuring default sockets do not trigger false failure positives.
- **Failure Isolation**: On divergence, produces `VERIFICATION_FAILED` result with granular mismatch reports (`property`, `expected`, `actual`), aborting turn execution before unverified state reaches the user or LLM.

### 3.7. Visual Scene Verification Subsystem (`agent/visual_verifier.py`, `tools/read_only/visual_verify.py`)
- **`VisualVerifier`**: Pure Python verification primitive that evaluates high-level natural language expectations against rendered 3D Viewport images.
- **Hierarchical Verification Safety**: Deterministic RNA semantic verification (`ChangeVerifier`) remains the primary, authoritative gatekeeper. Visual verification is skipped if semantic verification fails.
- **Non-Destructive Outcomes**: Structured outcomes (`PASS`, `FAIL`, `UNCERTAIN`) act as a secondary sanity signal. A visual `FAIL` flags visual discrepancy warnings in tool and history records without triggering automated rollback of verified Blender state.
- **`VisualResultParser`**: Robust, fault-tolerant parser extracting structured decisions from model output. Gracefully treats malformed JSON, markdown fences, unrecognized status tokens, or empty strings as `UNCERTAIN` without throwing unhandled exceptions.
- **`VisualVerifyTool`**: Semantic read-only tool (`RiskLevel.READ_ONLY`) allowing the agent to capture the active viewport and request visual verification on demand.
- **Zero Byte/Base64 Leaks**: History and serializations (`VisualVerificationResult.to_dict()`) store only `image_id` references, never raw bytes or base64 dumps.

### 3.8. Provider Protocol Engine (`agent/openai_provider.py`, `agent/sse_parser.py`, `agent/http_client.py`)
- **`HttpClient`**: Pure Python streaming HTTP client using `urllib.request`. Reads responses in arbitrary byte chunks supporting immediate abort via `cancel_event`.
- **`SSEParser`**: Deterministic byte-level Server-Sent Events parser adhering to the W3C EventSource specification. Handles arbitrary chunk fragmentation across character boundaries with strict event size guards.
- **`ToolCallAccumulator`**: Reassembles fragmented streaming tool-call deltas into complete, validated `ToolCall` objects.
- **`ContextBuilder`**: Assembles system prompt, conversation history, and tool schemas into an immutable `ProviderRequestContext` while enforcing character safety caps (`MAX_CONTEXT_CHARS=15000`). Resolves in-memory PNG bytes from `adapter.get_viewport_screenshot` strictly on the main thread.
- **`OpenAIRequestMapper` (M7 Task 2)**: Maps internal messages to OpenAI Chat Completions payload format. For multimodal messages referencing `image_id`, formats content parts with `image_url` data URIs (`data:image/png;base64,...`) entirely in memory.
- **Multimodal Capability Gate (M7 Task 2)**: When `supports_multimodal=False`, deterministically aborts image dispatch with `PROVIDER_UNSUPPORTED` / `MultimodalUnsupportedError`.

### 3.8. Native Dual User Interface (`ui/`)
- **`GPU Viewport Overlay` (`ui/gpu_overlay/`)**: Floating HUD rendered directly on Blender's 3D Viewport framebuffer (`SpaceView3D.draw_handler_add` with `POST_PIXEL`). Features anti-aliased rounded box geometry, multi-pass drop shadow, blinking cursor, full Turkish/Unicode text editing, interactive Approve/Reject buttons, hotkey triggering (`Alt+Space`), and in-scene assistant drawer with zero external C++ dependencies.
- **`N-Panel Sidebar` (`ui/panel.py`, `ui/uilist.py`)**: Persistent 3D Viewport sidebar panel providing full conversation history, token metrics, and manual approval controls.
- **`TimerBridge`**: Registers with `bpy.app.timers`. Each tick drains up to `max_events_per_tick` (10) within `max_tick_seconds` (5 ms), processes events on the main thread, synchronizes `WindowManager` RNA properties, and tags visible viewports for redraw.
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
| **Destructive Action Intercepted**| `ApprovalPolicy` gate | Transitions to `PENDING_APPROVAL`. Tool will not execute until explicit user confirmation. |
| **User Rejection of Action** | `AgentRuntime.reject` | Emits `ToolResult.fail("USER_REJECTED")`. Agent informs LLM; conversation proceeds normally. |
| **Invalid / Expired Approval** | `AgentRuntime.approve` | Rejects stale or duplicate execution attempts. No orphaned operations. |
| **Mutation Outcome Divergence** | `ChangeVerifier` -> `AgentRuntime` | Returns `ToolResult.fail("VERIFICATION_FAILED")` with mismatch details. Transitions to `AgentState.ERROR`. |
| **Viewport Render Unavailable** | `BlenderAdapter.capture_viewport` | Returns `ToolResult.fail("VIEWPORT_UNAVAILABLE")`. Catches missing viewport or GPU offscreen error safely. |
| **Unsupported Multimodal Model** | `OpenAICompatibleProvider` / `OpenAIRequestMapper` | Emits `ProviderError(PROVIDER_UNSUPPORTED)` -> `AgentErrorEvent("PROVIDER_UNSUPPORTED")`. Transitions to `AgentState.ERROR`. |
| **Infinite Tool Call Loop** | `AgentRuntime._current_tool_round` | Triggers `MAX_TOOL_ROUNDS_EXCEEDED` when `_current_tool_round > max_tool_rounds`. Transitions to `ERROR`. |
| **User Turn Cancellation** | `AgentRuntime.cancel_current_turn` | Sets `cancel_event`, drops worker stream, invalidates `turn_id` and approvals, resets state to `IDLE`. |

---

## 5. Security & Privacy Guarantees

Blender AI Copilot is designed around six non-negotiable principles:
1. **Zero External Runtime Dependencies**: Standard library Python (`urllib`, `json`, `threading`, `queue`, `zlib`, `struct`) + Blender Native API (`bpy`, `gpu`, `gpu_extras`). No Chromium, no node.js, no binary wheel sidecars.
2. **Key Hygiene**:
   - API keys are never written to source control.
   - Keys are kept in memory only as long as necessary.
   - Masked strings (`sk-1...abcd`) are used in history and serialization.
3. **Deterministic Human-in-the-Loop Gate**: Destructive actions (such as deleting objects) are mathematically prevented from firing without manual confirmation, protecting artists against accidental scene corruption or hallucinations.
4. **Lossless Recovery**: Every mutation is atomic and recorded in Blender's undo buffer, guaranteeing that the artist can undo any AI action with a single keystroke (`Ctrl+Z`).
5. **Deterministic Verification Gate**: Mutations are guaranteed to conform to expected geometric and shader parameters through live RNA inspection, preventing silent scene state drift.
6. **In-Memory Multimodal Privacy**: Viewport screenshots are never written to disk as temporary files. Raw PNG bytes and base64 payloads are excluded from ChatMessage serialization and history logs, preventing disk leaks and UI performance degradation.
