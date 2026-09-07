# Project Roadmap — Blender AI Sidebar

This roadmap outlines the phased development trajectory for Blender AI Sidebar, transitioning from a robust, non-destructive grounding foundation to a fully autonomous, safe Blender copilot.

---

## Milestone Status Overview

| Phase | Milestone | Focus Area | Status | Verification |
| :---: | :--- | :--- | :---: | :--- |
| **M1** | **Grounding Copilot & Foundation** | Core domain, 5 grounding tools, async queue, native UI, headless test harness | **COMPLETED** | 56 pure Python tests, 8 Blender suites |
| **M2** | **Real LLM / Provider Integration** | OpenAI-compatible streaming protocol, SSE parser, HTTP client, tool round-trip | **COMPLETED** | 221 pure Python tests, 9 Blender suites |
| **M2.8** | **Streaming UI Token Updates** | Incremental token rendering in N-Panel during generation | *PLANNED* | Phase M2 polish |
| **M3** | **Safe Mutation & Scene Editing** | Object transforms, material assignments, modifiers, collections | *PLANNED* | M3 Milestone |
| **M4** | **Two-Tier Approval System** | Plan preview + granular user action approval | *PLANNED* | M4 Milestone |
| **M5** | **Context Compaction & History** | Token-efficient rolling memory & persistent conversation sessions | *PLANNED* | M5 Milestone |
| **M6** | **Multimodal / Vision Grounding** | Viewport rendering capture & visual scene reasoning | *PLANNED* | M6 Milestone |

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

---

## Planned Future Milestones

### Milestone 2.8: Streaming UI Token Updates
- [ ] Connect `StreamingTextDeltaEvent` to a live assistant buffer in the active UI panel.
- [ ] Display real-time streaming tokens in the 3D Viewport sidebar as the LLM generates.
- [ ] Maintain responsive scrolling and truncation for high-throughput responses.

### Milestone 3: Safe Mutation Tools
- [ ] Implement write-capable tools with deterministic risk ratings (`RiskLevel.MUTATING`):
  - `transform_object` (Translate, rotate, scale)
  - `create_primitive` (Mesh primitives with configurable params)
  - `assign_material` (Apply existing materials or create basic BSDF)
  - `manage_collections` (Move/link objects across collections)
  - `apply_modifier` (Subdivision, bevel, boolean)
- [ ] Transactional undo grouping via Blender's `bpy.ops.ed.undo_push()`.

### Milestone 4: Two-Tier Approval System
- [ ] **Tier 1: High-Level Plan Review**:
  - The agent generates a structured execution plan before mutating any scene state.
  - User reviews proposed steps in the UI before granting execution permission.
- [ ] **Tier 2: Granular Action Approval**:
  - For high-risk operations (e.g. object deletion, destructive booleans), an explicit confirmation modal or button is presented.
  - Zero unprompted destructive actions.

### Milestone 5: Context Compaction & Extended Chat Sessions
- [ ] Rolling memory window with automated summarization of older conversational turns.
- [ ] Selective tool result pruning (removing verbose mesh vertex dumps once inspected).
- [ ] Multi-turn session persistence across `.blend` file reloads.

### Milestone 6: Vision & Multimodal Grounding
- [ ] Automated headless viewport screenshot capture (`bpy.ops.render.opengl`).
- [ ] Image encoding and multimodal payload assembly for vision-capable models (e.g., GPT-4o, Claude 3.5 Sonnet, Gemini 1.5 Pro).
- [ ] Visual verification of framing, lighting, composition, and shader appearance.
