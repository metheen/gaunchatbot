"""analytics telemetri katmanı testleri (izole SQLite; ağ/LLM gerektirmez)."""

import analytics


def test_log_ve_get(tmp_path):
    db = str(tmp_path / "t.db")
    lid = analytics.log_chat(
        user_question="Hoca çan eğrisi yapar mı?",
        rewritten_question="Bağıl değerlendirme sistemi nasıl uygulanır?",
        intent_route="semantic", response_time_ms=1234,
        bot_answer="Bağıl değerlendirme otomatik uygulanmaz.", db_path=db)
    assert isinstance(lid, int) and lid > 0
    row = analytics.get_log(lid, db_path=db)
    assert row["user_question"] == "Hoca çan eğrisi yapar mı?"
    assert row["rewritten_question"] == "Bağıl değerlendirme sistemi nasıl uygulanır?"
    assert row["intent_route"] == "semantic"
    assert row["response_time_ms"] == 1234
    assert row["feedback_score"] is None  # başlangıçta boş
    assert row["timestamp"]


def test_update_feedback(tmp_path):
    db = str(tmp_path / "t.db")
    lid = analytics.log_chat("q", "r", "chitchat", 10, "a", db_path=db)
    assert analytics.update_feedback(lid, 1, db_path=db) is True
    assert analytics.get_log(lid, db_path=db)["feedback_score"] == 1
    # olmayan kayıt -> False
    assert analytics.update_feedback(99999, -1, db_path=db) is False


def test_recent_en_yeni_once(tmp_path):
    db = str(tmp_path / "t.db")
    for i in range(3):
        analytics.log_chat(f"q{i}", "r", "semantic", 1, "a", db_path=db)
    rows = analytics.recent(2, db_path=db)
    assert len(rows) == 2
    assert rows[0]["user_question"] == "q2"  # en yeni önce


def test_report_ozet(tmp_path):
    db = str(tmp_path / "t.db")
    a = analytics.log_chat("çan var mı", "Bağıl değerlendirme", "semantic", 100, "cevap1", db_path=db)
    analytics.log_chat("bugün yemek", "Günün menüsü", "live", 200, "cevap2", db_path=db)
    analytics.log_chat("çan var mı", "Bağıl değerlendirme", "semantic", 300, "cevap3", db_path=db)
    analytics.update_feedback(a, 1, db_path=db)         # 👍
    b = analytics.log_chat("kötü soru", "kötü", "semantic", 50, "yanlış cevap", db_path=db)
    analytics.update_feedback(b, -1, db_path=db)        # 👎

    r = analytics.report(db_path=db)
    assert r["total"] == 4
    assert r["by_route"] == {"semantic": 3, "live": 1}
    assert r["avg_response_ms"] == 162.5  # (100+200+300+50)/4
    assert r["feedback"] == {"up": 1, "down": 1, "none": 2}
    assert r["top_topics"][0] == {"topic": "Bağıl değerlendirme", "count": 2}
    assert r["negatives"][0]["user_question"] == "kötü soru"
