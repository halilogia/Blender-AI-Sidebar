# Changelog — Blender AI Sidebar

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
  - Pure Python unit test count increased to 221 tests (all passing in under 4 seconds).
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
