# Blender AI Sidebar

> Autonomous Grounding Copilot & AI Agent inside Blender 5.2.1 LTS.

[![Blender Version](https://img.shields.io/badge/Blender-5.2.1%20LTS-orange.svg)](https://www.blender.org/)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%20Zero%20Dependencies-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/Tests-221%20Unit%20%7C%209%20Headless%20Suites-brightgreen.svg)]()
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Blender AI Sidebar** is a native, non-destructive AI copilot built specifically for Blender 5.2.1 LTS. It connects modern Large Language Models (LLMs) directly to Blender's internal data model using safe, deterministic grounding tools, asynchronous thread-isolated execution, and a native 3D Viewport sidebar interface.

---

## Key Features

- **Strict Non-Destructive Grounding**:
  - `inspect_scene`: Detailed breakdown of scene hierarchy, active camera, render settings, and object counts.
  - `inspect_selection`: Active and selected object properties, types, and transforms.
  - `inspect_object`: Complete object transform, parentage, modifier stack, and material slots.
  - `inspect_material`: Principled BSDF shader parameters, color values, metallic, roughness, and slot mapping.
  - `inspect_mesh`: Topology diagnostics (vertex/edge/polygon counts), world-space bounding box dimensions, and UV availability.

- **Thread-Isolated Architecture**:
  - **Background Worker**: Handles HTTP connections, SSE streaming, and payload serialization without ever freezing Blender's UI.
  - **Main Thread Event Pump**: All Blender Python API (`bpy`) queries and tool executions run exclusively on Blender's main event loop via `TimerBridge` (`bpy.app.timers`).
  - **Thread-Safe Queue**: High-performance, lock-bounded event passing prevents memory leaks, race conditions, and UI starvation.

- **OpenAI-Compatible LLM Integration**:
  - Native Chat Completions streaming protocol (`POST /v1/chat/completions` with `stream=True`).
  - End-to-end support for tool/function calling round-trips (`tool_calls` -> execute tool -> return `tool` message -> final synthesis).
  - Multi-round conversational loops with automated loop guards (`max_tool_rounds`).
  - Compatible with **9Router**, **LM Studio**, **Ollama**, **OpenRouter**, or direct **OpenAI** endpoints.

- **Zero External Dependencies**:
  - Built entirely using Python's standard library (`urllib.request`, `http.client`, `json`, `threading`, `queue`, `dataclasses`).
  - No `pip install` required inside Blender's bundled Python environment.

- **Enterprise-Grade Addon Design**:
  - Conforms strictly to Blender 5.2 extension manifest standards (`blender_manifest.toml`).
  - Native `UIList` session history with status indicators.
  - Addon Preferences with password-masked API keys and disk persistence (`config.json`).
  - In-flight cancellation and stale turn rejection.

---

## Architecture Overview

```text
               +------------------------------------------------------+
               |                  Blender Main Thread                 |
               |                                                      |
               |  [ 3D Viewport Panel ] <==> [ UIList History ]       |
               |            |                        ^                |
               |            v                        |                |
               |     [ TimerBridge ] <---------------+                |
               |            |                        |                |
               |            v                        |                |
               |     [ AgentRuntime ]                |                |
               |      /     |      \                 |                |
               |     /      |       \                |                |
   [ StateMachine ]  [ Dispatcher ]  [ Conversation ]|                |
                           |                         |                |
                           v                         |                |
                   [ Grounding Tools ]               |                |
                   (inspect_scene/etc)               |                |
                           |                         |                |
                           v                         |                |
                   [ BlenderAdapter ]                |                |
                     (bpy datablocks)                |                |
               +-----------|-------------------------|----------------+
                           |                         |
               Thread-Safe | Event Queue             | Events
                           v                         |
               +------------------------------------------------------+
               |                Background Worker Thread              |
               |                                                      |
               |                  [ AgentWorker ]                     |
               |                         |                            |
               |                         v                            |
               |             [ OpenAICompatibleProvider ]             |
               |                         |                            |
               |            +------------+------------+               |
               |            |                         |               |
               |            v                         v               |
               |      [ HttpClient ]            [ SSEParser ]         |
               |     (urllib.request)                 |               |
               |                                      v               |
               |                         [ ToolCallAccumulator ]      |
               +--------------------------------------|---------------+
                                                      |
                                                      v
                                      Local / Remote OpenAI Endpoint
                                  (9Router / Ollama / LM Studio / OpenAI)
```

---

## Project Structure

```text
Blender AI Sidebar/
├── adapter/                      # Thread-safe Blender API bridge
│   ├── base.py                   # Abstract adapter interface
│   └── blender_adapter.py        # Main-thread-enforced bpy datablock access
├── agent/                        # Core agent coordinator & provider logic
│   ├── context_builder.py        # ProviderRequestContext assembler & guards
│   ├── dispatcher.py             # Tool validation & invocation dispatcher
│   ├── history.py                # Runtime history tracking
│   ├── http_client.py            # Pure Python streaming HTTP client
│   ├── mock_provider.py          # Deterministic offline mock provider
│   ├── models.py                 # ChatMessage, Conversation, ToolCall models
│   ├── openai_provider.py        # OpenAI-compatible streaming LLM adapter
│   ├── provider.py               # Abstract provider interface
│   ├── runtime.py                # Main-thread state & lifecycle coordinator
│   ├── sse_parser.py             # Deterministic byte-level SSE parser
│   ├── state_machine.py          # State transitions (IDLE/PROCESSING/TOOL/ERROR)
│   ├── tool_call_accumulator.py  # Streaming tool-call reassembly
│   ├── tool_mapper.py            # Internal-to-OpenAI function schema mapper
│   └── worker.py                 # Background generation worker thread
├── brain/                        # Design documents & technical plans
│   ├── knowledge.md              # Engineering invariant & architecture guide
│   └── plans/                    # Milestone implementation plans
├── core/                         # Shared pure Python core domain models
│   ├── config.py                 # Configuration loader, ENV overrides & validation
│   ├── event_queue.py            # Thread-safe bounded event queue
│   ├── events.py                 # Canonical boundary events & metrics
│   └── types.py                  # ToolResult, ToolError, RiskLevel
├── tools/                        # Non-destructive grounding tool implementations
│   ├── base.py                   # Abstract BaseTool definition
│   ├── registry.py               # In-memory tool registry
│   └── read_only/                # Grounding tools
│       ├── inspect_material.py   # Shader parameters & slot mappings
│       ├── inspect_mesh.py       # Topology, UVs, world bounding box
│       ├── inspect_object.py     # Transform, modifiers, hierarchy
│       ├── inspect_scene.py      # Scene summary, render settings, counts
│       └── inspect_selection.py  # Selected objects & active context
├── ui/                           # Blender native UI & timer integration
│   ├── operators.py              # Send, Clear, Cancel operators
│   ├── panel.py                  # 3D Viewport N-Panel sidebar interface
│   ├── preferences.py            # Addon Preferences & config persistence
│   ├── properties.py             # WindowManager RNA property definitions
│   ├── timer_bridge.py           # bpy.app.timers consumer & UI sync
│   └── uilist.py                 # Custom UIList history display
├── tests/                        # Comprehensive test harnesses
│   ├── integration/              # Headless Blender 5.2.1 LTS integration suites
│   ├── manual/                   # Live endpoint verification scripts (9Router)
│   ├── unit/                     # Pure Python unit test suites (221 tests)
│   ├── run_all_blender_tests.py  # Master headless test runner (9 suites)
│   └── run_unit_tests.py         # Pure Python test runner
├── blender_manifest.toml         # Blender 5.2 Extension manifest
└── __init__.py                   # Addon lifecycle (register / unregister)
```

---

## Installation

### Prerequisites
- **Blender**: 5.2.0 LTS or higher (tested against Blender 5.2.1 LTS).
- **LLM Endpoint**: Any OpenAI-compatible Chat Completions endpoint (e.g. 9Router at `http://localhost:20128/v1`, Ollama, LM Studio, or OpenAI).

### Install as Extension / Addon
1. Download or clone this repository into your Blender extensions or addons directory:
   ```bash
   git clone https://github.com/halilogia/Blender-AI-Sidebar.git
   ```
2. In Blender, open **Edit > Preferences > Add-ons**.
3. Search for **Blender AI Sidebar** and enable the checkbox.
4. Expand the addon preferences to configure:
   - **Base URL**: e.g. `http://localhost:20128/v1` (or your endpoint).
   - **Model**: e.g. `gpt-4o`, `llama3.1`, or your local model.
   - **API Key**: Enter if required (masked automatically).
   - **Timeout (seconds)**: Default is `30.0`.
5. Open the 3D Viewport, press `N` to open the sidebar, and switch to the **AI Copilot** tab.

---

## Usage

1. Type a natural language request in the prompt input field (e.g., *"Sahneyi incele"* or *"Cube objesini incele"*).
2. Click **Gönder** (Send).
3. The Copilot communicates with your LLM, automatically selects the appropriate read-only inspection tools, executes them on Blender's main thread, passes the real Blender datablock data back to the LLM, and displays the synthesized answer in the history log.
4. Click **İptal** (Cancel) at any time to abort an in-flight request.
5. Click **Temizle** (Clear) to reset conversation and history.

---

## Running Tests

### 1. Pure Python Unit Tests (Fast, No Blender Required)
Runs 221 unit tests covering SSE parsing, HTTP client, request mapping, tool accumulation, state transitions, loop limits, and provider round-trips:
```bash
python tests/run_unit_tests.py
```

### 2. Headless Blender Integration Tests
Runs all 9 headless integration test suites using Blender's Python runtime:
```bash
python tests/run_all_blender_tests.py
```

### 3. Live 9Router / OpenAI Endpoint Verification
Test your active local 9Router or OpenAI-compatible server:
```bash
python tests/manual/test_live_openai_endpoint.py
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
