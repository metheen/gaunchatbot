# GaunAI — Gaziantep Üniversitesi Hibrit RAG Asistanı

> Üniversite web sitesine gömülebilen, Türkçe konuşan, kaynağa dayalı (grounded) bir soru-cevap asistanı.
> Yapısal sorgular için **SQL**, serbest metin için **RAG** (vektör arama + yerel LLM) kullanan hibrit bir mimari.

*A Turkish, source-grounded Q&A assistant for Gaziantep University — hybrid SQL + RAG architecture with a local LLM, fully self-hostable and privacy-preserving.*

---

## ✨ Öne Çıkanlar

- **Hibrit yönlendirme** — Kesin veri gerektiren sorular (personel, birim, iletişim) doğrudan **SQL**'den; serbest metin/mevzuat soruları **RAG** ile yanıtlanır. LLM asla kesin veri uydurmaz.
- **Yerel & gizlilik dostu** — Embedding ve üretim tamamen **Ollama** (bge-m3 + qwen2.5) ile yerelde çalışır; veri üçüncü taraf API'lere gitmez.
- **Kapsam kilidi** — Bot yalnızca üniversiteyle ilgili sorulara cevap verir; alakasız/genel sorular kibarca reddedilir (`GAUN_ONLY_MODE`).
- **Güvenlik kalkanı** — Statik prompt-injection filtresi, rate-limit, girdi boyutu sınırı, istek zaman aşımı ve CORS origin kilidi.
- **Çok katmanlı fallback** — RAG → canlı web araması (SearXNG/DuckDuckGo/Wikipedia) → kibar özür; paralel I/O ile tek LLM üretimi.
- **Canlı veri** — Yemek menüsü ve duyurular gibi anlık konular her soruda taze çekilir (deterministik tarih/saat, `+03:00`).
- **Telemetri & öğrenme** — 👍/👎 geri bildirimi kaydedilir; anonim analitik BİDB paneline sunulur.
- **Gömülebilir widget** — `static/embed.js` ile herhangi bir sayfaya tek satırda eklenir.

## 🏗️ Mimari

```
Kullanıcı ──▶ embed.js (widget)
                  │
                  ▼
           FastAPI köprüsü (api.py)  ──  rate-limit · CORS · injection kalkanı
                  │
                  ▼
        intent_router.py  ── soruyu sınıflandırır
             │            │
     yapısal │            │ serbest metin
             ▼            ▼
     MariaDB (SQL)   rag_pipeline.py ──▶ Qdrant (bge-m3 vektörleri)
                              │
                              ▼
                     Ollama (qwen2.5:7b-instruct)  ── kaynağa dayalı üretim
                              │
                     fallback ▼
              web_search.py (SearXNG → DuckDuckGo → Wikipedia)
```

## 🧰 Teknoloji Yığını

| Katman | Teknoloji |
|---|---|
| Web köprüsü | FastAPI + Uvicorn |
| Vektör deposu | Qdrant (`bge-m3`, 1024 boyut, çok dilli) |
| Yapısal veri | MariaDB |
| LLM (yerel) | Ollama — `qwen2.5:7b-instruct` |
| Önbellek | DiskCache |
| Tarayıcılar | BeautifulSoup + PyMuPDF (mevzuat PDF) |

## 🚀 Hızlı Başlangıç

**Gereksinimler:** Docker + Docker Compose, [Ollama](https://ollama.com), Python 3.11+

```bash
# 1) Ortam değişkenleri
cp .env.example .env && chmod 600 .env      # CHANGE_ME değerlerini doldur

# 2) Yerel LLM modelleri
ollama pull bge-m3
ollama pull qwen2.5:7b-instruct

# 3) Altyapı (Qdrant + MariaDB)
docker compose up -d qdrant mariadb

# 4) Python bağımlılıkları
python -m venv .venv && ./.venv/bin/pip install -r requirements.txt

# 5) Şema (yapısal tablolar)
#    Veri seti (personel dökümü) bu repoda YER ALMAZ — bkz. "Veri" bölümü.
docker exec -i gaun_mariadb mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" \
  gaun_assistant < db/migrations/001_entity_tables.sql

# 6) Sunucuyu başlat
./.venv/bin/python local_server.py          # http://127.0.0.1:8000
# veya production ASGI:
./.venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000
```

CLI ile tek soru:
```bash
./.venv/bin/python bot.py ask "Bilgi İşlem Daire Başkanı kimdir?"
```

## ⚙️ Yapılandırma

Tüm ayarlar `.env` üzerinden yönetilir — açıklamalı tam liste için [`.env.example`](.env.example).
Öne çıkanlar: `QDRANT_URL`, `OLLAMA_LLM_MODEL`, `RAG_TOP_K`, `RAG_SCORE_THRESHOLD`,
`GAUN_ONLY_MODE`, `ALLOWED_ORIGINS`, `RATE_LIMIT_PER_MIN`, `ADMIN_API_KEY`.

## 🔐 Güvenlik

- **Sırlar koda gömülmez** — tümü `.env`'de; `.env` asla commit edilmez.
- Prompt-injection için statik kalkan (`security_guard.py`).
- IP başına rate-limit, girdi boyutu sınırı, istek zaman aşımı (DoS azaltma).
- Production'da `ALLOWED_ORIGINS` **mutlaka** ayarlanmalı (aksi halde CORS açık kalır).
- Admin telemetri endpoint'i `ADMIN_API_KEY` boşsa tamamen kapalıdır (401).

## 🗂️ Veri

Bu depo **yalnızca kaynak kodu** içerir. Aşağıdakiler bilinçli olarak **dahil edilmemiştir**:

- **Personel/birim veri dökümü** (`db/staff_seed.sql`) — gerçek kişisel veri içerir (KVKK).
- **Taranmış bilgi tabanı** (`offline_data/`) — kurumsal içerik.
- Telemetri veritabanı, loglar, yerel önbellek ve tüm `.env` dosyaları.

Şema (`db/migrations/`) dahildir; veri seti kendi taramanızla (`crawler_rehber.py`,
`gaun_crawler.py`) yeniden üretilebilir.

## 📁 Proje Yapısı

```
api.py               FastAPI köprüsü (rate-limit, CORS, admin telemetri)
bot.py               RAG asistanı çekirdeği + CLI (ask/repl)
intent_router.py     soru sınıflandırma / hibrit yönlendirme
rag_pipeline.py      retrieval + grounding + üretim
web_search.py        çok-sağlayıcılı canlı web araması (fallback)
security_guard.py    statik prompt-injection kalkanı
live_fetcher.py      anlık konular (yemek/duyuru)
analytics.py         anonim telemetri & BİDB raporu
local_server.py      bağımlılıksız yerel geliştirme sunucusu
static/              gömülebilir widget (embed.js, index.html)
crawler_*.py         veri toplama (rehber/mevzuat/yönetmelik)
db/migrations/       şema (entity tabloları)
tests/               birim + kaos/edge-case testleri
docker-compose.yml   Qdrant + MariaDB altyapısı
```

## 🧪 Testler

```bash
./.venv/bin/pytest -q
```

## 📄 Lisans

Tüm hakları saklıdır. Aksi belirtilmedikçe bu kod izinsiz kullanılamaz.
