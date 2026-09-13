# Project Roadmap — Blender AI Copilot

This roadmap outlines the phased development trajectory for Blender AI Copilot, transitioning from a robust, non-destructive grounding foundation to a fully autonomous, safe Blender copilot.

**CURRENT STATUS: M7 Task 1 — Viewport Screenshot Capture**

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
| **M7** | **Vision / Screenshot Grounding** | Viewport screenshot capture, multimodal vision provider, visual reasoning | **IN PROGRESS (Task 1 Complete)** | 320 pure Python tests, 15 Blender suites |
| **M4.2** | **High-Level Plan Review (Tier 1)** | Multi-step plan preview and user confirmation before batch operations | *PLANNED* | M4 Milestone Phase 2 |
| **M8** | **Context Compaction & Rolling Memory** | Token-efficient rolling memory & persistent conversation sessions | *PLANNED* | M8 Milestone |
| **M9** | **Text-to-3D Asset Generation Integration** | External 3D foundation model / API bridge (e.g. Tripo3D, Trellis, Meshy) | *PLANNED* | M9 Milestone |

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

### Milestone 7: Vision / Screenshot Grounding (In Progress)
- [x] **M7 Task 1: Viewport Screenshot Capture Primitive (COMPLETED)**:
  - `capture_viewport` read-only semantic tool (`RiskLevel.READ_ONLY`).
  - Main-thread execution enforcement via `assert_main_thread()`.
  - `ViewportReader`: renders active 3D Viewport via `gpu.types.GPUOffScreen` with `do_color_management=True`.
  - Pure Python in-memory PNG encoder (`encode_png_rgba`) using `zlib` and `struct` (zero external dependencies).
  - In-memory bounded LRU cache (max 10 images) preventing memory leaks.
  - Zero filesystem writes, zero scene contamination (objects, meshes, materials, images, and selection remain untouched).
  - Clean metadata contract (`image_id`, `width`, `height`, `format`, `mime_type`, `byte_size`) preventing conversation log pollution.
  - 320 pure Python unit tests and 15/15 headless Blender integration suites passing.

---

## Planned Future Milestones

### Milestone 7: Vision / Screenshot Grounding (Upcoming Tasks)
- [ ] **M7 Task 2**: Multimodal Provider Integration:
  - Extend `OpenAICompatibleProvider` to format image payloads for vision-capable models (GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro).
  - Retrieval of cached in-memory PNG bytes via `adapter.get_viewport_screenshot(image_id)`.
- [ ] **M7 Task 3**: Visual Scene Verification:
  - Visual sanity check comparing rendered viewport state against high-level prompt intent.

### Milestone 4.2: High-Level Plan Review (Tier 1)
- [ ] Structured multi-step execution plan generated prior to complex scene edits.
- [ ] User review and batch approval before initiating multiple sequential tool calls.

### Milestone 8: Context Compaction & Extended Chat Sessions
- [ ] Rolling memory window with automated summarization of older conversational turns.
- [ ] Selective tool result pruning (removing verbose mesh vertex dumps once inspected).
- [ ] Multi-turn session persistence across `.blend` file reloads.

### Milestone 9: Text-to-3D Asset Generation Integration
- [ ] External 3D generation API bridge (Tripo3D, Meshy, Rodin, Trellis).
- [ ] `generate_3d_asset` tool dispatching prompt to text-to-3D service and automatically importing generated `.glb`/`.obj` mesh into active scene.
