#!/usr/bin/env python3
"""GAUN Chatbot — Telemetri & Analytics (Faz 15).

Hafif, bağımsız SQLite katmanı. bot.py her cevaptan sonra buraya log yazar;
api.py /api/feedback ile 👍/👎 skorunu günceller. Mevcut RAG/SQL/Live/Chitchat
mimarisini BOZMAZ — yalnız yan-kanal kayıt tutar.

chat_logs kolonları:
  id, timestamp, user_question, rewritten_question (jargon çevirisi),
  intent_route (sql/rag/live/chitchat...), response_time_ms, bot_answer,
  feedback_score (+1 / -1 / NULL)

Tüm fonksiyonlar db_path alır (test için); varsayılan gaunai_telemetry.db.
Her çağrı kendi bağlantısını açıp kapatır (thread-safe, FastAPI threadpool ile uyumlu).
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = os.getenv(
    "TELEMETRY_DB",
    str(Path(__file__).resolve().parent / "gaunai_telemetry.db"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_logs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp          TEXT    NOT NULL,
    user_question      TEXT,
    rewritten_question TEXT,
    intent_route       TEXT,
    response_time_ms   INTEGER,
    bot_answer         TEXT,
    feedback_score     INTEGER
);
CREATE TABLE IF NOT EXISTS learnings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    question_norm  TEXT UNIQUE,   -- normalize edilmiş soru (Türkçe katlama, bot.py)
    tokens         TEXT,          -- boşlukla ayrık içerik token'ları (örtüşme skoru)
    user_question  TEXT,          -- gösterim için ham soru
    answer         TEXT,          -- 👍 alan (güvenilir) cevap
    source         TEXT,
    upvotes        INTEGER DEFAULT 0,   -- FARKLI IP'lerden 👍 sayısı
    downvotes      INTEGER DEFAULT 0,   -- FARKLI IP'lerden 👎 sayısı
    approval_score INTEGER DEFAULT 0,   -- upvotes - downvotes
    active         INTEGER DEFAULT 0,   -- 1 ise RAG/eşleşmede KULLANILABİLİR (eşik aşıldı)
    hits           INTEGER DEFAULT 0,
    created_at     TEXT
);
-- Data-poisoning koruması: her (soru, IP) çifti EN FAZLA bir oy. Böylece tek bir
-- IP arka arkaya 👍'layıp eşiği (3 farklı IP) tek başına aşamaz.
CREATE TABLE IF NOT EXISTS learning_votes (
    question_norm  TEXT NOT NULL,
    ip             TEXT NOT NULL,
    vote           INTEGER NOT NULL,   -- +1 / -1
    voted_at       TEXT,
    UNIQUE(question_norm, ip)
);
"""

# Onay eşikleri (bot.py .env'den de okur; analytics varsayılanları burada):
APPROVAL_THRESHOLD = 3   # aktif olmak için gereken FARKLI-IP 👍 sayısı
REMOVE_SCORE = -2        # bu skora (veya altına) düşen öğrenme silinir


def _connect(db_path: "str | None" = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: "str | None" = None) -> None:
    """Tabloları (chat_logs + learnings + learning_votes, yoksa) oluşturur —
    idempotent. _SCHEMA birden çok CREATE içerdiği için executescript kullanılır.
    Eski (kolonsuz) learnings tablosuna eksik onay-puanı kolonlarını ekler."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        # Idempotent migration: önceki sürümden kalma learnings tablosuna yeni
        # kolonları ekle (SQLite ALTER kolon-varsa hata verir; o yüzden kontrol).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(learnings)")}
        for col, ddl in (
            ("upvotes", "INTEGER DEFAULT 0"),
            ("downvotes", "INTEGER DEFAULT 0"),
            ("approval_score", "INTEGER DEFAULT 0"),
            ("active", "INTEGER DEFAULT 0"),
        ):
            if col not in cols:
                conn.execute(f"ALTER TABLE learnings ADD COLUMN {col} {ddl}")


def log_chat(user_question: str, rewritten_question: str, intent_route: str,
             response_time_ms: int, bot_answer: str,
             db_path: "str | None" = None) -> int:
    """Bir sohbet turunu kaydeder ve eklenen kaydın id'sini döndürür."""
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO chat_logs (timestamp, user_question, rewritten_question, "
            "intent_route, response_time_ms, bot_answer, feedback_score) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), user_question,
             rewritten_question, intent_route, response_time_ms, bot_answer, None),
        )
        return int(cur.lastrowid)


def update_feedback(log_id: int, score: int, db_path: "str | None" = None) -> bool:
    """İlgili kaydın feedback_score'unu günceller. Kayıt bulunduysa True."""
    init_db(db_path)
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE chat_logs SET feedback_score = ? WHERE id = ?", (score, log_id))
        return cur.rowcount > 0


def vote_learning(question_norm: str, ip: str, vote: int, *,
                  tokens: str = "", user_question: str = "", answer: str = "",
                  source: str = "", threshold: int = APPROVAL_THRESHOLD,
                  remove_at: int = REMOVE_SCORE,
                  db_path: "str | None" = None) -> dict:
    """DATA-POISONING korumalı öğrenme oyu.

    Bir soru→cevap adayına IP'nin oyunu (👍/👎) işler. Kurallar:
      * Her (soru, IP) çifti EN FAZLA bir oy sayar (aynı IP tekrar oylarsa
        günceller) — tek IP eşiği tek başına aşamaz.
      * upvotes = FARKLI IP'lerden 👍 sayısı. active = upvotes >= threshold (3):
        yalnız o zaman find/eşleşmede KULLANILIR.
      * approval_score = upvotes - downvotes. Bu skor remove_at'e (-2) düşerse
        öğrenme VE oyları silinir (otomatik gizleme).
      * Aday YALNIZ olumlu (+1) ilk oyda ve geçerli cevapla oluşturulur; sonraki
        oylar cevabı DEĞİŞTİRMEZ (oy toplandıktan sonra içerik değiştirilemez).

    Döner: {created, active, approval_score, upvotes, downvotes, removed}."""
    if not question_norm or vote not in (1, -1):
        return {"created": False, "active": False, "approval_score": 0,
                "upvotes": 0, "downvotes": 0, "removed": False}
    init_db(db_path)
    with _connect(db_path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM learnings WHERE question_norm = ?", (question_norm,)).fetchone()
        created = False
        if exists is None:
            # Aday yalnız POZİTİF oy + geçerli cevapla doğar (👎 tek başına aday üretmez).
            if vote != 1 or not answer:
                return {"created": False, "active": False, "approval_score": 0,
                        "upvotes": 0, "downvotes": 0, "removed": False}
            conn.execute(
                "INSERT INTO learnings (question_norm, tokens, user_question, answer, "
                "source, upvotes, downvotes, approval_score, active, hits, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, ?)",
                (question_norm, tokens, user_question, answer, source,
                 datetime.now().isoformat(timespec="seconds")))
            created = True
        # IP başına tek oy (upsert) — farklı-IP şartını DB düzeyinde zorlar.
        conn.execute(
            "INSERT INTO learning_votes (question_norm, ip, vote, voted_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(question_norm, ip) DO UPDATE SET "
            "vote = excluded.vote, voted_at = excluded.voted_at",
            (question_norm, ip or "?", vote,
             datetime.now().isoformat(timespec="seconds")))
        up = conn.execute("SELECT COUNT(*) c FROM learning_votes "
                          "WHERE question_norm = ? AND vote = 1", (question_norm,)).fetchone()["c"]
        down = conn.execute("SELECT COUNT(*) c FROM learning_votes "
                            "WHERE question_norm = ? AND vote = -1", (question_norm,)).fetchone()["c"]
        score = up - down
        # POISONING: approval_score eşiğin altına düşerse GİZLE (active=0). Kayıt
        # SİLİNMEZ — BİDB raporunda 'öğrenme zehirlenmesine takılan girdiler'
        # olarak görünsün (soft-hide). Aksi halde ≥threshold farklı IP 👍 ile aktif.
        hidden = score <= remove_at
        active = 0 if hidden else (1 if up >= threshold else 0)
        conn.execute(
            "UPDATE learnings SET upvotes = ?, downvotes = ?, approval_score = ?, "
            "active = ? WHERE question_norm = ?",
            (up, down, score, active, question_norm))
        return {"created": created, "active": bool(active), "approval_score": score,
                "upvotes": up, "downvotes": down, "hidden": hidden}


def active_learnings(db_path: "str | None" = None) -> "list[dict]":
    """YALNIZ AKTİF (eşiği aşmış, active=1) öğrenilmiş kayıtları döndürür —
    bot.py bunları örtüşme skoruyla eşleştirir. Onaylanmamış adaylar dönmez."""
    init_db(db_path)
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT question_norm, tokens, user_question, answer, source "
            "FROM learnings WHERE active = 1")]


def all_learnings(db_path: "str | None" = None) -> "list[dict]":
    """TÜM öğrenilmiş kayıtları (aday + aktif) döndürür — admin/rapor için."""
    init_db(db_path)
    with _connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT question_norm, tokens, user_question, answer, source, "
            "upvotes, downvotes, approval_score, active FROM learnings")]


# ---------------------------------------------------------------------------
# BİDB Raporlama — bilgi boşluğu + geri bildirim/öğrenme (admin)
# ---------------------------------------------------------------------------

# Cevabın 'bulunamadı/yönlendirme' (RAG cevap üretemedi) olduğunu gösteren
# kalıplar — bilgi boşluğu tespiti için.
_GAP_ANSWER_PATTERNS = ("bilmiyorum", "bulamad", "bulunamad", "resmi sayfa",
                        "ziyaret edebilir", "danisin", "danismali")


def knowledge_gaps(limit: int = 15, db_path: "str | None" = None) -> "list[dict]":
    """BİLGİ BOŞLUĞU: RAG'in cevap ÜRETEMEDİĞİ (fallback/yönlendirme cevabı) ya da
    doğrudan web'e (general_knowledge) düşen EN SIK konular. BİDB bu listeyle
    hangi konularda SSS/mevzuat eksik olduğunu görüp içeriği zenginleştirir.

    Konu = rewritten_question (jargon-çevrilmiş/normalize soru). Yönlendirme
    cevapları (LIKE) + general_knowledge rotası birlikte sayılır."""
    init_db(db_path)
    like = " OR ".join(["LOWER(bot_answer) LIKE ?"] * len(_GAP_ANSWER_PATTERNS))
    params = [f"%{p}%" for p in _GAP_ANSWER_PATTERNS] + [limit]
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(rewritten_question,''), user_question) AS topic, "
            "COUNT(*) AS count, "
            "SUM(CASE WHEN intent_route='general_knowledge' THEN 1 ELSE 0 END) AS web_hits, "
            "SUM(CASE WHEN feedback_score=-1 THEN 1 ELSE 0 END) AS downvotes "
            "FROM chat_logs "
            f"WHERE intent_route='general_knowledge' OR {like} "
            "GROUP BY topic ORDER BY count DESC LIMIT ?", params)
        return [dict(r) for r in rows]


def top_upvoted(limit: int = 15, db_path: "str | None" = None) -> "list[dict]":
    """En popüler ONAYLANMIŞ (👍) öğrenilmiş cevaplar — hangi sorular en çok
    beğenildi + kaç kez sunuldu (hits)."""
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT user_question, upvotes, downvotes, hits, active "
            "FROM learnings WHERE upvotes > 0 "
            "ORDER BY upvotes DESC, hits DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


def poisoned_hidden(limit: int = 15, remove_at: int = REMOVE_SCORE,
                    db_path: "str | None" = None) -> "list[dict]":
    """ÖĞRENME ZEHİRLENMESİNE takılan (approval_score <= -2) GİZLENEN girdiler —
    çok 👎 alıp barajın altına düşmüş, artık sunulmayan öğrenmeler."""
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT user_question, upvotes, downvotes, approval_score "
            "FROM learnings WHERE approval_score <= ? "
            "ORDER BY approval_score ASC LIMIT ?", (remove_at, limit))
        return [dict(r) for r in rows]


def bump_learning_hit(question_norm: str, db_path: "str | None" = None) -> None:
    """Bir öğrenilmiş cevap kullanıldığında isabet sayacını artırır (analitik)."""
    with _connect(db_path) as conn:
        conn.execute("UPDATE learnings SET hits = hits + 1 WHERE question_norm = ?",
                     (question_norm,))


def get_log(log_id: int, db_path: "str | None" = None) -> "dict | None":
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM chat_logs WHERE id = ?", (log_id,)).fetchone()
        return dict(row) if row else None


def recent(limit: int = 10, db_path: "str | None" = None) -> "list[dict]":
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM chat_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def report(db_path: "str | None" = None, top: int = 5) -> dict:
    """Telemetriden analitik özet: toplam, rota dağılımı, ortalama süre,
    memnuniyet (👍/👎), en çok sorulan konular ve 👎 alan cevaplar."""
    init_db(db_path)
    with _connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) c FROM chat_logs").fetchone()["c"]
        by_route = {
            r["intent_route"] or "?": r["c"]
            for r in conn.execute(
                "SELECT intent_route, COUNT(*) c FROM chat_logs "
                "GROUP BY intent_route ORDER BY c DESC")
        }
        avg_ms = conn.execute(
            "SELECT AVG(response_time_ms) a FROM chat_logs "
            "WHERE response_time_ms IS NOT NULL").fetchone()["a"]
        up = conn.execute("SELECT COUNT(*) c FROM chat_logs WHERE feedback_score=1").fetchone()["c"]
        down = conn.execute("SELECT COUNT(*) c FROM chat_logs WHERE feedback_score=-1").fetchone()["c"]
        none = conn.execute("SELECT COUNT(*) c FROM chat_logs WHERE feedback_score IS NULL").fetchone()["c"]
        # En çok sorulan konular (jargon çevirisi = normalize edilmiş konu).
        top_topics = [
            {"topic": r["rewritten_question"], "count": r["c"]}
            for r in conn.execute(
                "SELECT rewritten_question, COUNT(*) c FROM chat_logs "
                "GROUP BY rewritten_question ORDER BY c DESC LIMIT ?", (top,))
        ]
        negatives = [
            {"user_question": r["user_question"], "bot_answer": r["bot_answer"]}
            for r in conn.execute(
                "SELECT user_question, bot_answer FROM chat_logs "
                "WHERE feedback_score=-1 ORDER BY id DESC LIMIT ?", (top,))
        ]
    return {
        "total": total,
        "by_route": by_route,
        "avg_response_ms": round(avg_ms, 1) if avg_ms is not None else None,
        "feedback": {"up": up, "down": down, "none": none},
        "top_topics": top_topics,
        "negatives": negatives,
    }


def _print_report(db_path: "str | None" = None) -> None:
    r = report(db_path)
    print("=== GaunAI Telemetri Özeti ===")
    print(f"Toplam sohbet    : {r['total']}")
    print(f"Ortalama süre    : {r['avg_response_ms']} ms")
    fb = r["feedback"]
    print(f"Memnuniyet       : 👍 {fb['up']}  👎 {fb['down']}  (skorsuz {fb['none']})")
    print("Rota dağılımı    : " + (", ".join(f"{k}={v}" for k, v in r["by_route"].items()) or "-"))
    print("\nEn çok sorulan konular:")
    for t in r["top_topics"]:
        print(f"  [{t['count']}x] {t['topic']}")
    if r["negatives"]:
        print("\n👎 alan cevaplar (iyileştirilecek):")
        for n in r["negatives"]:
            print(f"  S: {n['user_question'][:60]}")
            print(f"     C: {(n['bot_answer'] or '')[:80]}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="GaunAI telemetri")
    ap.add_argument("--recent", type=int, metavar="N", help="son N kaydı yazdır")
    args = ap.parse_args()
    if args.recent:
        for row in recent(args.recent):
            print(row)
    else:
        _print_report()
