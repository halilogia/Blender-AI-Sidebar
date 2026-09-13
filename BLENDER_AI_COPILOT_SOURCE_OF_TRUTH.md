# BLENDER AI COPILOT — TEKNİK DURUM HARİTASI (SOURCE OF TRUTH)

## 1. Ürün Tanımı: Mevcut Durum vs. Hedef Ürün

### Ürün Hedefi (Vision)
> **Blender AI Copilot'un uzun vadeli amacı:**  
> Kullanıcı doğal dil talebini sahne bağlamını anlayarak planlayan, semantic Blender tools ve gerektiğinde harici 3D generation servislerini kullanan, yaptığı değişiklikleri doğrulayan ve güvenli onay mekanizmasıyla Blender sahnesinde gerçekleştiren agentic bir copilot olmaktır.

### Mevcut Durum vs. Hedef Ürün Ayrımı
- **CURRENT (Mevcut Durum):** M1–M4.1 aşamaları tamamlanmış; sahneyi okuyabilen (`inspect_*`), temel mutasyonları yapabilen (`create_primitive`, `transform_object`, `delete_object`), her başarılı mutasyon için Blender undo state oluşturan ve riskli eylemleri programatik onay kapısıyla (`PendingApproval`) durduran **semantic Blender assistant**.
- **TARGET (Hedeflenen Ürün):** Planning + mutation + verification + vision + materials + controlled scripting + opsiyonel external 3D generation yeteneklerine sahip tam kapsamlı **agentic copilot**.

---

## 2. Rol Haritası

| Rol | Sorumlu | Sorumluluk Alanı |
| :--- | :--- | :--- |
| **Product Owner** | **Kullanıcı** | Nihai karar verici, vizyon, kabul kriterleri, önceliklendirme ve canlı kabul testleri. |
| **Technical Architect** | **ChatGPT** | Üst düzey mimari tasarım, servis/araç araştırmaları ve stratejik yol haritası. |
| **Implementation Agent** | **Antigravity** | Kod geliştirme, refactoring, thread güvenliği ve yerel test otomasyonu. |
| **3D / AI Research** | **ChatGPT + Antigravity** | Text-to-3D sağlayıcıları, model yetenekleri ve Blender API kısıtları araştırması. |
| **Manual QA** | **Kullanıcı** | Gerçek Blender GUI, 9Router, canlı model ve etkileşim doğrulaması. |
| **Automated QA** | **Antigravity** | Pure Python unit testleri ve headless Blender test suite'leri. |

---

## 3. Mimari İlkeler ve Sınırlar

1. **Strict Thread Boundary**:
   - **Background Worker Thread**: Yalnızca HTTP I/O, SSE streaming ve JSON deserialization yürütür. `bpy` modülüne ve Blender veri bloklarına doğrudan erişimi kesinlikle engellenmiştir.
   - **Blender Main Thread**: Tüm sahne sorguları, mutasyonlar, UI çizimleri ve undo çağrıları yalnızca `bpy.app.timers` (`TimerBridge`) üzerinden ana iş parçacığında çalıştırılır.
2. **Deterministic Semantic Tools Only**:
   - Güvenlik ve stabilite gereği doğrudan `exec()` veya `eval()` ile serbest Python kodu çalıştırma yaklaşımı kullanılmaz. Eylemler şeması önceden tanımlı araçlar (`BaseTool`) üzerinden yürütülür.
3. **Blender Undo Entegrasyonu**:
   - Her başarılı mutasyon için Blender undo state oluşturulur (`bpy.ops.ed.undo_push()`) ve mutasyon tek bir undo adımı olarak tasarlanır.
4. **Deterministic Approval Gate**:
   - Onay kararı LLM'e bırakılamaz. `ApprovalPolicy`, risk seviyesi `MEDIUM` veya `HIGH` olan araç çağrılarını yakalar ve `PENDING_APPROVAL` durumuna geçirir. Kullanıcı açık onay vermeden eylem yürütülmez.
5. **Provider-Agnostic Engine**:
   - Sistem belirli bir modele kilitli değildir; 9Router veya OpenAI uyumlu herhangi bir endpoint üzerinden çalışır. Resmî OpenAI kaynaklarında yer alan `gpt-6-astra` API modeli de dahil olmak üzere OpenAI uyumlu tüm modeller bu çerçevede desteklenen sağlayıcı seçenekleri arasındadır (önceki "Astra yok" tespiti kapatılmıştır).
6. **Credential Hygiene**:
   - API anahtarları sahneye, WindowManager'a, `.blend` dosyasına veya loglara kaydedilmez.

---

## 4. Yol Haritası (Roadmap)

### Tamamlanan Fazlar
- **M1 — Grounding:** Sahne, seçim, obje, materyal ve mesh okuma araçları (`inspect_*`).
- **M2 — Provider / AgentRuntime:** SSE streaming, tool-call accumulation, context builder ve çift yönlü tool döngüsü.
- **M2.9 — Native GPU Viewport Overlay:** Viewport üzerinde çalışan modal HUD arayüzü (`Alt+Space`).
- **M3.1 — Safe Mutation + Undo:** `create_primitive`, `transform_object`, `delete_object` ve `undo_push` entegrasyonu.
- **M4.1 — Deterministic Approval Gate:** Risk seviyeleri, `PendingApproval`, Viewport HUD onay kartı ve `Y`/`N` klavye kısayolları.
  *(Not: Onay UX iyileştirmeleri zorunlu bir milestone değil, bağımsız bir UI backlog maddesidir).*

### Gelecek Fazlar
- **M5 — Verification + Change Sets:** Mutasyon sonrası sahne durumunu otomatik doğrulama ve raporlama.
- **M6 — Materials + Shader Tools:** Shader node ağları, doku yönetimi ve Principled BSDF kontrolleri.
- **M7 — Vision / Screenshot Grounding:** Viewport ekran görüntüsü alma ve multimodal görsel inceleme.
- **M8 — Controlled Python:** Korumalı ve parametrik script yürütme katmanı.
- **M9 — External Text-to-3D Integration:** Harici 3D üretim servislerinin entegrasyonu.

---

## 5. Harici 3D Üretim Prensibi (External 3D Generation Axiom)

Harici 3D entegrasyonunda mimari ayrım şu şekildedir:

$$\text{LLM (Orkestratör / Planlayıcı)} \neq \text{3D Model Üreticisi (Generator)}$$

```text
[ KULLANICI ]
     │ Doğal dil talebi
     ▼
[ LLM Provider ] (Planlayıcı / Orkestratör)
     │
     └── Gelecekteki Araç Adayı: generate_3d_asset(prompt, ...)
              │ (Henüz implementasyon başlamadı — tasarım aşamasında)
              ▼
     [ Harici 3D Servis / Model Adayları ]
     (Temsilî adaylar: Meshy, Tripo, Hyper3D Rodin, TRELLIS.2 vb.)
              │
              └── Çıktı: .glb / .obj
                       │
                       ▼
     [ Blender Adapter & Scene Host ]
              │
              ├── Dosyayı içe aktarır ve sahneye yerleştirir
              ├── Undo state oluşturur
              └── Durumu doğrular (Verification)
```

> **Önemli Not:** `generate_3d_asset` aracı gelecekteki bir semantik araç adayıdır; şu an implementasyonu başlamamıştır. Listelenen 3D servisleri (Meshy, Tripo, Rodin, TRELLIS.2) yalnızca **aday sağlayıcılar/araştırma alternatifleridir**; henüz bir servis seçilmemiş veya nihai entegrasyon kararı verilmemiştir.

---

## 6. Test Durumu ve Standartlar

- **Son doğrulanan otomatik test sonucu:**
  - `252 Pure Python unit test` (Hızlı, Blender bağımsız).
  - `12 headless Blender test suite` (Blender 5.2.1 LTS runtime içinde).
  *(Test sayıları proje geliştikçe güncellenecektir).*
- **Doğrulama Ayrımı:**
  - **AUTOMATED**: Yalnızca Antigravity tarafından scriptler ile koşturulabilen birim ve headless entegrasyon testleridir.
  - **MANUAL**: Gerçek Blender GUI, canlı 3D Viewport HUD, canlı 9Router/Model gecikmesi ve kullanıcı klavye/fare etkileşimlerini içerir. Bu testler yalnızca **Kullanıcı** tarafından gerçek arayüzde bizzat icra edildiğinde "Doğrulandı" statüsü kazanır.

---

*Bu metin, proje için resmi ve bağlayıcı teknik durum belgesi (Source of Truth) olarak kilitlenmiştir.*
