#!/usr/bin/env python3
"""GaunAI — Kaos Mühendisliği (Chaos Engineering) test paketi.

Mantıksal değil FİZİKSEL/ALTYAPISAL dayanıklılık: eşzamanlılık, bağlam şişmesi,
çok-alfabe girdi, bağımlılık kesintisi. Sistemi "kırmaya" çalışır ve ÇÖKMEDEN
güvenli davranıp davranmadığını doğrular.

4 senaryo:
  1. Concurrency   — 10 eşzamanlı semantik istek (asyncio.gather + ASGI transport)
  2. Context bloat — 4×1999 karakter history + RAG sorusu (token limiti taşar mı)
  3. Alfabe/dil    — Rusça/Arapça kampüs sorusu (router yakalıyor mu, web'e kaçıyor mu)
  4. DB kesintisi  — Qdrant down → 500/traceback sızıyor mu, zarifçe mi yakalanıyor

İKİ KATMAN:
  * HIZLI (stub/deterministik) — LLM/DB gerektirmez, her koşuda çalışır.
  * CANLI (GAUNAI_LIVE=1)      — gerçek LLM/RAG/eşzamanlılık ile uçtan uca.

Çalıştırma:
  ./.venv/bin/pytest test_chaos_engineering.py -v
  GAUNAI_LIVE=1 ./.venv/bin/pytest test_chaos_engineering.py -v -s
"""

import asyncio
import os

import pytest

import intent_router as ir

LIVE = os.getenv("GAUNAI_LIVE", "").strip() in ("1", "true", "yes", "on")
live_only = pytest.mark.skipif(not LIVE, reason="canlı LLM/RAG gerekir (GAUNAI_LIVE=1)")

# --- Senaryo girdileri ------------------------------------------------------
# 10 FARKLI semantik (RAG) soru — hepsi üniversite kapsamında, çoğu SSS.
CONCURRENT_QS = [
    "yatay geçiş şartları nelerdir?",
    "kayıt dondurma nasıl yapılır?",
    "mazeret sınavı başvurusu nasıl olur?",
    "bütünleme sınavına kimler girebilir?",
    "staj zorunlu mu?",
    "çift anadal şartları nedir?",
    "not yükseltmek için ne yapmalıyım?",
    "azami öğrenim süresi ne kadar?",
    "disiplin cezaları nelerdir?",
    "yaz okulu ücreti nasıl hesaplanır?",
]
RU_QUESTION = "Где находится библиотека?"          # Kütüphane nerede? (Rusça)
AR_QUESTION = "أين تقع المكتبة في الجامعة؟"          # Üniversitedeki kütüphane nerede? (Arapça)
BLOAT_MSG = "A" * 1999                              # Pydantic history sınırına yakın tek mesaj


def _fresh_client(monkeypatch, stub=None):
    """Rate-limit'i devre dışı bırakılmış TestClient (fonksiyonel testler için —
    modül-düzeyi _chat_limiter testler arası dolup flaky yapmasın). stub verilirse
    bot.answer_with_telemetry onunla değiştirilir."""
    import api
    import bot
    from fastapi.testclient import TestClient
    from ratelimit import RateLimiter
    monkeypatch.setattr(api, "_chat_limiter", RateLimiter(10_000))
    if stub is not None:
        monkeypatch.setattr(bot, "answer_with_telemetry", stub)
        monkeypatch.setattr(api.bot, "answer_with_telemetry", stub)
    return TestClient(api.app, raise_server_exceptions=False)


# ===========================================================================
# SENARYO 1 — CONCURRENCY (Kuyruk Testi)
# ===========================================================================

def test_s1_concurrency_stub_hepsi_200(monkeypatch):
    """10 EŞZAMANLI istek (asyncio.gather + ASGI). LLM stub'lanır (I/O
    beklemesi taklit) — burada MODELİ değil, API'nin eşzamanlılık dayanıklılığını
    (threadpool + executor kuyruğu) test ederiz: çökme/500/exception OLMAMALI,
    hepsi güvenli 200 dönmeli."""
    import time as _t

    import httpx
    import api

    def stub(q, h=None):
        _t.sleep(0.05)  # iş yükü taklidi → istekler gerçekten çakışsın
        return {"answer": f"OK:{q[:20]}", "log_id": 1}

    _fresh_client(monkeypatch, stub)  # rate-limit + stub kur (client'ı ASGI ile açacağız)

    async def _fire():
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chaos") as ac:
            tasks = [ac.post("/api/chat", json={"question": q}) for q in CONCURRENT_QS]
            return await asyncio.gather(*tasks, return_exceptions=True)

    results = asyncio.run(_fire())
    # Hiçbiri exception (bağlantı düşmesi/çökme) olmamalı
    excs = [r for r in results if isinstance(r, Exception)]
    assert not excs, f"eşzamanlılıkta exception: {excs}"
    # Hepsi 200 (500 YOK)
    codes = [r.status_code for r in results]
    assert all(c == 200 for c in codes), f"beklenmeyen kodlar: {codes}"
    assert len(results) == 10


def test_s1_rate_limit_guvenli_reddeder(monkeypatch):
    """Eşzamanlılığın SINIRI: rate-limit aşılınca 500 değil, güvenli 200 mesajı
    (spam/DoS koruması istisna fırlatmaz)."""
    import api
    import bot
    from fastapi.testclient import TestClient
    from ratelimit import RateLimiter
    monkeypatch.setattr(api, "_chat_limiter", RateLimiter(3))  # dakikada 3
    monkeypatch.setattr(bot, "answer_with_telemetry",
                        lambda q, h=None: {"answer": "ok", "log_id": 1})
    monkeypatch.setattr(api.bot, "answer_with_telemetry",
                        lambda q, h=None: {"answer": "ok", "log_id": 1})
    client = TestClient(api.app, raise_server_exceptions=False)
    codes, bodies = [], []
    for _ in range(6):  # 3'ü geçer, kalanı limitlenir
        r = client.post("/api/chat", json={"question": "merhaba"})
        codes.append(r.status_code)
        bodies.append(r.json()["answer"])
    assert all(c == 200 for c in codes), f"rate-limit 200 olmalı: {codes}"
    assert any("fazla istek" in b.lower() for b in bodies), "limit mesajı yok"


@live_only
def test_s1_concurrency_canli_hepsi_200():
    """10 GERÇEK semantik soru eşzamanlı (canlı LLM/RAG). Sistem kuyruğu yönetip
    (executor max_workers + REQUEST_TIMEOUT) hepsine güvenli 200 dönmeli —
    çökme/500 YOK. (Yavaş: ollama üretimleri serileşir.)"""
    import httpx
    import api

    async def _fire():
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://chaos",
                                     timeout=180) as ac:
            tasks = [ac.post("/api/chat", json={"question": q}) for q in CONCURRENT_QS]
            return await asyncio.gather(*tasks, return_exceptions=True)

    results = asyncio.run(_fire())
    excs = [r for r in results if isinstance(r, Exception)]
    assert not excs, f"canlı eşzamanlılıkta exception: {excs}"
    codes = [r.status_code for r in results]
    assert all(c == 200 for c in codes), f"canlı kodlar: {codes}"
    # Her cevap boş olmayan bir string olmalı (kısmi çökme yok)
    for r in results:
        assert isinstance(r.json().get("answer"), str) and r.json()["answer"]


# ===========================================================================
# SENARYO 2 — CONTEXT BLOAT (Token/Bağlam sınırı)
# ===========================================================================

def test_s2_bloat_history_sanitize_son4(monkeypatch):
    """4×1999 karakterlik history → _sanitize_history son 4'ü tutar, çökmez.
    (Bağlam penceresi patlamasına karşı ilk savunma.)"""
    import bot
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": BLOAT_MSG}
            for i in range(4)]
    out = bot._sanitize_history(hist)
    assert len(out) == 4
    assert all(len(m["content"]) == 1999 for m in out)


def test_s2_bloat_api_kabul_eder(monkeypatch):
    """API, 4×1999 history + soruyu 422 vermeden kabul etmeli (history sınırı 20
    ITEM; karakter değil). LLM stub — burada Pydantic/taşıma katmanını test ederiz."""
    hist = [{"role": "user", "content": BLOAT_MSG} for _ in range(4)]
    client = _fresh_client(
        monkeypatch, stub=lambda q, h=None: {"answer": f"hist={len(h or [])}", "log_id": 1})
    r = client.post("/api/chat", json={"question": "yatay geçiş şartları?", "history": hist})
    assert r.status_code == 200, r.text
    assert r.json()["answer"] == "hist=4"


@live_only
def test_s2_bloat_canli_cokmez():
    """4×1999 history + RAG sorusu GERÇEK modele gider — token limiti aşıp
    çökmemeli (qwen2.5 32k bağlam; ~3-4k token güvenli). Geçerli cevap dönmeli."""
    import bot
    hist = [{"role": "user" if i % 2 == 0 else "assistant",
             "content": ("Soru bağlamı " + BLOAT_MSG)[:1999]} for i in range(4)]
    r = bot.answer_with_telemetry("kayıt dondurma nasıl yapılır?", hist)
    assert isinstance(r["answer"], str) and len(r["answer"]) > 0
    assert r.get("intent") not in (None, "error")


# ===========================================================================
# SENARYO 3 — ALFABE / DİL ŞAŞIRTMACASI (Rusça / Arapça)
# ===========================================================================

def test_s3_kiril_arap_general_knowledge():
    """BULGU: _norm_query [^a-z0-9] olan HER şeyi (Kiril/Arap harfleri) siler →
    saf gayri-Latin girdi boş normalize olup 'general_knowledge'a düşer.
    GAUN_ONLY_MODE (varsayılan) sayesinde web'e SIZMAZ ama SORU DA YANITLANMAZ."""
    assert ir.classify_intent(RU_QUESTION) == "general_knowledge"
    assert ir.classify_intent(AR_QUESTION) == "general_knowledge"
    # needs_gaun_scope False = kilitli olmasa SAF web'e gider (izolasyon yok) —
    # riski belgeler. Latin harfli aynı soru ise doğru şekilde GAÜN kapsamında.
    assert ir.needs_gaun_scope(RU_QUESTION) is False
    assert ir.needs_gaun_scope("kütüphane nerede") is True


def test_s3_latin_gomulu_gaun_sinyali_yakalanir():
    """Kiril içinde Latin GAÜN sinyali varsa ('GAUN kütüphane где') yine de
    semantik (kapsam içi) yakalanır — kısmi dayanıklılık."""
    assert ir.classify_intent("GAUN kütüphane где") == "semantic"


@live_only
def test_s3_kiril_web_e_sizmaz():
    """Rusça kampüs sorusu GAUN_ONLY_MODE'da web'e SIZMAMALI — kibar kapsam
    reddi dönmeli (dış üniversite/genel web sonucu değil)."""
    import bot
    ans = bot.answer_with_telemetry(RU_QUESTION, [])["answer"].lower()
    # Kapsam reddi işaretleri; harici web cevabı (adres/link) OLMAMALI
    assert any(k in ans for k in ("gaün", "gaun", "üniversite", "yalnız")), ans[:160]


# ===========================================================================
# SENARYO 4 — DB / BAĞIMLILIK KESİNTİSİ
# ===========================================================================

def test_s4_db_kesintisi_api_zarif_yakalar(monkeypatch):
    """Qdrant/DB down → bot ham bağlantı hatası fırlatsa bile API katmanı
    YAKALAMALI: 500 değil 200, gövdede GÜVENLİ mesaj, TRACEBACK/kod SIZMAMALI."""
    def boom(*a, **k):
        raise ConnectionError("[Errno 61] Connection refused (simulated qdrant down)")

    client = _fresh_client(monkeypatch, stub=boom)
    r = client.post("/api/chat", json={"question": "kayıt dondurma nasıl yapılır?"})
    assert r.status_code == 200, f"500 sızdı: {r.status_code}"
    ans = r.json()["answer"]
    assert "hata" in ans.lower(), f"güvenli hata mesajı değil: {ans!r}"
    # Ham iç ayrıntı SIZMAMALI (traceback / dosya yolu / SQL / stack)
    for leak in ("Traceback", 'File "', "line ", "/Users/", "mysql", "qdrant",
                 "Errno", "Connection refused"):
        assert leak not in ans, f"iç ayrıntı sızdı ({leak!r}): {ans!r}"


def test_s4_db_kesintisi_feedback_de_zarif(monkeypatch):
    """Feedback ucu da DB hatasında çökmemeli — {ok: False, error:...} döner."""
    import api
    import bot
    from fastapi.testclient import TestClient
    from ratelimit import RateLimiter
    monkeypatch.setattr(api, "_feedback_limiter", RateLimiter(10_000))

    def boom(*a, **k):
        raise ConnectionError("db down")
    monkeypatch.setattr(bot, "record_feedback", boom)
    monkeypatch.setattr(api.bot, "record_feedback", boom)
    client = TestClient(api.app, raise_server_exceptions=False)
    r = client.post("/api/feedback", json={"log_id": 1, "score": 1})
    assert r.status_code == 200
    assert r.json()["ok"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
