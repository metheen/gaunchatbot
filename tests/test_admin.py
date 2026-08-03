"""BİDB admin/telemetri: knowledge_gaps + raporlar + yetki kontrolü."""

import pytest


def _tmp(monkeypatch, tmp_path):
    import analytics
    monkeypatch.setattr(analytics, "DB_PATH", str(tmp_path / "t.db"))
    return analytics


def test_knowledge_gaps_bosluklari_toplar(monkeypatch, tmp_path):
    analytics = _tmp(monkeypatch, tmp_path)
    # general_knowledge (web'e düşen) + 'bulamadım' (RAG cevap üretemedi) = gap
    analytics.log_chat("docker nedir", "docker", "general_knowledge", 100, "Docker bir platform.")
    analytics.log_chat("docker nedir", "docker", "general_knowledge", 100, "Docker bir platform.")
    analytics.log_chat("xyz süreci", "xyz süreci", "semantic", 100, "Bu konuda bilgi bulamadım.")
    # normal cevaplanmış GAÜN sorusu → gap DEĞİL
    analytics.log_chat("harç ne kadar", "harç ücreti", "semantic", 100, "Harç ücreti 1000 TL'dir.")

    gaps = analytics.knowledge_gaps()
    topics = {g["topic"]: g for g in gaps}
    assert "docker" in topics and topics["docker"]["count"] == 2
    assert topics["docker"]["web_hits"] == 2
    assert "xyz süreci" in topics
    assert "harç ücreti" not in topics   # cevaplandı → boşluk değil


def test_top_upvoted_ve_poisoned_hidden(monkeypatch, tmp_path):
    import bot
    analytics = _tmp(monkeypatch, tmp_path)
    # popüler (3 IP 👍 → aktif)
    lid = analytics.log_chat("kütüphane saati", "kütüphane", "web", 50,
                             "Kütüphane 22:00'a kadar açıktır.")
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        bot.record_feedback(lid, 1, ip=ip)
    top = analytics.top_upvoted()
    assert top and top[0]["upvotes"] == 3

    # zehirlenme: -2'ye düşür → poisoned_hidden'da
    lid2 = analytics.log_chat("yanlış bilgi", "yanlış bilgi", "web", 50, "Yanlış bir cevap metni.")
    bot.record_feedback(lid2, 1, ip="9.9.9.1")
    for ip in ("9.9.9.2", "9.9.9.3", "9.9.9.4"):
        bot.record_feedback(lid2, -1, ip=ip)
    hidden = analytics.poisoned_hidden()
    assert any(h["approval_score"] <= -2 for h in hidden)


def test_require_admin_yetki_kontrolu(monkeypatch):
    import api
    from fastapi import HTTPException
    monkeypatch.setattr(api, "ADMIN_API_KEY", "gizli-anahtar")
    # doğru: X-API-Key
    assert api.require_admin(x_api_key="gizli-anahtar", authorization=None) is None
    # doğru: Bearer token
    assert api.require_admin(x_api_key=None, authorization="Bearer gizli-anahtar") is None
    # yanlış anahtar → 401
    with pytest.raises(HTTPException) as e:
        api.require_admin(x_api_key="yanlis", authorization=None)
    assert e.value.status_code == 401
    # eksik → 401
    with pytest.raises(HTTPException):
        api.require_admin(x_api_key=None, authorization=None)


def test_admin_key_yoksa_endpoint_kapali(monkeypatch):
    import api
    from fastapi import HTTPException
    monkeypatch.setattr(api, "ADMIN_API_KEY", "")   # tanımsız
    with pytest.raises(HTTPException) as e:
        api.require_admin(x_api_key="herhangi", authorization=None)
    assert e.value.status_code == 401


def test_admin_stats_json_yapisi(monkeypatch, tmp_path):
    import api
    _tmp(monkeypatch, tmp_path)
    api.analytics.log_chat("docker nedir", "docker", "general_knowledge", 100, "Docker platform.")
    out = api.admin_stats(_=None)   # dependency doğrulaması ayrı test edildi
    assert set(out.keys()) == {"summary", "knowledge_gaps", "feedback"}
    assert set(out["feedback"].keys()) == {"top_upvoted", "poisoning_hidden"}
    assert isinstance(out["knowledge_gaps"], list)
