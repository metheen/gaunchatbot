# Faz 2 RAG Runbook — bot.py

## Altyapı (Mac, dev/pilot)

Stack host'a **yalnız 127.0.0.1** üzerinden açık (LAN'a kapalı):

```bash
docker compose up -d                 # mariadb + qdrant (healthy bekle)
ollama pull bge-m3                    # embedding modeli (LLM modelleri embed için kullanılmaz)
```

`.env` host'tan erişim için ayarlı: `MARIADB_HOST=127.0.0.1`,
`QDRANT_URL=http://127.0.0.1:6333`, `OLLAMA_URL=http://127.0.0.1:11434`.

## Veri hattı

```bash
.venv/bin/python crawler_rehber.py --write     # rehber -> MariaDB staff (~1216)
.venv/bin/python bot.py ingest                 # MariaDB -> bge-m3 -> Qdrant
.venv/bin/python bot.py ingest --recreate      # koleksiyonu sıfırdan kur
.venv/bin/python bot.py ask "Veli Örnek dahili?"
.venv/bin/python bot.py                         # interaktif REPL
```

## Doğrulanan davranış (2026-07-08)

| Sorgu tipi | Örnek | Sonuç |
|---|---|---|
| Topraklanmış kişi/dahili | "Veli Örnek dahili?" | ✅ Doğru: Fizik Müh., 9001 |
| Halüsinasyon tuzağı | "Uzay Bilimleri Fak. dekanı?" | ❌ Uyduruyor ("Eyüp Yeter") |
| Alan dışı | "Kripto borsası nasıl kurulur?" | ❌ Bilmiyorum demiyor, çöp üretiyor |
| Toplu/agregasyon | "X bölümünde kimler var?" | ❌ RAG bu soru tipine uygun değil |

## Bilinen sınırlama ve KÖK NEDEN (kritik)

Saf vektör RAG + önceki küçük üretim modeli kombinasyonu **reddetme (Bilmiyorum)
gereksinimini güvenilir karşılamıyor**:

1. **Skor çakışması:** bge-m3 dense skorları geçerli isim aramalarında
   ~0.52-0.55; alakasız "fakülte dekanı" sorgusu 0.55 (daha yüksek). Hiçbir
   `RAG_SCORE_THRESHOLD` bu ikisini ayıramaz — eşik bir band-aid.
2. **Model zayıflığı:** önceki küçük üretim modeli, "bağlamda geçmiyorsa
   Bilmiyorum de" talimatına Türkçe'de güvenilir uymuyor; boş bağlamda bile
   uyduruyor.

### Önerilen kalıcı çözüm (Faz 3 intent router ile örtüşüyor)

- **Hibrit yönlendirme:** kişi/unvan/birim/dahili sorguları vektör RAG'e DEĞİL,
  MariaDB'ye deterministik gitsin (`staff.search_name` normalize eşleşmesi,
  `role_title` LIKE). Vektör RAG yalnız serbest metin (yönetmelik/duyuru) için.
  "Uzay Bilimleri" gibi var olmayan birim → MariaDB'de 0 satır → deterministik
  "kayıtlarımda yok". Bu, master plandaki hibrit mimarinin ta kendisi.
- **Daha güçlü LLM:** qwen2.5:7b-instruct veya benzeri (TR talimat uyumu daha
  iyi). Donanım elverirse ölçülmeli.
- Bu iki değişiklikten SONRA Altın Test Seti C kategorisi (reddetme) yeniden
  koşulmalı.
