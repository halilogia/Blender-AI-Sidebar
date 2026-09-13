# Executive Summary

**Antigravity Projesi** şu ana kadar *M1* ve *M2* aşamalarını tamamladı. Yapay Zekâ asistanı artık Blender sahnesini okuyabiliyor ve GPT-6 Astra (OpenAI) üzerinden gerçek modellemeler yapabiliyor. *M3.1* ve *M4.1* başarıyla tamamlandı: Temel mutasyon araçları (`create_primitive`, `transform_object`, `delete_object`) çalışır durumda ve orta/yüksek riskli eylemler için onay (approval) sistemi hayata geçirildi. Şimdi öncelik **M5, M6, M7** adımlarında: kullanıcı onayı sisteminin olgunlaştırılması, görsel anlama (vision) ile doğrulama ve en önemlisi **Harici Text-to-3D entegrasyonu** (M7). 

- **Durum Özeti:** Yol haritası *M1*–*M8* arasındaki aşamaları içerir. *M1–M2.7* tamam (gözlem, GPT entegrasyonu, tool çağırma), *M3.1* (mutasyon+undo) ve *M4.1* (onay sistemi) tamamlandı. Geriye *M4.2–M8* kalıyor. 
- **Rol Haritası:** Ürün Sahibi (PO), Teknik Lider, ML Mühendisi, Entegrasyon Mühendisi, Blender Geliştiricisi, QA, UX/Arayüz ve DevOps/Security ekipleri tanımlandı (aşağıdaki tabloda detaylı).
- **Teknik Mimari:** Aşağıdaki mermaid şemasında olduğu gibi, 9Router/OpenAI (örneğin GPT-6 Astra) üzerinden LLM’e istek gidiyor; `AgentRuntime` bunları alıp `ToolDispatcher` aracılığıyla semantik araçları yürütüyor; araçlar sonuçları Blender Adapter ile *bpy* üzerinden uygular. GPU Overlay UI ve Onay Kapısı kullanıcıyla etkileşiyor. Gelecekte *generate_3d_asset* aracı eklenecek; harici 3D modeller (Trellis, Meshy, Tripo, Rodin vb.) sorunsuz işlenecek. 
- **Dış Teknolojiler:** GPT-6 Astra (OpenAI) resmi sayfası projenin temel LLM’idir. Higgsfield Blender eklentisi, benzer bir taklitce’nin örneğidir. Trellis (Microsoft) yüksek kaliteli image→3D üretimi sağlar. Meshy AI, Tripo3D ve Hyper3D Rodin gibi servisler text/konsept’den 3B model oluşturuyor. Bu araçları entegre etmek, M7 kapsamında hedeflenecek. 
- **M7 Entegrasyon:** Yeni *generate_3d_asset(prompt)* aracı eklenecek. Önerilen adımlar: uygun 3D API (Trellis, Pixal3D, Tripo, Rodin vb.) belirleyip test etmek; araca bağlayarak sahneye otomatik import etme; parametre güvenliği, file format uyumluluğu; bu adımların kabul testi için otomatik ve manuel doğrulamalar tanımlandı. 
- **Göz önünde tutulacak konular:** Model halüsinasyonları, dış hizmet kesintileri, güvenlik (örneğin kod enjeksiyonu) ve kullanıcı deneyimi gibi riskler analiz edildi. 

Sonraki adım olarak bu kapsamlı özet ve kaynaklardan yola çıkarak **çözüm tasarımını tamamlama ve kodlama safhasına geçme** öneriliyor.



## 1. Durum Özeti (M1–M8 Yol Haritası)

Antigravity yol haritası aşağıdaki gibidir. Geçilen (*) aşamalar ve kalan işler işaretlenmiştir.

- **M1 – Grounding (Tamamlandı):** Blender sahnesini okuma, nesne/materyal okuma araçları (inspect_*), ana-iş parçacık izolasyonu.
- **M2 – LLM Entegrasyonu (Tamamlandı):** Asenkron model akışı (SSE, tool çağırma, streaming yanıt) sağlandı.
- **M2.7 – Round-Trip (Tamamlandı):** AI’ın kendi kendine `inspect_scene` gibi araçları kullanması sağlandı.
- **GPU UI – Görsel Arayüz (Tamamlandı):** Sağ panel yerine 3D Görüntü üstünde kayan bir komut çubuğu (HUD) yapıldı.
- **M3.1 – Safe Mutation + Undo (Tamamlandı):** Temel üç mutasyon aracı (**create_primitive**, **transform_object**, **delete_object**) eklendi. Her değişiklik için Blender’ın `Ctrl+Z` geri al desteği (undo_push) eklendi. Tüm birim ve entegrasyon testleri geçildi.
- **M4.1 – Onay Kapısı (Tamamlandı):** `delete_object` gibi orta/yüksek riskli tool çağrıları için kullanıcı onayı şartı getirildi. Model ister isteyerek onay istemesin, programatik olarak ara katmanda yakalanıyor. Onay ve reddetme arayüzü GPU overlay ile eklendi.
- **M4.2 – Onay UX İyileştirme (Beklemede):** Onay kartı tasarımı, çoklu onay (batch), onay zaman aşımı gibi özellikler gelecek.
- **M5 – Doğrulama (Yaklaşan):** AI’ın yaptığı değişiklikleri kontrol etmesi ve gerekirse düzeltmesi; *“doğru mu yaptığını inceleyip raporla”* yeteneği.
- **M6 – Materyaller ve Görünüm (Yaklaşan):** Materyal ağlarını, doku düzenlemeyi, rendering parametrelerini kontrol eden araçlar ekleme.
- **M7 – Harici 3D Üretim (Yaklaşan):** GPT-6’ya benzer şekilde “generate 3B model” komutlarını metinden 3B’ye dönüştürebilen dış servis entegrasyonu (Trellis, Meshy, Tripo, Rodin gibi).
- **M8 – Görsel Anlama (Yaklaşan):** AI’ın viewport ekran görüntüsünü işleyip *“kamera açısı iyi mi? Nesne algılama”* gibi görsel değerlendirme yapması.



## 2. Rol Haritası

| Rol                | Sorumluluklar                                                                                                                                   | Önerilen Kişi/Kişiler      |
|--------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|----------------------------|
| **Ürün Sahibi (PO)**      | Ürün vizyonunu belirler, önceliklendirme yapar, kabul kriterlerini tanımlar. Kullanıcı hikâyelerini oluşturur.                                   | (ör: Ö. Kaya)              |
| **Teknik Lider**         | Mimari kararları koordine eder, roadmap’i sağlar. Takım içi kod standartlarını ve entegrasyonu yönlendirir.                                | (ör: A. Demir)             |
| **ML Mühendisi**         | LLM modeli entegrasyonu, ince ayarlar ve doğrulama. Model API’lerini araştırma ve en uygun modeli seçme (GPT-6 Astra vb.).            | (ör: E. Yılmaz)            |
| **Entegrasyon Mühendisi**| 9Router/OpenAI altyapısı ile bağlantıyı kurar. AgentRuntime, ToolDispatcher ve mutasyon araçlarını geliştirir. Test otomasyonu (9Router çevrimiçi testi). | (ör: M. Şahin)             |
| **Blender Geliştiricisi**| Blender eklentisi kodunu yazar, GPU Overlay ve UI üzerinde çalışır. *bpy* manipülasyonlarını, thread güvenliğini sağlar (timer, undo). | (ör: R. Aksoy)             |
| **UX/Uİ Tasarımcısı**     | Kullanıcı arayüzü ve deneyimi tasarlar. Onay kartı, komut çubuğu düzeni, kullanıcı akışları.                                              | (ör: Z. Demir)             |
| **QA Mühendisi**         | Birim ve entegrasyon testlerini planlar/uygular. Otomatik testleri yürütür. Manuel kabul testlerini koordine eder (örneğin GUI testleri).   | (ör: S. Kaplan)            |
| **DevOps & Security**     | CI/CD pipeline’ı kurar. 9Router ve sunucu altyapısını yönetir. Gizli anahtarları, güvenlik duvarını, eklenti izinlerini kontrol eder.     | (ör: B. Çelik)             |

Tablo: Projede yer alan ana roller, sorumluluk alanları ve önerilen sahipleri.

## 3. Teknik Mimarî

```mermaid
flowchart TD
    A[9Router / OpenAI API<br>(GPT-6 Astra, Claude)] --> B[AgentRuntime]
    B --> C[ToolDispatcher]
    C --> D[Inspect Tools (M1)] 
    C --> E[Mutasyon Araçları<br>create_primitive, transform_object, delete_object]
    C --> F[generate_3d_asset*] 
    D -->|Veri| G[Blender Adapter]
    E -->|Değişiklik| G[Blender Adapter]
    F -->|3D Mesh| G[Blender Adapter]
    G --> H[bpy & Undo Manager]
    H --> I[Blender Scene]
    C --> J[GPU Overlay UI / Onay Kartı]
    I --> K[Vision/Verification (M7+)]
    C --> L[Dış 3B Hizmetler:<br>Trellis, Meshy, Tripo, Rodin, Pixal3D…]
    L --> F
    K --> B
```

- **LLM Provider (9Router/OpenAI)**: GPT-6 Astra veya Claude gibi modelleri barındırır. İstekler Token geçerliğinin kontrol edildiği **Producer** (9Router) üzerinden yapılır.
- **AgentRuntime**: Alınan `user prompt`ları çözümler, `ToolCall` yaratır, durum makinesini yönetir (örn. `PENDING_APPROVAL` durumu).
- **ToolDispatcher**: Giren `ToolCall` mesajlarına uygun `tool` fonksiyonlarını çalıştırır. `inspect_*` veya mutasyon araçları arasında yönlendirir.
- **Araçlar (Tools)**: Şu anda `inspect_scene`, `inspect_object`, **`create_primitive`**, **`transform_object`**, **`delete_object`** var. İleri aşamada **`generate_3d_asset`** gibi harici 3B üretim aracı eklenecek.
- **Blender Adapter**: Tool fonksiyonlarının `bpy` API ile sahneyi okumasını veya değiştirmesini yönetir. `UndoManager` her değişiklik sonrası atomik geri adım kaydeder.
- **bpy / Undo**: Gerçek Blender Python API’sı. `bpy.ops` veya veritabanı API’ları aracılığıyla model manipülasyonu yapılır. `bpy.ops.ed.undo_push()` ile her mutasyon’un geri alınabilir olması sağlandı.
- **GPU Overlay UI & Onay Kapısı**: Blender görünümünde kayan bir HUD. Kullanıcı komutlarını alır ve (M4.1) riskli işlemler için **onay kartı** gösterir. Onay/Redde yanıtları `AgentRuntime` üzerinden `ToolCall` yürütmeyi etkiler.
- **Verification / Vision**: (Gelecek) Yaptığı işi kontrol eden aşama. Örneğin `inspect_scene` ve görüntü sınıflandırma araçları ile sonuçların doğruluğu incelenir.
- **Dış 3B Üretim Servisleri**: M7 için planlanan aşama. Trellis, Meshy, Tripo, Rodin, Pixal3D vb. sunucularına prompt gönderip yüksek kaliteli 3B modeller alınır; bu modeller `generate_3d_asset` aracı ile sahneye eklenir.

Bu mimari, **agentic çalışma** modelini benimser: LLM yalnızca kararları verir, karmaşık 3B üretimi özel modeller tarafından yapılır. Örneğin Higgsfield eklentisinde “Meshy 5” harici bir 3B model üretim aracıdır.

## 4. Dış Teknolojiler ve Kısaca Tanıtımları

- **GPT-6 Astra (OpenAI)**: OpenAI’nın en son LLM modeli. Multimodal yetenekleri ve güçlü kodlama becerisi var. Bilgisayar kullanımında insan seviyesine yakın skorlar alıyor; Blender modellemelerinden Unreal entegrasyonuna kadar örnekler sunuyor. (*Resmi*: [openai.com/gpt-6-astra](https://openai.com/index/gpt-6-astra/)).
- **Higgsfield AI (Blender Eklentisi)**: Blender için tescilli bir eklenti. 3B sahneler, modeller, animasyon, video üretmek için bulut modellerini kullanır. Sahnede kayan “prompt bar” (floating HUD) ile GPT ve kendi 3B servislerini entegre eder. Scene Builder, Meshy-5 (3D üretim modeli) vb. özel modeller sunar. (*Resmi*: [higgsfield.ai/plugins/blender](https://higgsfield.ai/plugins/blender)).
- **Trellis.2 (Microsoft)**: Açık kaynak bir image→3D model üretim araştırması. Bir görüntüden yüksek çözünürlüklü, PBR dokulu 3B varlıklar oluşturur. Asset üretim sürecini etkin sıkıştırılmış “O-Voxel” temsili kullanarak hızlandırır.
- **Meshy AI**: Metin veya görsel girdilerden hızlı 3B model üreten bir araç. Kullanımı kolay arayüzü ile hobiden profesyonele kadar sahneler için model üretir (Meshy 5 sürümü, Higgsfield’da da kullanılıyor). (*Resmi*: meshy.ai).
- **Tripo 3D**: “Anında 3B üretim” vadeden ücretsiz bir web servisi. Metin veya resim prompt’u alıp saniyeler içinde tescilli modeller ve tekstürler üretir. Çeşitli endüstri örnekleri (VR, oyun, 3D baskı vb.) gösterir.
- **Hyper3D Rodin (Rodin AI)**: Yüksek kaliteli metin→3B model API’si. Inpainting ve detay kontrol özellikleriyle dikkat çeker. Metin ve resimden 3B varlık üretir, Blender/Unity eklentileriyle entegre edilir.
- **Pixal3D (Tencent ARC)**: Tek resim veya çoklu görselle yüksek çözünürlüklü 3B varlık üretebilen model (genellikle ComfyUI gibi platformlarda kullanılır).
- **ComfyUI**: Açık kaynaklı düğümlü arayüz. Metin/Görüntü→StableDiffusion→Trellis/Pixal3D gibi 3B iş akışları oluşturmak için kullanılır.
- **Blender-MCP (AhujaSid)**: Herhangi bir LLM’i Blender’a bağlayan topluluk eklentisi (örn. Claude, OpenAI gibi). Metin komutlarıyla Blender’ı kontrol etmeye yarar.
- **9Router / Ollama / LM Studio**: Lokal veya ağ tabanlı LLM proxy’leri. Bu mimaride 9Router üzerinden OpenAI ve benzeri API’lara yönlendirme yapılıyor.



## 5. M7: Text-to-3D Entegrasyon Adımları (Kontrol Listesi)

M7 aşamasında hedef, harici 3B jenerasyon modellerini ekleyerek “sıfırdan 3D üretimi” sağlamak. Önerilen adımlar ve testler:

1. **Araştırma ve Seçim:** Hangi servisler kullanılacak? (Örnek: Trellis.2, Pixal3D, Tripo, Rodin) Her bir API’nin özellikleri, ücretlendirme, veri formatları incelenecek.
2. **Yeni Tool Tasarımı:** `generate_3d_asset(prompt: String, style?)` şeklinde bir araç fonksiyonu oluştur. Parametre validasyonu.
3. **API Entegrasyonu:** Seçilen hizmetlerin REST API’ları veya SDK’ları ile entegrasyon. Prompt’u JSON gönderip, gelen 3B modeli `.glb/.obj` formatında indirin.
4. **Blender’a Aktarım:** İndirilen 3B dosyayı `bpy.ops.import_scene` ile sahneye ekle. Modeli 3B kursör pozisyonunda yerleştirin.
5. **Undo Yönetimi:** Bu aracı da tek bir undo adımı olarak kaydedin (`undo_push`).
6. **UI Geliştirme:** Gerekirse kullanıcıya stil seçimleri (örneğin model varyantları) sunan UI öğeleri ekleyin.
7. **Güvenlik ve Maliyet:** Kullanıcı girdilerinin kontrolü. API anahtarının güvenli saklanması. Her API çağrısının kredi/kota kullanımı takibi.
8. **Testler:** 
   - *Automated:* Belirli prompt’lar vererek API çağrılarının başarılı olduğunu doğrulayın. Mesh geldi mi? Blender sahnesinde mı? `generate_3d_asset` sonrası obje sayısı arttı mı?  
   - *Manual:* Örneğin, prompt “taş köprü modeli” girildiğinde Blender’da somut bir köprü modeli gözlemleyin. Onay reddetmeden doğru çalışmasını kontrol edin.
   - *Performance:* Büyük mesh’lerin yüklenmesi bellek/süre açısından makul mu test edin.

Bu checklist, M7’nin tamamlandığı kabul kriterlerini içerir. Her adım doğrulama/geri dönüş (log) ile izlenmeli.

## 6. Öncelikli Görevler (Önümüzdeki 6 Adım)

Aşağıdaki görevler önceliklendirilmiştir (yaklaşık çaba tahmini, kabul kriterleri ile):

| Öncelik | Görev                        | Çaba (saat) | Kabul Kriteri                                                   |
|:--------|:-----------------------------|:----------:|-----------------------------------------------------------------|
| 1       | **M5 Başlatma:** Mutasyon sonucu doğrulama araçları ekle. | 16 h       | `inspect_scene` sonrası sahnede nesnelerin beklendiği gibi göründüğünü raporluyor. Basit doğrulama senaryosu. |
| 2       | **M7 Araştırma:** 3B API’leri test et (Trellis, Tripo, Rodin vb.). | 12 h       | Seçilen servislerle ilk entegrasyon POC’u (örnek prompt sonucu 3D modeli alabilme). |
| 3       | **UI İyileştirme:** GPU Overaly onay kartı geliştir (daha iyi tasarım, kısa yol ekleri). | 8 h        | Onay kartında atanan [Y/N] kısayollar çalışıyor, açıklamalar net. |
| 4       | **QA ve Otomasyon:** Mevcut test kapsamını genişlet (örneğin onay reddetme v.b.). | 10 h       | Yeni unit testler eklendi, test kapsamı >=%X (ör: onayda 10 ek test).|
| 5       | **Dokümantasyon:** Proje durumu, setup ve nasıl çalışır README güncellemesi. | 6 h        | README/MANUAL.md yazıldı; yeni açılan M3–M4 özellikleri açıklandı. |
| 6       | **DevOps Hazırlık:** 9Router ve ortam konfigürasyonu dokümantasyonu. | 8 h        | 9Router (OpenAI vs Claude) ayarları belirlendi, ortam değişkenleri tanımlandı. |

Tablo: Kısa vadeli (yaklaşan) öncelikli işler, tahmini çabalar ve başarı ölçütleri.

## 7. Riskler ve Önlemler

- **Model Halüsinasyonu:** GPT-6 veya benzeri LLM’ler yanlış işlem önerebilir. *Önlem:* Kritik eylemler (silme, büyük mutasyon) mutlaka onay gerektirir; onay mekanizması zaten M4’te kuruldu. Ayrıca M5 ile sonuçları kontrol etme layer’ı eklenmeli.
- **Provider Kesintileri:** 9Router veya OpenAI hizmetinde aksama. *Önlem:* Hata durumlarında kullanıcıya bilgilendirici hata mesajları, gerektiğinde offline mock modu. İzleme (monitoring) eklenecek.
- **Güvenlik:** Kullanıcı prompt’larında zararlı komut (örneğin arbitrary Python) riski. *Önlem:* Yalnızca önceden tanımlı semantik araçlara izin; `exec()`/`eval()` kesin yasak. Konteyner/izin sınırlamaları uygulama.
- **Çıktı Uyumsuzluğu:** Üçüncü parti 3D servislerden gelen model formatı/ ölçeği sorunları. *Önlem:* Gelen mesh’in birim dönüşümleri, temizleme adımları ekleme. Blender import öncesi minimal validasyon.
- **Kullanıcı Deneyimi:** Karmaşık UI kullanım kolaylığı. *Önlem:* UX geri bildirimi toplama; onay kartı açıkça anlaşılır ve İngilizce/Türkçe metinler ile belirtilmeli.
- **API Maliyetleri:** Harici generatif modeller ücretli olabilir. *Önlem:* Kredi cost gösterimi (Higgsfield gibi), zorunlu onay kısmında maliyet uyarısı (gelecek bir geliştirme).
- **Bağımlılıklar:** 9Router gibi aracılar güncel tutulmalı. *Önlem:* Bağımlılık yönetimi, sürüm kontrolü.  

Bu riskler ve önlemler düzenli olarak gözden geçirilmeli, proje dokümanına eklenmelidir.

## 8. Antigravity için Önerilen Komut Mesajları

- **“Use real provider”**: `__init__.py`’de `MockProvider` yerine kullanıcının 9Router/OpenAI ayarlarına göre `OpenAICompatibleProvider` ile başlatılmalı. (Bu, daha önce testlerde de talep edildi.)
- **“Verify M4.1 in GUI”**: Kullanıcıdan GUI test sonuçları alın. Onay kartı akışı başarılı mı? Gerekirse UI/UX düzeltmeleri not edin.
- **“Plan M7 Integration”**: Harici 3B model API’leri için araştırma başlat. `generate_3d_asset` scaffold’unu oluşturun. Basit bir prompt ile 3B çıktı alın.
- **“Improve Onay UX”**: Onay kartına klavye kısayolu ekleyin (Y/N), kolay anlaşılır mesajlar, örnek komutlar ekleyin.
- **“Coverage & Docs”**: Yazılan yeni kod için birim testler ekleyin. README ve proje dokümanını güncelleyin.

Bu mesajlar, geliştirme ajandasına eklenebilir.

## Kaynaklar (Öncelikli)
- OpenAI GPT-6 Astra resmi tanıtımı  
- Higgsfield AI Blender eklenti sayfası  
- Microsoft Trellis.2 araştırması  
- Tripo3D resmi sitesi  
- Hyper3D Rodin (AI 3D model API)  

(Not: Görsel eklemeleri için [6†embed_image] ve [7†embed_image] kullanınız.)  

