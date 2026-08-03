# Faz 5 Runbook — Mevzuat Scraper + Semantik RAG

## Akış

```
crawler_mevzuat.py (requests+bs4)
  seed sayfalar → extract_main_text (nav/header/footer atılır, #content/main/body)
  → chunk_text → dedupe → bge-m3 embed (1024) → Qdrant 'regulations'
     payload: {document, source_url, title, chunk_index}   (idempotent: point_id_for(url#i))

bot.py: semantik soru → retrieve_semantic → regulations + staff (multi-collection)
        → qwen2.5:7b-instruct (temp=0, grounding) → cevap
```

## Çalıştırma

```bash
./.venv/bin/python setup_regulations_db.py            # regulations koleksiyonu (1024, cosine)
./.venv/bin/python setup_regulations_db.py --recreate # sıfırdan
./.venv/bin/python crawler_mevzuat.py                 # seed sayfaları çek + göm
./.venv/bin/python bot.py ask "Sınav sonucuma nasıl itiraz ederim?"
```

## Doğrulanan davranış (2026-07-09)

| Soru | Kaynak | Sonuç |
|---|---|---|
| "Sınav sonucuma nasıl itiraz ederim, kaç gün?" | Resmî Gazete yönetmeliği | ✅ "en fazla beş iş günü içinde... dilekçe" (grounded) |
| "Ders kaydı yapmazsam ne olur?" | OİDB SSS | ✅ doğru, akıcı, grounded |

qwen2.5:7b-instruct Türkçe üretimi qwen 4B'ye göre çok daha akıcı ve sadık;
"uydurma" davranışı görülmedi.

## Seed sayfa notları (kararlılık)

| Seed | Durum |
|---|---|
| resmigazete.gov.tr/.../20170821-5.htm (yönetmelik) | ✅ yavaş (read ~40s), uzun timeout gerekli; sınav itirazı burada |
| oidb.gaziantep.edu.tr SSS | ⚠️ kararsız (bazı koşularda ConnectTimeout), retry ile geliyor |
| oidb üniversitemiz-yönetmelikleri | sadece link listesi (1 chunk) — düşük değer |
| eduroam.gaziantep.edu.tr | ❌ DNS çözülmüyor; doğru host bulunmalı |

Scraper 3 deneme + `time.sleep(2)` ile kibar; bir seed düşse diğerleri işlenir.

## Açık işler (roadmap)

1. **Kapsam:** gerçek seed listesi genişletilmeli (yemekhane/SKS host'u doğru
   bulunmalı; eduroam gerçek URL; yatay geçiş/çift anadal yönergeleri).
2. **PDF:** bazı yönetmelikler PDF; HTML scraper almıyor — PDF metin çıkarımı
   (pdfminer/pypdf) eklenmeli.
3. **Link takibi:** şu an yalnız seed sayfalar; index sayfalarından alt
   linkleri (aynı domain, whitelist) takip eden sığ bir crawl eklenebilir.
4. **Chunk kalitesi:** madde (MADDE n) sınırına duyarlı chunking retrieval'ı
   iyileştirir; şu an cümle-bazlı 600 karakter.
5. **Kaynak gösterimi:** payload'da source_url var; system prompt LLM'i
   cevabın sonuna kaynak linki eklemeye yönlendirebilir.
```
