# Changelog — Blender AI Copilot

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] - M4.2 High-Level Plan Review

### Added
- **M4.2: Structured Plans, Validation, Execution, Batch Approval**:
  - Frozen `Plan` / `PlanStep` models with validated-snapshot immutability.
  - Strict `PlanValidator`: unknown-tool, argument schema, dependency, cycle and `propose_plan`-nesting rejection; deterministic topological order.
  - `propose_plan` meta-tool for declarative plan intake (no scene mutation).
  - `PlanExecutor` orchestration layer over existing ToolDispatcher plus semantic verification and explicit visual-verification hooks; fail-fast step execution with `PlanExecutionSummary`.
  - `PlanReview` single batch-approval gate: no step executes before approval; approve runs immutable plan once; reject runs zero mutations; single-use, stale-turn/cancellation protected.
  - Approved plan suppresses per-step re-approval; standalone tool approval path unchanged.
  - Risk derived from registry/tool metadata; LLM `overall_risk` ignored.

---

## [0.7.0] - 2026-09-13

### Added
- **M7 Task 3: Visual Scene Verification**:
  - `VisualVerifier` (`agent/visual_verifier.py`) evaluating viewport state against high-level natural language prompt intent.
  - Hierarchical verification: deterministic RNA semantic verification (`ChangeVerifier`) is authoritative; visual verification is skipped if semantic verification fails.
  - Deterministic structured output format (`PASS`, `FAIL`, `UNCERTAIN` + concise rationale).
  - Fault-tolerant `VisualResultParser` handling malformed JSON, markdown fences, unrecognized status values, or empty strings gracefully without unhandled exceptions.
  - Read-only semantic tool `visual_verify` (`tools/read_only/visual_verify.py`) registered in `ToolRegistry` (`RiskLevel.READ_ONLY`).
  - Safe failure policy: `FAIL` and `UNCERTAIN` report visual discrepancies without triggering automatic mutation rollbacks.
  - Strict thread isolation: viewport capture and screenshot resolution remain on Blender main thread; worker thread handles HTTP/JSON/SSE.
  - Zero raw image bytes or base64 strings in history logs or serialized results (`to_dict()`).
  - Automatic post-mutation visual verification wired into `AgentRuntime._execute_and_verify` when visual confirmation is requested by user prompt or runtime expectation.
  - Added unit test suite `tests/unit/test_visual_verifier.py` (unit tests expanded to 375 tests).
  - Added Blender headless integration suite `tests/integration/test_visual_verification_integration.py` (master suite expanded to 17 suites).
- **M7 Task 2: Multimodal Provider Integration**:
  - `ChatMessage` and `ProviderRequestContext` updated to support in-memory image attachments (`image_id` and `images: Mapping[str, bytes]`).
  - `ContextBuilder.build` resolves raw PNG bytes on the main thread via `adapter.get_viewport_screenshot(image_id)` before worker task dispatch.
  - Background worker thread isolation: worker strictly handles HTTP/JSON/SSE off the main thread without touching `bpy` or `gpu`.
  - `OpenAIRequestMapper` maps messages with images into OpenAI-compatible `image_url` data URIs (`data:image/png;base64,...`) entirely in memory.
  - In-memory privacy: zero temporary PNG files written to disk; raw image bytes and base64 strings excluded from history logs and message serialization.
  - Deterministic capability gate: `supports_multimodal=False` deterministically yields `PROVIDER_UNSUPPORTED` / `MultimodalUnsupportedError`.
  - Added `tests/unit/test_multimodal_provider.py` (unit tests expanded to 335 tests).
  - Added `tests/integration/test_multimodal_integration.py` (master Blender integration test suite expanded to 16 suites).
- **M7 Task 1: Viewport Screenshot Capture Primitive**:
  - `capture_viewport` read-only semantic tool (`RiskLevel.READ_ONLY`) in `tools/read_only/capture_viewport.py`.
  - `ViewportReader` in `adapter/readers/viewport_reader.py`:
    - Resolves active 3D Viewport in Blender context and fallback screens.
    - Offscreen GPU framebuffer rendering via `gpu.types.GPUOffScreen` with `do_color_management=True`.
    - Pure Python in-memory PNG encoder (`encode_png_rgba`) using `zlib` and `struct` (zero external dependencies).
    - Bounded in-memory LRU cache (max 10 images) mapping `image_id` to raw PNG bytes.
    - Zero filesystem writes, zero scene contamination (objects, meshes, materials, images, and selection unchanged).
  - Main-thread execution enforcement (`ThreadSafetyViolationError` on cross-thread calls).
  - Clean metadata contract preventing conversation log and history pollution.
  - Added `tests/unit/test_capture_viewport.py` (unit tests expanded to 320 tests).
  - Added `tests/integration/test_viewport_capture.py` (master Blender integration test suite expanded to 15 suites).

---

## [0.6.0] - 2026-09-13

### Added
- **M6: Materials & Shader Tools**:
  - `set_material`: Mutates Principled BSDF shader socket properties (`base_color`, `metallic`, `roughness`, `emission_color`, `emission_strength`, `alpha`).
  - `assign_material`: Binds existing or newly created materials to object material slots with automatic slot expansion.
  - Normalization & clamping: 3-element RGB automatically converted to 4-element RGBA; out-of-range scalars/colors clamped safely to [0, 1].
  - Deterministic material verification in `ChangeVerifier` and `build_change_set_from_result`.
  - Partial verification support: modifying a single property verifies without failing on untouched default sockets.
  - Lossless shader undo/redo: all material mutations record atomic undo steps via `push_undo_step()`.
  - Headless integration test suite (`tests/integration/test_material_mutations.py`) validating all 10 acceptance scenarios including genuine RNA divergence detection (`test_04_real_verification_fail`).

---

## [0.5.0] - 2026-09-13

### Added
- **M5: Deterministic Mutation Verification & Change Sets**:
  - `ChangeSet` immutable data model capturing `operation`, `target_name`, `before`, `expected_after`, and `actual_after`.
  - Pure Python `ChangeVerifier` engine (100% standard library, zero `bpy` dependencies).
  - `build_change_set_from_result` mapper deriving expected states directly from tool call parameters.
  - Floating point and rotational epsilon tolerances (`EPSILON=1e-3`, Euler circular wrapping in `[-pi, pi]`).
  - Verification integration in `AgentRuntime._execute_and_verify`.
  - Structured failure handling: divergence produces `VERIFICATION_FAILED` error code, detailed property mismatches, and safe turn termination.
  - Headless integration test suite (`tests/integration/test_verification_integration.py`).

---

## [0.4.0] - 2026-09-13

### Added
- **M4.1: Deterministic Approval Gate**:
  - `ApprovalPolicy` providing central risk-based execution gating (`READ_ONLY`/`LOW` auto-approve; `MEDIUM`/`HIGH`/`CRITICAL` require explicit user confirmation).
  - Pure Python immutable `PendingApproval` container with cryptographic UUID tokens (`approval_id`), frozen `tool_call` parameters, and human-readable descriptions.
  - Runtime execution interception in `AgentRuntime`: even if the LLM provider directly emits destructive tool calls without conversational confirmation, execution is deterministically trapped in `AgentState.PENDING_APPROVAL`.
  - `runtime.approve(approval_id)`: Dispatches tool exactly once and invalidates token against duplicate execution.
  - `runtime.reject(approval_id)`: Guarantees tool is never dispatched, producing controlled `USER_REJECTED` outcome returned to conversation.
  - Cancellation safety: `cancel_current_turn()` immediately invalidates pending approvals.
- **Viewport GPU Overlay Approval Card**:
  - In-viewport floating card with amber warning styling (`⚠ Confirm Action`), object action descriptions, and risk badge.
  - Interactive `[ Reject (N) ]` and `[ Approve (Y) ]` buttons with hover feedback.
  - Full keyboard shortcut support: `Y` / `A` / `Enter` to approve, `N` / `R` / `Esc` to reject.
  - Fixed Blender input event handling for `BACK_SPACE` key and added `Ctrl+Backspace` word deletion support.
- **Verification & Testing**:
  - Added `tests/unit/test_approval_gate.py` verifying all 12 approval security invariants (unit test suite expanded to 252 tests).
  - Added `tests/integration/test_approval_integration.py` confirming live object deletion and rejection semantics under headless Blender (integration test suite expanded to 12 suites).
  - Full manual validation completed on real Blender GUI with live 9Router LLM provider.

---

## [0.3.0] - 2026-09-13

### Added
- **M3.1: Safe Mutation & Undo Foundation**:
  - `create_primitive`: Generates `CUBE`, `SPHERE`, or `PLANE` with deterministic names and dimensions via Data API.
  - `transform_object`: Translates, rotates, and scales existing objects in absolute or relative coordinates.
  - `delete_object`: Safely unlinks and purges targeted objects by exact name with `ObjectNotFoundError` fail-safes.
  - Atomic Undo: Integrated `push_undo_step()` into all mutation mutators, enabling lossless `Ctrl+Z` / `Ctrl+Shift+Z` native Blender undo operations.
  - Strict thread-safety guards ensuring all scene mutations execute exclusively on Blender's main thread.
- **In-Viewport Native GPU HUD (M2.9)**:
  - Floating HUD drawn directly in the 3D Viewport via Blender `gpu` and `blf` APIs (Higgsfield-inspired floating bar, ~0 MB added binary size).
  - Text editing buffer, cursor blink, Turkish and Unicode input support, responsive layout, and `Alt+Space` modal toggle operator.
- **Production Provider Wiring**:
  - Connected production addon initialization to live `OpenAICompatibleProvider` driven by user preferences (`base_url`, `model`, `api_key`).

---

## [0.2.0] - 2026-09-07

### Added
- **OpenAI-Compatible Streaming Provider Engine**:
  - Pure Python `HttpClient` using `urllib.request` with streaming bytes reading, auth header hygiene, and instant cancellation support.
  - Pure Python `SSEParser` adhering to W3C EventSource standard, supporting arbitrary chunk fragmentation, UTF-8 boundary handling, LF/CRLF line endings, and `[DONE]` sentinels.
  - Pure Python `ToolCallAccumulator` reassembling streaming fragmented tool-call deltas into validated `ToolCall` instances.
  - `OpenAICompatibleProvider` connecting to standard `/v1/chat/completions` endpoints.
  - `ContextBuilder` mapping internal `Conversation` and registered tools to OpenAI function schemas with context character safety cap (`MAX_CONTEXT_CHARS=15000`).
- **Full AgentRuntime Tool Round-Trip (M2.7)**:
  - User Prompt -> LLM streaming -> `tool_calls` -> Main Thread `ToolDispatcher` -> `ToolResult` -> `Conversation` -> 2nd LLM call -> Final Assistant Response.
  - Multi-round sequential tool execution within a single user turn.
  - Loop safety guard (`max_tool_rounds` / `MAX_TOOL_ROUNDS_EXCEEDED`).
  - Stale turn rejection and clean in-flight cancellation without residual tool executions.
  - Dual execution modes: async event-driven (`submit_prompt`) and synchronous (`run`).
- **Addon Preferences & Configuration Management (M2.1)**:
  - Native Preferences UI in Blender for `Base URL`, `Model`, `API Key`, and `Timeout`.
  - Password subtype masking for API keys in UI.
  - Atomic, restricted-permission configuration persistence (`config.json`).
  - Support for environment variable overrides (`OPENAI_BASE_URL`, `BLENDER_AI_API_KEY`, etc.).
- **Tests & Verification**:
  - Pure Python unit test count increased to 221 tests.
  - Blender headless integration test suites expanded to 9 suites, including live Blender datablock round-trip verification (`test_provider_roundtrip.py`).
  - Added live endpoint test harness (`tests/manual/test_live_openai_endpoint.py`) targeting local 9Router (`http://localhost:20128/v1`).

---

## [0.1.0] - 2026-09-07

### Added
- **5 Non-Destructive Grounding Tools**:
  - `inspect_scene`: Scene hierarchy, camera, render settings, object counts.
  - `inspect_selection`: Current selection and active object transform.
  - `inspect_object`: Object metadata, transform matrices, modifier stack, material slots.
  - `inspect_material`: Principled BSDF shader parameters and material slots.
  - `inspect_mesh`: Topology diagnostics, vertex/edge/face counts, UV channel detection, world-space bounding box.
- **Thread-Safe Asynchronous Boundary**:
  - Main thread / background worker isolation (`AgentWorker`).
  - `ThreadSafeEventQueue` with bounded batch draining.
  - `BlenderAdapter` main-thread safety guard (`ThreadSafetyViolationError`).
  - `TimerBridge` consuming events via `bpy.app.timers`.
- **State Machine & Runtime Lifecycle**:
  - `AgentStateMachine` with deterministic transitions (`IDLE`, `PROCESSING`, `EXECUTING_TOOL`, `ERROR`).
  - Turn metrics tracking (`t_submitted`, `t_first_event`, `t_tools_duration`, `t_completed`).
  - In-flight cancellation and graceful unregistration handling.
- **Native Blender 5.2 Interface**:
  - 3D Viewport sidebar N-Panel (`VIEW_3D` -> `AI Copilot`).
  - Custom `UIList` displaying user prompts, tool executions, and assistant responses.
  - Prompt submission, cancel, and clear history operators.
  - Blender 5.2 extension manifest (`blender_manifest.toml`).
