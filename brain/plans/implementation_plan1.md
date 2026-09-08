# Floating AI Command Bar & Conversation Drawer Implementation Plan

## 1. Değişecek ve Yeni Açılacak Dosyalar

### Yeni Dosyalar:
1. `ui/command_bar.py` [NEW]:
   * `AISIDEBAR_PT_command_bar` (Blender `Panel` with `bl_space_type = 'VIEW_3D'`, `bl_region_type = 'WINDOW'`).
   * Minimal, odaklanmış tek satırlık modern prompt giriş alanı (`✦ Ask Blender AI...`).
   * `Enter` / `Send` ve `Esc` ile kapatma.
2. `ui/conversation_drawer.py` [NEW]:
   * `AISIDEBAR_PT_conversation_drawer` (Blender `Panel` with `bl_space_type = 'VIEW_3D'`, `bl_region_type = 'WINDOW'`).
   * Aktif veya son turn'e ait konuşma kartı:
     - 👤 **You:** Kullanıcı prompt'u.
     - 🤖 **AI:** Canlı streaming metni veya nihai yanıt.
     - 🔧 **Tools:** Çalışan / tamamlanan araç kartları (`inspect_scene`, `inspect_object` vb.) ve özetleri.
     - Durum çubuğu (`Cancel` butonu veya `Yeni Soru` kısayolu).
3. `ui/keymap.py` [NEW]:
   * 3D Viewport için eklenti kısayol kaydı (`Alt + Space` -> `ai_sidebar.open_command_bar`).
   * Temiz lifecycle: `register_keymaps()` ve `unregister_keymaps()`.
4. `tests/integration/test_floating_ui.py` [NEW]:
   * Command Bar, Conversation Drawer, Keymap ve state geçişlerini headless Blender 5.2.1 ortamında doğrulayan entegrasyon testi.

### Değişecek Mevcut Dosyalar:
1. `ui/properties.py` [MODIFY]:
   * `ui_mode`: `EnumProperty(items=[("CLOSED", ...), ("COMMAND_BAR", ...), ("CONVERSATION", ...)], default="CLOSED")`.
   * `live_streaming_text`: Canlı token akışını drawer'da göstermek için hafif metin property'si.
2. `ui/operators.py` [MODIFY]:
   * `AISIDEBAR_OT_open_command_bar`: `ui_mode = "COMMAND_BAR"`, `call_panel(name="AISIDEBAR_PT_command_bar", keep_open=True)` çağırır.
   * `AISIDEBAR_OT_open_conversation`: `ui_mode = "CONVERSATION"`, `call_panel(name="AISIDEBAR_PT_conversation_drawer", keep_open=True)` çağırır.
   * `AISIDEBAR_OT_send_prompt`: Prompt'u `runtime.submit_prompt()` ile gönderir, `ui_mode`'u `CONVERSATION` yapar ve doğrudan Conversation Drawer'ı açar.
3. `ui/panel.py` [MODIFY]:
   * N-Panel'den büyük prompt ve chat formları kaldırılır.
   * N-Panel **Inspector & Control** paneline dönüştürülür:
     - Provider & Model durumu (9Router / OpenAI-compatible).
     - Sahne Context'i (Aktif sahne, nesne sayısı, seçili nesne).
     - Session History listesi (Geçmiş aramaları inceleme).
     - `✦ Open Command Bar (Alt+Space)` hızlı başlatma butonu.
4. `ui/header.py` veya `ui/panel.py` [MODIFY]:
   * 3D Viewport üst çubuğuna (`VIEW3D_HT_header`) `[ ✦ Ask AI ]` minimal tetikleyici butonu eklenir.
5. `ui/__init__.py` & `__init__.py` [MODIFY]:
   * Yeni panel sınıfları, keymap ve header çizim fonksiyonları register/unregister zincirine dahil edilir.

---

## 2. `call_panel` Nasıl Kullanılacak?

* `call_panel(name="...", keep_open=True)` Blender'ın native pencere yöneticisi (`wm`) üzerinde çalışan popover mekanizmasıdır.
* Bu çözüm bir MVP/native popup çözümüdür; GPU/bgl kodu gerektirmez, Blender 5.2'de çökme riski taşımaz.
* `keep_open=True` parametresi sayesinde kullanıcı panel içinde işlem yaparken popup kaybolmaz, streaming sırasında `tag_redraw` ile anlık güncellenir.

---

## 3. Shortcut Nasıl Register Edilecek?

* `wm.keyconfigs.addon` altında `"3D View"` alanına `Alt + Space` kısayolu tanımlanır:
  ```python
  km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
  kmi = km.keymap_items.new("ai_sidebar.open_command_bar", type="SPACE", value="PRESS", alt=True)
  ```
* Eklenti devre dışı bırakıldığında (`unregister`), keymap çakışma bırakmadan temizlenir.

---

## 4. Conversation State Mevcut Runtime History'ye Nasıl Bağlanacak?

* İkinci bir paralel state **oluşturulmayacaktır**.
* Conversation Drawer, `runtime.history` içindeki son konuşma turuna (en son `turn_id`'ye ait `HistoryItem` listesine) bakar.
* Event türüne göre (`USER` -> Prompt kartı, `TOOL` -> Araç kutucuğu, `ASSISTANT` -> Model cevabı) render edilir.
* Streaming sırasında `TimerBridge`, gelen token delta'larını `live_streaming_text` alanına yazar ve ekranı tazeler (`tag_redraw`).

---

## 5. Command Bar ile Conversation Arasında State Geçişi

```text
[CLOSED]
   │
   │ Shortcut (Alt+Space) veya Header Butonu
   ▼
[COMMAND_BAR] (Minimal arama kutusu)
   │
   │ Enter / Send
   ▼
[CONVERSATION] (Canlı Conversation Drawer açılır)
   │ ├── 👤 You: Prompt
   │ ├── 🤖 AI: Streaming...
   │ ├── 🔧 inspect_scene: Completed
   │ └── 🤖 AI: Final cevap
   │
   │ Esc veya Kapat
   ▼
[CLOSED]
```

---

## 6. Doğrulama Planı

1. **Birim & Entegrasyon Testleri:**
   * 221 Pure Python birim testi ve 9/9 mevcut Blender entegrasyon testi bozulmadan çalıştırılacak.
   * `tests/integration/test_floating_ui.py` ile `call_panel`, `keymap`, `ui_mode` geçişleri ve drawer çizimi doğrulanacak.
2. **Kullanıcı Kabul Senaryoları (GUI):**
   * Senaryo 1: `Alt + Space` -> Command Bar açılır -> `"Sahneyi incele."` yazılır -> `Enter` -> Conversation Drawer açılır -> Streaming metin, `inspect_scene` aracı ve final yanıt görünür.
   * Senaryo 2: `"Cube nesnesini incele ve boyutlarını söyle."` sorgusuyla çoklu tool/özellik döngüsü test edilir.
