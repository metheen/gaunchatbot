# Faz 3/4 Runbook — Hibrit Yönlendirici + Semantik Korpus

## Mimari

```
soru
 └─ intent_router.classify_intent
     ├─ YAPISAL (numara/telefon/dahili/eposta/kim/kimdir...)
     │    └─ Qdrant ES GEÇİLİR → MariaDB SQL
     │         ├─ match_department  → birimin (+alt birim) personeli
     │         └─ search_staff_by_tokens → search_name LIKE (AND)
     │    └─ CEVAP: rag_pipeline.format_staff_answer (DETERMİNİSTİK, LLM YOK)
     └─ SEMANTİK (yönetmelik/karmaşık)
          └─ bge-m3 → Qdrant 'regulations' + 'staff' RAG
               └─ qwen2.5:7b-instruct (temp=0, grounding)
```

## Kritik tasarım kararı — yapısal cevaplar LLM'siz

Yapısal sorgular (özellikle telefon/dahili) **deterministik biçimlenir**, LLM'e
verilmez. Telefon numarası gibi alanlarda üretim modeli gereksiz risk taşır.
DB'den birebir biçimlendirme, "uydurma yok" gereksinimini garanti eder.

## Doğrulanan davranış (2026-07-08)

| Soru | Yönlendirme | Sonuç |
|---|---|---|
| "Bilgi İşlem numarası kaç?" | YAPISAL/SQL, birim eşleşti (17) | ✅ 17 gerçek BİDB dahilisi, uydurma yok |
| "Veli Örnek dahili?" | YAPISAL/SQL, isim eşleşti (1) | ✅ "VELİ ÖRNEK (Profesör) — dahili 9001" |
| "Uzay Bilimleri dekanı?" | YAPISAL/SQL, eşleşme yok | ✅ "Bilmiyorum." (halüsinasyon tuzağı) |
| "Yatay geçiş şartları?" | SEMANTİK/RAG | ✅ `regulations` koleksiyonundan topraklanmış cevap |

## Çalıştırma

```bash
docker compose up -d
ollama pull bge-m3
ollama pull qwen2.5:7b-instruct
./.venv/bin/python embed_data.py                    # bge-m3 -> Qdrant staff
./.venv/bin/python setup_regulations_db.py          # Qdrant regulations
./.venv/bin/python crawler_yonetmelik.py            # örnek yönetmelik ingest
./.venv/bin/python bot.py ask "Bilgi İşlem numarası kaç?"
./.venv/bin/python bot.py ask "Yatay geçiş şartları nelerdir?"
./.venv/bin/python bot.py                            # REPL
```

.env: `OLLAMA_EMBED_MODEL=bge-m3`, `OLLAMA_LLM_MODEL=qwen2.5:7b-instruct`,
`QDRANT_COLLECTION=staff`.

## Açık işler (roadmap)

1. **Gerçek mevzuat crawler'ı:** `crawler_yonetmelik.py` şimdilik hard-coded
   örnek metin ingest eder. T1/T3/T4 kaynakları gerçek crawler ile beslenmeli.
2. **Router kenar durumları:** "Celal hocaya nasıl ulaşırım" gibi karışık
   ifadeler; harita intent'i (yemekhane → kampus.gaun.edu.tr/harita) henüz yok.
3. **Birim telefonu:** departments.phone boş; birim-düzeyi santral numarası
   ayrı toplanabilir (şimdilik birimin personel dahilileri listeleniyor).
