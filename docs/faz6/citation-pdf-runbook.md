# Faz 6 Runbook — Kaynak Gösterme (Citation) + PDF Kırıcı

## Yeni yetenekler

1. **PDF desteği (crawler_mevzuat.py):** `fetch` artık Response döndürür.
   `is_pdf` (Content-Type=application/pdf VEYA URL .pdf ile biter) → `fitz`
   (PyMuPDF) ile bellekte açılıp tüm sayfa metni çıkarılır; değilse HTML
   gövdesi. Aynı chunk/dedupe/payload/idempotency akışı.
2. **Citation (rag_pipeline + bot.py):**
   - `format_context` her chunk'a `[Kaynak: <source_url>] Metin: <document>`
     iliştirir.
   - `SYSTEM_PROMPT` LLM'i cevabın en altına `🔗 Kaynak: [URL]` eklemeye
     zorlar; SADECE bağlamdaki linki yazar, uydurmaz.
3. **bot.answer(question):** tek satırlık giriş —
   `python -c "import bot; print(bot.answer('...'))"`.

## Doğrulanan test (2026-07-09)

```
S: Sınav sonucuma nasıl itiraz edebilirim ve kaç gün sürem var?
C: "...en fazla beş iş günü içinde itiraz edebilirsiniz. ...dilekçe vererek..."
   🔗 Kaynak: [https://www.resmigazete.gov.tr/eskiler/2017/08/20170821-5.htm]

S: Yatay geçiş için genel not ortalaması en az kaç olmalı?   (PDF kaynağı)
C: "...4.00 üzerinden 3.25 olmalıdır."
   🔗 Kaynak: [https://oidb.gantep.edu.tr/upload/files/YATAY%20GECIS%20...YONERGE.pdf]
```

İkisi de grounded (kaynak metinle birebir), kaynak URL doğru, link uydurulmadı.
PDF hattı ("3.25") ve HTML hattı ("beş iş günü") ayrı kaynaklardan çalışıyor.

## Çalıştırma

```bash
.venv/bin/python -m pip install PyMuPDF
.venv/bin/python setup_regulations_db.py
.venv/bin/python crawler_mevzuat.py          # HTML + PDF seed'leri göm
.venv/bin/python -c "import bot; print(bot.answer('...'))"
```

## Notlar / açık işler

- **Resmî Gazete kararsız:** sunucu bazı koşularda ConnectTimeout veriyor
  (tekrarlı isteklerde IP sınırlaması olası). crawler tek seed düşse
  diğerlerini işler; kritik kaynak düşerse tekrar çalıştır (idempotent,
  --recreate GEREKMEZ). Yönetmelik yalnız oradaysa o içerik gelmeden test
  "Bilmiyorum" döner — bu doğru grounding davranışıdır.
- **PDF chunk kalitesi:** madde-duyarlı chunking + tablo/başlık koruması ileride
  iyileştirilebilir.
- eduroam/SKS host'ları hâlâ DNS çözülmüyor (doğru URL bulunmalı).
