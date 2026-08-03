#!/usr/bin/env python3
"""GAUN Chatbot — FastAPI köprüsü (Faz 8).

Yerel RAG motoru (bot.answer_question) ile web arayüzü (static/index.html)
arasında ince bir köprü. Local AI/RAG mimarisini BOZMAZ; yalnız HTTP taşır.

Çalıştırma:
  ./.venv/bin/uvicorn api:app --reload --host 0.0.0.0 --port 8000

Uçlar:
  GET  /             -> static/index.html (widget)
  POST /api/chat     -> {"question","history"} -> {"answer","log_id"}
  POST /api/feedback -> {"log_id","score"} -> {"ok": bool}
  GET  /api/health   -> {"status": "ok"}
"""

import hmac
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import analytics
import bot
from ratelimit import RateLimiter

# .env'i IMPORT anında yükle: ADMIN_API_KEY, ALLOWED_ORIGINS, rate-limit gibi
# modül-düzeyi ayarlar aşağıda okunuyor; bot.py .env'i istek anında yüklediği
# için burada erken yükleme şart (aksi halde ADMIN_API_KEY boş kalır → 401).
load_dotenv()

# BİDB admin/telemetri endpoint'i için yetki anahtarı. BOŞSA endpoint TAMAMEN
# kapalıdır (401) — güvenli varsayılan. Production'da .env'de MUTLAKA ayarlanır.
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()

# GÜVENLİK/DAYANIKLILIK sınırları: aşırı büyük girdi LLM/DB'yi yormasın (DoS),
# geçmiş penceresi şişmesin. Hepsi .env ile ayarlanabilir.
MAX_QUESTION_CHARS = int(os.getenv("MAX_QUESTION_CHARS", "2000"))
MAX_HISTORY_ITEMS = int(os.getenv("MAX_HISTORY_ITEMS", "20"))
# Kurumsal serving: IP başına dakikalık istek sınırı (spam/DoS), istek zaman
# aşımı (asılı LLM çağrısı sistemi kilitlemesin), eşzamanlı iş havuzu.
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "20"))
REQUEST_TIMEOUT_SEC = float(os.getenv("REQUEST_TIMEOUT_SEC", "60"))
MAX_WORKERS = int(os.getenv("API_MAX_WORKERS", "4"))

_chat_limiter = RateLimiter(RATE_LIMIT_PER_MIN)
_feedback_limiter = RateLimiter(int(os.getenv("FEEDBACK_LIMIT_PER_MIN", "30")))
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="gaunai")


def _client_ip(request: Request) -> str:
    """İstemci IP'si — ters proxy arkasındaysa X-Forwarded-For'un ilk değeri."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
logging.getLogger("bot").setLevel(logging.INFO)
log = logging.getLogger("api")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
INDEX_HTML = STATIC_DIR / "index.html"
EMBED_JS = STATIC_DIR / "embed.js"

app = FastAPI(title="GaunAI", version="1.0")

# CORS: .env'deki ALLOWED_ORIGINS (virgülle ayrık) ile sınırlanır — gaziantep.edu.tr
# gibi gömüldüğü gerçek origin(ler) buraya yazılır. Boşsa (yerel/dev) "*" ile
# geliştirme kolaylığı korunur; production'da ALLOWED_ORIGINS MUTLAKA ayarlanmalı.
_allowed = os.getenv("ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGINS = [o.strip() for o in _allowed.split(",") if o.strip()] or ["*"]
if ALLOWED_ORIGINS == ["*"]:
    # Kurumsal/production'da MUTLAKA daraltılmalı — açık kalırsa görünür uyar.
    log.warning("CORS ALLOWED_ORIGINS='*' (herkese açık) — production'da "
                ".env'de ALLOWED_ORIGINS=https://www.gaziantep.edu.tr olarak ayarlayın.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/embed.js")
def embed_js() -> FileResponse:
    """Üçüncü taraf sayfalara (ör. gaziantep.edu.tr) gömülen widget script'i."""
    return FileResponse(str(EMBED_JS), media_type="application/javascript")


class ChatRequest(BaseModel):
    # max_length: aşırı uzun girdiyle LLM/DB'yi yorma (DoS) koruması.
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    # geçmiş penceresi sınırlı (bot zaten son 4'ü alır; burada üst sınır DoS için).
    history: list[dict] = Field(default_factory=list, max_length=MAX_HISTORY_ITEMS)


class ChatResponse(BaseModel):
    answer: str
    log_id: "int | None" = None   # telemetri kaydı; feedback bununla ilişkilenir


class FeedbackRequest(BaseModel):
    log_id: int = Field(ge=1)
    score: Literal[-1, 1]   # yalnız +1 (👍) veya -1 (👎) — model düzeyinde kısıt


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(INDEX_HTML))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# NOT: endpoint 'def' (async değil) — FastAPI bunu threadpool'da çalıştırır,
# böylece bloklu LLM/DB çağrısı event loop'u kilitlemez.
@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, request: Request) -> ChatResponse:
    if not _chat_limiter.allow(_client_ip(request)):
        return ChatResponse(
            answer="Çok fazla istek gönderdiniz. Lütfen biraz bekleyip tekrar deneyin.")
    question = (req.question or "").strip()
    if not question:
        return ChatResponse(answer="Lütfen bir soru yazın.")
    try:
        # Zaman aşımı: asılı bir LLM/ağ çağrısı isteği (ve worker'ı) süresiz
        # kilitlemesin — ayrı havuzda çalıştırıp REQUEST_TIMEOUT_SEC bekle.
        fut = _executor.submit(bot.answer_with_telemetry, question, req.history)
        result = fut.result(timeout=REQUEST_TIMEOUT_SEC)
        return ChatResponse(answer=result["answer"], log_id=result.get("log_id"))
    except FutureTimeout:
        log.warning("chat zaman aşımı (%.0fs): %r", REQUEST_TIMEOUT_SEC, question[:80])
        return ChatResponse(
            answer="Yanıt beklenenden uzun sürdü. Lütfen tekrar deneyin.")
    except Exception as exc:  # köprü çökmesin; hatayı kullanıcıya sade döndür
        log.exception("chat hatası")
        return ChatResponse(answer=f"Sistemde bir hata oluştu: {type(exc).__name__}")


@app.post("/api/feedback")
def feedback(req: FeedbackRequest, request: Request) -> dict:
    """Kullanıcının 👍/👎 skorunu ilgili log kaydına işler."""
    if not _feedback_limiter.allow(_client_ip(request)):
        return {"ok": False, "error": "rate_limited"}
    try:
        # record_feedback: skoru kaydeder VE data-poisoning korumalı öğrenme
        # oyunu işler (≥3 farklı IP 👍 → aktif; -2 → gizle). IP çağrıdan geçer.
        ok = bot.record_feedback(req.log_id, req.score, _client_ip(request))
        return {"ok": ok}
    except Exception as exc:
        log.exception("feedback hatası")
        return {"ok": False, "error": type(exc).__name__}


def require_admin(
    x_api_key: "str | None" = Header(default=None, alias="X-API-Key"),
    authorization: "str | None" = Header(default=None),
) -> None:
    """BİDB admin yetki kontrolü: X-API-Key VEYA Authorization: Bearer <key>.
    ADMIN_API_KEY tanımsızsa endpoint TAMAMEN kapalı (401). Sabit-zamanlı
    karşılaştırma (hmac.compare_digest) — zamanlama saldırısına dirençli."""
    sunulan = x_api_key
    if not sunulan and authorization and authorization.lower().startswith("bearer "):
        sunulan = authorization.split(" ", 1)[1].strip()
    if not ADMIN_API_KEY or not sunulan or not hmac.compare_digest(sunulan, ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Geçersiz veya eksik yetki anahtarı")


@app.get("/api/admin/stats")
def admin_stats(_: None = Depends(require_admin)) -> dict:
    """BİDB TELEMETRİ & RAPORLAMA (yetki gerekli). Saf JSON döner:
      - summary        : genel sağlık (toplam, rota dağılımı, süre, 👍/👎)
      - knowledge_gaps : RAG cevap üretemeyen / web'e düşen en sık konular
      - feedback.top_upvoted     : en popüler (👍) öğrenilmiş cevaplar
      - feedback.poisoning_hidden: zehirlenme barajına (≤-2) takılıp gizlenenler
    """
    return {
        "summary": analytics.report(),
        "knowledge_gaps": analytics.knowledge_gaps(),
        "feedback": {
            "top_upvoted": analytics.top_upvoted(),
            "poisoning_hidden": analytics.poisoned_hidden(),
        },
    }
