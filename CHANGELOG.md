# Changelog — Blender AI Copilot

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
