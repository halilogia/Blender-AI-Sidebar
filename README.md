# Blender AI Copilot (Blender AI Sidebar) — v1.0.0

> Autonomous Grounding Copilot & AI Agent inside Blender 5.2.1 LTS.

[English](#english) | [Türkçe](#türkçe)

[![Blender Version](https://img.shields.io/badge/Blender-5.2.1%20LTS-orange.svg)](https://www.blender.org/)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%20Zero%20Dependencies-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/Tests-618%20Unit%20%7C%2023%20Integration%20Suites-brightgreen.svg)]()
[![License: GPL-3.0](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)

**Blender AI Copilot** is a native, extensible AI agent built specifically for Blender 5.2.1 LTS. It connects modern Large Language Models (LLMs) directly to Blender's internal data model using deterministic grounding tools, safe scene mutations with atomic undo, strict policy-driven human approval gates, and a lightweight native GPU Viewport overlay.

> v1.0.0 is the first public release checkpoint. It includes FIFO prompt queueing, structured plan/task progress, diagnostics, multiline HUD input, and 618 passing pure-Python unit tests.

---

## English

## Key Features

- **Strict Non-Destructive Grounding**:
  - `inspect_scene`: Detailed breakdown of scene hierarchy, active camera, render settings, and object counts.
  - `inspect_selection`: Active and selected object properties, types, and transforms.
  - `inspect_object`: Complete object transform, parentage, modifier stack, and material slots.
  - `inspect_material`: Principled BSDF shader parameters, color values, metallic, roughness, and slot mapping.
  - `inspect_mesh`: Topology diagnostics (vertex/edge/polygon counts), world-space bounding box dimensions, and UV availability.

- **Safe Viewport Mutations & Atomic Undo (M3.1)**:
  - `create_primitive`: Generates `CUBE`, `SPHERE`, or `PLANE` with deterministic transforms and names via Data API.
  - `transform_object`: Translates, rotates, and scales existing objects in world coordinates.
  - `delete_object`: Safely unlinks and purges targeted objects with name verification and missing-object safety.
  - **Native Undo Push**: Every mutation automatically issues `bpy.ops.ed.undo_push()`, ensuring complete, lossless `Ctrl+Z` undo integration.

- **Deterministic Approval Gate & Risk Policy (M4.1)**:
  - Hardened execution policy independent of LLM hallucination or model prompts.
  - Granular risk classification: `READ_ONLY`, `LOW` (auto-execute), `MEDIUM`, `HIGH` (require explicit human approval).
  - Destructive operations (e.g. `delete_object`) freeze execution in `PENDING_APPROVAL`.
  - In-viewport interactive approval card with `[Approve]` and `[Reject]` buttons, keyboard shortcuts (`Y`/`N`), and single-use cryptographic security tokens.
  - User rejection safely routes back to the LLM conversation loop as structured context without throwing unhandled exceptions.

- **Dual User Interface**:
  - **Native GPU Viewport Overlay (HUD)**: Sleek, floating bar rendered directly in the 3D Viewport framebuffer (`SpaceView3D.draw_handler_add` with `POST_PIXEL`). Features anti-aliased geometry, drop shadows, Unicode/Turkish input buffer, interactive buttons, and hotkey toggling (`Alt+Space`). Zero external dependencies.
  - **3D Viewport N-Panel**: Standard persistent sidebar panel with complete session history (`UIList`), operation status badges, and manual approval controls.

- **Thread-Isolated Architecture**:
  - **Background Worker**: Handles HTTP connections, SSE streaming, and payload serialization without ever freezing Blender's UI or dropping viewport frames.
  - **Main Thread Event Pump**: All Blender Python API (`bpy`) queries, mutations, and undo pushes run exclusively on Blender's main event loop via `TimerBridge` (`bpy.app.timers`).
  - **Thread-Safe Queue**: High-performance, lock-bounded event passing prevents race conditions, memory leaks, and UI starvation.

- **OpenAI-Compatible LLM Integration**:
  - Native Chat Completions streaming protocol (`POST /v1/chat/completions` with `stream=True`).
  - End-to-end tool/function calling round-trips (`tool_calls` -> approval gate -> execute tool -> return `tool` message -> final synthesis).
  - Multi-round conversational loops with automated loop guards (`max_tool_rounds`).
  - Tested with **9Router**, **LM Studio**, **Ollama**, **OpenRouter**, and **OpenAI** endpoints.

- **Zero External Dependencies**:
  - Built entirely with Python's standard library (`urllib.request`, `http.client`, `json`, `threading`, `queue`, `dataclasses`).
  - No `pip install` or external wheels required inside Blender's bundled Python environment.

---

## Architecture Overview

```text
               +------------------------------------------------------+
               |                  Blender Main Thread                 |
               |                                                      |
               |  [ Viewport GPU HUD ] <======> [ N-Panel Sidebar ]   |
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
                  [ Approval Policy Gate ]            |                |
                   /                  \               |                |
       (Risk <= LOW)                   (Risk >= MEDIUM)|               |
             |                                  |     |                |
             v                                  v     |                |
     [ Tool Execution ]               [ PENDING APPROVAL ]             |
      - Grounding Tools                        |                       |
      - Mutation Tools                         |                       |
             |                                 | (User Approves/Rejects)
             v                                 |                       |
     [ BlenderAdapter ] <----------------------+                       |
      (bpy datablocks / undo_push)                                     |
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
│   └── blender_adapter.py        # Main-thread-enforced bpy datablock access & undo push
├── agent/                        # Core agent coordinator & provider logic
│   ├── context_builder.py        # ProviderRequestContext assembler & guards
│   ├── dispatcher.py             # Tool validation & invocation dispatcher
│   ├── event_router.py           # Stateless event classification boundary
│   ├── history.py                # Runtime history tracking
│   ├── http_client.py            # Pure Python streaming HTTP client
│   ├── mock_provider.py          # Deterministic offline mock provider
│   ├── models.py                 # ChatMessage, Conversation, ToolCall models
│   ├── openai_provider.py        # OpenAI-compatible streaming LLM adapter
│   ├── policy.py                 # ApprovalPolicy, PendingApproval, RiskLevel gating
│   ├── provider.py               # Abstract provider interface
│   ├── prompt_queue.py           # FIFO prompts submitted during an active turn
│   ├── runtime.py                # Main-thread state coordinator & approval handlers
│   ├── runtime_snapshot.py       # Consistent UI-neutral runtime projection
│   ├── sse_parser.py             # Deterministic byte-level SSE parser
│   ├── state_machine.py          # State transitions (IDLE/PROCESSING/TOOL/APPROVAL/ERROR)
│   ├── tool_call_accumulator.py  # Streaming tool-call reassembly
│   ├── tool_mapper.py            # Internal-to-OpenAI function schema mapper
│   └── worker.py                 # Background generation worker thread
├── brain/                        # Design documents & technical plans
│   ├── knowledge.md              # Engineering invariants & architecture guide
│   └── plans/                    # Milestone implementation plans
├── core/                         # Shared pure Python core domain models
│   ├── config.py                 # Configuration loader, ENV overrides & validation
│   ├── event_queue.py            # Thread-safe bounded event queue
│   ├── events.py                 # Canonical boundary events & metrics
│   └── types.py                  # ToolResult, ToolError, RiskLevel
├── tools/                        # Grounding and mutation tool implementations
│   ├── base.py                   # Abstract BaseTool definition
│   ├── registry.py               # In-memory tool registry
│   ├── read_only/                # Non-destructive grounding tools
│   │   ├── inspect_material.py   # Shader parameters & slot mappings
│   │   ├── inspect_mesh.py       # Topology, UVs, world bounding box
│   │   ├── inspect_object.py     # Transform, modifiers, hierarchy
│   │   ├── inspect_scene.py      # Scene summary, render settings, counts
│   │   └── inspect_selection.py  # Selected objects & active context
│   └── mutation/                 # Safe mutation tools with undo
│       ├── create_primitive.py   # CUBE, SPHERE, PLANE generation via Data API
│       ├── delete_object.py      # Targeted object unlinking and deletion (MEDIUM Risk)
│       └── transform_object.py   # Translation, rotation, and scaling (LOW Risk)
├── ui/                           # Blender native UI & timer integration
│   ├── gpu_overlay/              # Native 3D Viewport GPU HUD (Alt+Space)
│   │   ├── header.py             # Header bar widget
│   │   ├── keymap.py             # Viewport modal hotkey registration
│   │   ├── modal.py              # Modal operator for mouse/keyboard events
│   │   ├── renderer.py           # Blender GPU shader drawing routines
│   │   └── state.py              # HUD state machine, text buffer, and approvals
│   ├── operators.py              # Send, Clear, Cancel, Approve, Reject operators
│   ├── panel.py                  # 3D Viewport N-Panel sidebar interface
│   ├── preferences.py            # Addon Preferences & config persistence
│   ├── text_formatting.py        # Presentation-only assistant text cleanup
│   ├── properties.py             # WindowManager RNA property definitions
│   ├── timer_bridge.py           # bpy.app.timers consumer & UI sync
│   └── uilist.py                 # Custom UIList history display
├── tests/                        # Comprehensive test harnesses
│   ├── integration/              # Headless Blender 5.2.1 LTS integration suites
│   ├── manual/                   # Live endpoint verification scripts (9Router)
│   ├── unit/                     # Pure Python unit test suites (618 tests)
│   ├── run_all_blender_tests.py  # Master headless test runner (23 suites)
│   └── run_unit_tests.py         # Pure Python test runner
├── blender_manifest.toml         # Blender 5.2 Extension manifest
├── LICENSE                       # GNU General Public License v3.0
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
   - **Base URL**: e.g. `http://localhost:20128/v1` (or your local/remote endpoint).
   - **Model**: e.g. `gpt-4o`, `qwen2.5-coder`, `llama3.1`.
   - **API Key**: Enter if required (masked automatically).
   - **Timeout (seconds)**: Default is `30.0`.
5. In the 3D Viewport:
   - Press **`Alt + Space`** to open the floating **GPU Viewport HUD**, or
   - Press **`N`** to open the sidebar and switch to the **AI Copilot** tab.

---

## Usage

1. **Ask or Command**: Open the HUD (`Alt+Space`) or use the N-Panel, type a request (e.g., *"Sahneyi incele"*, *"Bir küp oluştur"*, or *"Küpü sil"*), and press Enter / Gönder.
2. **Autonomous Tool Selection**: The Copilot communicates with your LLM, selects the required grounding or mutation tools, and presents them to the execution layer.
3. **Approval Gate for Destructive Actions**: If a tool carries `MEDIUM` or `HIGH` risk (such as `delete_object`), execution stops immediately in `PENDING_APPROVAL`.
   - An amber approval card appears in the Viewport HUD and N-Panel.
   - Click **Approve (Y)** or press `Y`/`Enter` to permit execution.
   - Click **Reject (N)** or press `N`/`Esc` to decline. The rejection is fed back to the LLM to continue the conversation safely.
4. **Undo Support**: Any mutation can be reverted at any moment via Blender's standard `Ctrl + Z`.
5. **Prompt Queue**: Prompts submitted while another turn is active are kept in FIFO order and processed automatically.
6. **Plan and Task Progress**: Multi-step plans show approval cards and task progress with completed/failed step status.
7. **Multiline HUD**: Long prompts and assistant responses wrap across multiple lines. Shift+Enter inserts an explicit newline.

---

## Running Tests

### 1. Pure Python Unit Tests (Fast, No Blender Required)
Runs the complete pure-Python unit suite:
```bash
python tests/run_unit_tests.py
```

For a focused change, pass a test module or file and avoid waiting for the full suite:
```bash
python tests/run_unit_tests.py tests.unit.test_prompt_queue tests.unit.test_hardening
```

### 2. Headless Blender Integration Tests
Runs all 23 headless integration test suites inside Blender's actual Python runtime:
```bash
python tests/run_all_blender_tests.py
```

### 3. Live 9Router / OpenAI Endpoint Verification
Test your active local 9Router or OpenAI-compatible server:
```bash
python tests/manual/test_live_openai_endpoint.py
```

### 4. Diagnostics

The add-on writes a rotating diagnostic log to the operating system's temporary
directory by default. The N-Panel **Diagnostics** section shows the active path
and can copy it to the clipboard.

For local development, opt into a project-local directory without changing
the installed add-on default:

```powershell
$env:BLENDER_AI_LOG_DIR = ".\\logs"
```

The `logs/` directory and `*.log` files are excluded from Git because logs may
contain prompts and provider error details.

---

## Türkçe

Blender AI Copilot, Blender içinde çalışan yerel ve genişletilebilir bir AI
ajanıdır. OpenAI uyumlu LLM sağlayıcılarıyla konuşur; sahneyi incelemek,
nesne oluşturmak, dönüştürmek, silmek, materyal düzenlemek ve çok adımlı
işlemleri güvenli biçimde yürütmek için yapılandırılmış araçlar kullanır.

### Öne çıkan özellikler

- Sahne, seçim, obje, materyal ve mesh inceleme araçları.
- Küp, küre ve düzlem oluşturma; obje dönüştürme ve silme.
- Kamera, ışık, materyal, modifier, shading ve duplicate işlemleri.
- Risk tabanlı onay sistemi: düşük riskli işlemler otomatik, orta/yüksek riskli işlemler kullanıcı onaylıdır.
- Plan kartı, Approve/Reject butonları ve task ilerlemesi.
- Uzun promptlar ve AI cevapları için çok satırlı GPU HUD görünümü.
- Aktif işlem sürerken gönderilen mesajlar için FIFO queue.
- İptal, stale turn koruması, hata kaydı ve Blender `Ctrl+Z` undo desteği.
- Harici Python paketi gerektirmeyen standart kütüphane tabanlı mimari.

### Kurulum

1. Repository’yi indirin veya clone edin:

   ```bash
   git clone https://github.com/halilogia/Blender-AI-Sidebar.git
   ```

2. Blender’da **Edit > Preferences > Add-ons** menüsünü açın.
3. **Blender AI Sidebar** eklentisini etkinleştirin.
4. Eklenti ayarlarından OpenAI uyumlu endpoint, model ve gerekiyorsa API key girin.
5. GPU HUD için 3D Viewport üzerinde `Alt + Space`, N-Panel için `N` tuşuna basın.

### Kullanım

- `Sahneyi incele` gibi salt-okunur komutlar doğrudan çalışır.
- `Bir küp oluştur` gibi düşük riskli mutasyonlar güvenli şekilde yürütülür.
- Silme gibi riskli işlemlerde plan/onay kartı görünür; **Approve** veya **Reject** seçilir.
- İşlem sürerken yeni mesaj gönderilirse mesaj FIFO sırasına alınır.
- Uzun yazılar otomatik olarak alt satıra geçer; Shift+Enter manuel yeni satır ekler.
- Hata durumunda N-Panel’deki **Diagnostics** bölümünden log yolunu kopyalayabilirsiniz.

### Testler

Pure Python testlerini çalıştırmak için:

```bash
python tests/run_unit_tests.py
```

Odaklanmış test çalıştırmak için:

```bash
python tests/run_unit_tests.py tests.unit.test_prompt_queue tests.unit.test_event_router
```

v1.0.0 checkpoint’inde 618 pure-Python unit testi ve 23 Blender integration
test dosyası bulunmaktadır. Gerçek Blender entegrasyon testleri Blender’ın
kurulu olduğu ortamda çalıştırılmalıdır.

### Tanılama logları

Varsayılan log işletim sisteminin geçici klasörüne yazılır. Proje içinde
erişilebilir bir `logs/` klasörü kullanmak için PowerShell’de:

```powershell
$env:BLENDER_AI_LOG_DIR = ".\logs"
```

Loglar prompt ve provider hata ayrıntıları içerebileceği için Git’e eklenmez.

---

## License

This project is licensed under the **GNU General Public License v3.0** (GPL-3.0). See the [LICENSE](LICENSE) file for the full license text.
