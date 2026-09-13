# Project Roadmap — Blender AI Copilot

This roadmap outlines the phased development trajectory for Blender AI Copilot, transitioning from a robust, non-destructive grounding foundation to a fully autonomous, safe Blender copilot.

**CURRENT STATUS: M8 — Context Compaction & Rolling Memory (COMPLETED)**

---

## Milestone Status Overview

| Phase | Milestone | Focus Area | Status | Verification |
| :---: | :--- | :--- | :---: | :--- |
| **M1** | **Grounding Copilot & Foundation** | Core domain, 5 grounding tools, async queue, native UI, headless test harness | **COMPLETED** | 56 pure Python tests, 8 Blender suites |
| **M2** | **Real LLM / Provider Integration** | OpenAI-compatible streaming protocol, SSE parser, HTTP client, tool round-trip | **COMPLETED** | 221 pure Python tests, 9 Blender suites |
| **M2.9** | **Native GPU Viewport HUD** | Floating in-viewport HUD (Higgsfield-inspired, pure 2D GPU, zero Chromium) | **COMPLETED** | GPU overlay integration suite, interactive HUD |
| **M3.1** | **Safe Mutation & Undo Foundation** | `create_primitive`, `transform_object`, `delete_object`, atomic `undo_push()` | **COMPLETED** | 237 pure Python tests, 11 Blender suites |
| **M4.1** | **Deterministic Approval Gate & HUD Card** | Centralized `ApprovalPolicy`, `PENDING_APPROVAL` gate, Viewport Approval Card | **COMPLETED** | 252 pure Python tests, 12 Blender suites |
| **M5** | **Deterministic Mutation Verification** | `ChangeSet`, `ChangeVerifier`, tolerance engine, `VERIFICATION_FAILED` handling | **COMPLETED** | 301 pure Python tests, 13 Blender suites |
| **M6** | **Materials & Shader Tools** | `set_material`, `assign_material`, Principled BSDF mutation, slot expansion | **COMPLETED** | 313 pure Python tests, 14 Blender suites |
| **M7** | **Vision / Screenshot Grounding** | Viewport screenshot capture, multimodal vision provider, visual verification | **COMPLETED** | 365 pure Python tests, 17 Blender suites |
| **M4.2** | **High-Level Plan Review** | Structured immutable plans, PlanValidator, propose_plan, PlanExecutor, batch approval | **COMPLETED** | 517 pure Python tests |
| **M8** | **Context Compaction & Rolling Memory** | Rolling memory, selective pruning, .blend session persistence + sanitization | **COMPLETED** | 419 pure Python tests, 18 Blender suites |
| **M9** | **Advanced Agentic Blender Operations** | Semantic tools, scene construction, inspect-plan-execute-verify-repair loop | *PLANNED* | M9 Milestone |

---

## Completed Milestones

### Milestone 1: Grounding Foundation & Async Boundary (v0.1.0)
- [x] **Core Domain & Types**: `ToolResult`, `ToolError`, `RiskLevel`, canonical event models.
- [x] **5 Non-Destructive Grounding Tools**:
  - `inspect_scene` (Hierarchy, active camera, render engine, counts)
  - `inspect_selection` (Active selection context and transform)
  - `inspect_object` (Modifiers, parentage, location/rotation/scale)
  - `inspect_material` (Principled BSDF node parameters & slots)
  - `inspect_mesh` (Vertices, edges, faces, UV channels, world bounds)
- [x] **Thread-Safe Boundary**: `ThreadSafeEventQueue`, bounded batch draining, zero UI freezing.
- [x] **BlenderAdapter**: Main-thread enforcement (`ThreadSafetyViolationError` on cross-thread access).
- [x] **AgentRuntime & State Machine**: `IDLE -> PROCESSING -> EXECUTING_TOOL -> IDLE/ERROR`.
- [x] **Native Blender 5.2 UI**: N-Panel sidebar, `UIList` session history, prompt input, cancel/clear operators.
- [x] **Headless Test Infrastructure**: Automated Blender 5.2.1 LTS headless test runner.

### Milestone 2: Real LLM & Tool Round-Trip Integration (v0.2.0)
- [x] **M2.1: Preferences & Config**: Addon preferences UI, masked API key, disk persistence (`config.json`), ENV overrides.
- [x] **M2.2: Internal Message & Event Models**: Blender-independent `ChatMessage`, `Conversation`, `ToolCall`, `Role`, `ProviderCompleted`, `ProviderError`.
- [x] **M2.3: Tool Schema Mapper & ContextBuilder**: Deterministic mapping from `BaseTool` to OpenAI function calling schema; character safety limits (`MAX_CONTEXT_CHARS=15000`).
- [x] **M2.4: Pure Python SSE Parser**: W3C-compliant byte-stream parser handling arbitrary chunk slicing, UTF-8 boundaries, LF/CRLF, and `[DONE]` sentinels.
- [x] **M2.5: Pure Python HTTP Client**: Streaming POST client using `urllib.request`, zero pip dependencies, in-flight cancellation via threading event.
- [x] **M2.6: OpenAI-Compatible Provider**: `/v1/chat/completions` streaming provider with `ToolCallAccumulator` for reassembling fragmented deltas.
- [x] **M2.7: AgentRuntime + Real Provider + Tool Round-Trip**:
  - User Prompt -> LLM -> `tool_calls` -> `ToolDispatcher` -> Main Thread execution -> `ToolResult` -> Conversation -> 2nd LLM call -> Final Answer.
  - Multi-round tool execution support (sequential tools in single turn).
  - Infinite loop guard (`max_tool_rounds` / `MAX_TOOL_ROUNDS_EXCEEDED`).
  - Stale turn rejection and clean in-flight cancellation.
  - Validated against 221 pure Python tests and 9 headless Blender suites.
  - Verified against local 9Router endpoint (`http://localhost:20128/v1`).

### Milestone 2.9: Native In-Viewport GPU HUD
- [x] **Higgsfield-inspired floating viewport overlay** using native Blender `gpu` and `blf` APIs.
- [x] Zero Chromium, WebView, Qt, Skia, or web server overhead (~0 MB extra binary size).
- [x] Full text editing buffer with cursor navigation, unicode & Turkish character support, and `Alt+Space` modal toggle.

### Milestone 3.1: Safe Mutation & Undo Foundation
- [x] **3 Core Safe Mutation Tools**:
  - `create_primitive` (`CUBE`, `SPHERE`, `PLANE`)
  - `transform_object` (`location`, `rotation`, `scale` with absolute and relative modes)
  - `delete_object` (exact object name unlinking and removal)
- [x] **Atomic Undo Integration**:
  - Every mutating operation registers a discrete undo transaction via `push_undo_step()`.
  - Immediate, lossless undo/redo support in Blender (`Ctrl+Z` / `Ctrl+Shift+Z`).
- [x] **Main-Thread Strict Enforcement**: Mutations blocked from background worker threads via `assert_main_thread()`.

### Milestone 4.1: Deterministic Approval Gate & Viewport Approval Card
- [x] **Centralized Approval Policy (`ApprovalPolicy`)**:
  - `READ_ONLY` and `LOW` risk tools auto-approved.
  - `MEDIUM`, `HIGH`, and `CRITICAL` risk tools gated programmatically.
  - Zero reliance on LLM conversational compliance; deterministic Python guardrail.
- [x] **Execution Gate & State Machine**:
  - `AgentState.PENDING_APPROVAL` lifecycle state.
  - `PendingApproval` immutable data container with unique `approval_id`.
  - Stale turn protection, duplicate execution prevention, and in-flight cancellation safety.
- [x] **Viewport Approval Card**:
  - In-viewport visual prompt with `⚠ Confirm Action`, human-readable action description, and risk badge.
  - `[ Reject (N) ]` and `[ Approve (Y) ]` interactive buttons with mouse click and keyboard shortcuts.
- [x] **Test Verification**:
  - 252 pure Python unit tests passing (12/12 approval acceptance criteria).
  - 12/12 headless Blender integration test suites passing.
  - Verified live in real Blender GUI with 9Router.

### Milestone 5: Deterministic Mutation Verification & Change Sets (v0.5.0)
- [x] **Standardized ChangeSet Data Structure (`core/change_set.py`)**:
  - Immutable representation capturing `operation`, `target_name`, `before`, `expected_after`, and `actual_after`.
  - `VerificationResult` and `VerificationStatus` (`PASS`, `FAIL`).
- [x] **Deterministic ChangeVerifier Engine (`agent/verifier.py`)**:
  - 100% pure Python standard library; zero `bpy` dependency.
  - Epsilon-based vector (`location`, `scale`) comparison and circular Euler angle wrapping difference.
  - Strict verification rules for `create`, `transform`, and `delete` operations.
- [x] **Runtime Verification Integration (`AgentRuntime._execute_and_verify`)**:
  - `build_change_set_from_result` derives expected target state directly from tool arguments.
  - Automatically captures live Blender actual snapshot from mutation adapter result.
  - On verification pass: attaches `verification` metadata to `ToolResult.ok`.
  - On verification fail: transitions to `AgentState.ERROR` with `VERIFICATION_FAILED` error code and detailed property mismatches, halting the turn safely.
- [x] **Test Verification**:
  - 301 pure Python unit tests passing.
  - 13/13 headless Blender integration test suites passing (`test_verification_integration.py`).

### Milestone 6: Materials & Shader Tools (v0.6.0)
- [x] **`set_material` Tool (`tools/mutations/set_material.py`)**:
  - Principled BSDF socket mutation: `base_color`, `metallic`, `roughness`, `emission_color`, `emission_strength`, `alpha`.
  - Input normalization: 3-element RGB automatically converted to 4-element RGBA.
  - Clamping: scalar and color inputs outside [0, 1] clamped safely to prevent shader engine errors.
- [x] **`assign_material` Tool (`tools/mutations/assign_material.py`)**:
  - Binds existing or new materials to object material slots.
  - Automatic slot expansion: requesting `slot_index=2` on an object with 1 slot creates intermediate empty slots.
- [x] **Lossless Shader Undo/Redo**:
  - Every material mutation pushes an atomic undo transaction (`push_undo_step()`).
  - Verified with native Blender `perform_undo()` and `perform_redo()`.
- [x] **Deterministic Material Verification**:
  - Extended `ChangeVerifier` and `build_change_set_from_result` for shader properties.
  - Partial verification: modifying a single property (e.g. `roughness`) verifies without failing on untouched default sockets.
  - Intentional divergence detection: verifies that genuine RNA divergence produces `VERIFICATION_FAILED`.
- [x] **Test Verification**:
  - 313 pure Python unit tests passing.
  - 14/14 headless Blender integration test suites passing (`test_material_mutations.py`).

### Milestone 7: Vision / Screenshot Grounding (COMPLETED)
- [x] **M7 Task 1: Viewport Screenshot Capture Primitive (COMPLETED)**:
  - `capture_viewport` read-only semantic tool (`RiskLevel.READ_ONLY`).
  - Main-thread execution enforcement via `assert_main_thread()`.
  - `ViewportReader`: renders active 3D Viewport via `gpu.types.GPUOffScreen` with `do_color_management=True`.
  - Pure Python in-memory PNG encoder (`encode_png_rgba`) using `zlib` and `struct` (zero external dependencies).
  - In-memory bounded LRU cache (max 10 images) preventing memory leaks.
  - Zero filesystem writes, zero scene contamination (objects, meshes, materials, images, and selection remain untouched).
  - Clean metadata contract (`image_id`, `width`, `height`, `format`, `mime_type`, `byte_size`) preventing conversation log pollution.
  - 320 pure Python unit tests and 15/15 headless Blender integration suites passing.
- [x] **M7 Task 2: Multimodal Provider Integration (COMPLETED)**:
  - Multimodal data contract: `ChatMessage` and `ProviderRequestContext` accept in-memory `image_id` / `images`.
  - Main-thread image resolution: `ContextBuilder.build` resolves raw PNG bytes on the main thread via `adapter.get_viewport_screenshot(image_id)`.
  - Thread-safe worker isolation: background worker thread receives pre-resolved in-memory PNG bytes and performs strictly HTTP/JSON/SSE.
  - In-memory Base64 data URI payload mapping: `OpenAIRequestMapper` formats `user` and `tool` messages with `image_url` parts (`data:image/png;base64,...`).
  - Deterministic capability gate: `supports_multimodal=False` deterministically yields `PROVIDER_UNSUPPORTED` / `MultimodalUnsupportedError`.
  - Zero filesystem writes: zero temporary image files created on disk.
  - Privacy and log hygiene: raw image bytes and base64 strings excluded from history and message serialization.
  - 335 pure Python unit tests and 16/16 headless Blender integration suites passing (`test_multimodal_integration.py`).

- [x] **M7 Task 3: Visual Scene Verification (COMPLETED)**:
  - `VisualVerifier` deterministic visual scene verification engine and `VisualResultParser`.
  - Read-only `visual_verify` semantic tool registered in `ToolRegistry` (`RiskLevel.READ_ONLY`).
  - Structured decision outcomes: `PASS`, `FAIL`, `UNCERTAIN` with concise rationale.
  - Verification hierarchy: deterministic RNA semantic verification (`ChangeVerifier`) is authoritative gatekeeper; visual verification is skipped if semantic verification fails.
  - Safe failure handling: visual `FAIL` / `UNCERTAIN` reports visual discrepancy without triggering automatic rollback.
  - Main-thread safety: `capture_viewport` and `get_viewport_screenshot` strictly isolated to Blender main thread.
  - Zero raw PNG bytes or base64 data URIs leaked into history or message serialization.
  - Robust parser safely rejecting malformed/corrupted model responses without unhandled exceptions.
  - 365 pure Python unit tests and 17/17 headless Blender integration suites passing (`test_visual_verification_integration.py`).

---

## Completed Milestones (continued)

### Milestone 4.2: High-Level Plan Review (COMPLETED)
- [x] Structured immutable execution plans (`agent/plan_models.py`): frozen `Plan` / `PlanStep`, validated snapshot semantics.
- [x] Strict `PlanValidator` (`agent/plan_validator.py`): unknown-tool, schema, dependency, cycle and `propose_plan`-nesting rejection; risk derived deterministically from registry/tool metadata (LLM `overall_risk` is not authoritative).
- [x] `propose_plan` meta-tool: declarative plan intake only, no scene mutation, no recursion inside execution plans.
- [x] Deterministic topological `PlanExecutor` (`agent/plan_executor.py`): orchestration layer over existing ToolDispatcher/verification, fail-fast execution, `PlanExecutionSummary`.
- [x] High-level `PlanReview` + single batch approval: one review card before any step executes; approve runs immutable plan exactly once; reject runs zero mutations; single-use, stale-turn/cancellation protected.
- [x] Approved plan suppresses per-step re-approval; standalone single-tool approval behavior unchanged.

### Milestone 8: Context Compaction & Rolling Memory (COMPLETED)
- [x] **M8 Task 1: Context Compaction Architecture / Minimal Design**:
  - Deterministic context budget guard and compaction triggers.
  - Safe tool-call / tool-result sequence preservation.
  - Architectural decoupling between active conversational context and historical rolling memory.
- [x] **M8 Task 2: Rolling Memory Window & State Compaction**:
  - Pure Python `RollingMemory` tracking user tasks, verified mutations, deleted entities, read inspections, and errors.
  - Scene entity lifecycle consistency: successful `delete_object` operations strictly remove objects from verified living state into `deleted_entities`.
  - Deterministic summary injection preserving `validate_sequence()` integrity without LLM summarization overhead.
- [x] **M8 Task 3: Selective Tool Result Pruning**:
  - Deterministic truncation of verbose JSON dumps in older inspected read-only results (`inspect_mesh`, `inspect_scene`, `inspect_object`, etc.).
  - Preserves recent turns and active turns verbatim.
  - Maintains strict `(ASSISTANT tool_calls) -> (TOOL result)` sequence validity and valid JSON structures.
- [x] **M8 Task 4: .blend Reload Session Persistence**:
  - Blender-native session state persistence via active Scene custom property (`scene["ai_sidebar_session_memory"]`).
  - Zero external database or JSON file dependencies; zero secrets, credentials, or raw image bytes persisted.
  - Integrated with `bpy.app.handlers.save_pre` and `bpy.app.handlers.load_post` executing strictly on Blender's main thread.
  - Safe deserialization with schema version marker (`schema_version = 1`) and graceful fallback for missing or corrupted data.
  - 419 pure Python unit tests and 18/18 headless Blender integration suites passing (`test_session_persistence.py`).

---

## Planned Future Milestones

### Milestone 9: Advanced Agentic Blender Operations
- [ ] Stronger semantic Blender tools.
- [ ] Camera / light / collection / modifier / mesh operation coverage.
- [ ] Multi-step scene construction capabilities.
- [ ] Agent loop: inspect → plan → execute → verify → repair.
- [ ] Real Blender end-to-end validation.

