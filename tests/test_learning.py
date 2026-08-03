"""Öğrenme deposu + DATA-POISONING korumaları (👍/👎 eşik mantığı) testleri.

Kurallar: bir bilgi ancak ≥3 FARKLI IP'den 👍 alınca AKTİF olur (sunulur);
approval_score -2'ye düşünce otomatik silinir; her (soru, IP) çifti tek oy sayar.
Hepsi geçici SQLite ile (gerçek DB'ye dokunmaz)."""


def _tmp_db(monkeypatch, tmp_path):
    import analytics
    monkeypatch.setattr(analytics, "DB_PATH", str(tmp_path / "t.db"))
    return analytics


def _log(analytics, q="kütüphane kaça kadar açık", ans="Kütüphane 22:00'a kadar açıktır."):
    return analytics.log_chat(q, "kütüphane saati", "web", 100, ans)


def test_tek_begeni_aktive_ETMEZ(monkeypatch, tmp_path):
    """Tek 👍 yalnız ADAY oluşturur; eşik (3 IP) aşılmadan sunulmaz."""
    import bot
    _tmp_db(monkeypatch, tmp_path)
    lid = _log(bot.analytics)
    bot.record_feedback(lid, 1, ip="1.1.1.1")
    assert bot.find_learned_answer("kütüphane kaça kadar açık") is None


def test_uc_farkli_ip_begeni_aktive_eder(monkeypatch, tmp_path):
    import bot
    _tmp_db(monkeypatch, tmp_path)
    lid = _log(bot.analytics)
    bot.record_feedback(lid, 1, ip="1.1.1.1")
    bot.record_feedback(lid, 1, ip="2.2.2.2")
    assert bot.find_learned_answer("kütüphane kaça kadar açık") is None   # 2 < 3
    bot.record_feedback(lid, 1, ip="3.3.3.3")                            # 3. farklı IP
    got = bot.find_learned_answer("kütüphane kaça kadar açık")
    assert got is not None and "22:00" in got


def test_ayni_ip_uc_kez_aktive_ETMEZ(monkeypatch, tmp_path):
    """Farklı-IP şartı: aynı IP 3 kez 👍'lasa da tek oy sayılır → aktif olmaz."""
    import bot
    _tmp_db(monkeypatch, tmp_path)
    lid = _log(bot.analytics)
    for _ in range(3):
        bot.record_feedback(lid, 1, ip="9.9.9.9")
    assert bot.find_learned_answer("kütüphane kaça kadar açık") is None
    # DB'de tek upvote sayılmalı
    row = [r for r in bot.analytics.all_learnings()][0]
    assert row["upvotes"] == 1 and row["active"] == 0


def test_onayli_bilgi_eksi_ikide_gizlenir(monkeypatch, tmp_path):
    import bot
    _tmp_db(monkeypatch, tmp_path)
    lid = _log(bot.analytics)
    # aday oluştur (1 👍), sonra approval_score'u -2'ye düşür
    bot.record_feedback(lid, 1, ip="1.1.1.1")   # up1 down0 score1
    bot.record_feedback(lid, -1, ip="2.2.2.2")  # up1 down1 score0
    bot.record_feedback(lid, -1, ip="3.3.3.3")  # up1 down2 score-1
    bot.record_feedback(lid, -1, ip="4.4.4.4")  # up1 down3 score-2 → GİZLENİR
    # Kayıt SİLİNMEZ (rapor için korunur) ama sunulmaz (active=0) ve gizli listede.
    row = bot.analytics.all_learnings()[0]
    assert row["active"] == 0 and row["approval_score"] <= -2
    assert bot.find_learned_answer("kütüphane kaça kadar açık") is None
    hidden = bot.analytics.poisoned_hidden()
    assert len(hidden) == 1 and hidden[0]["approval_score"] <= -2


def test_ip_oyunu_degistirebilir(monkeypatch, tmp_path):
    """Aynı IP 👍→👎 değiştirirse oy güncellenir (yeni oy eklenmez)."""
    import bot
    _tmp_db(monkeypatch, tmp_path)
    lid = _log(bot.analytics)
    bot.record_feedback(lid, 1, ip="1.1.1.1")   # up1
    bot.record_feedback(lid, 1, ip="2.2.2.2")   # up2
    bot.record_feedback(lid, -1, ip="2.2.2.2")  # 2.2.2.2 fikrini değiştirdi → up1 down1
    row = bot.analytics.all_learnings()[0]
    assert row["upvotes"] == 1 and row["downvotes"] == 1


def test_olumsuz_cevap_aday_olmaz(monkeypatch, tmp_path):
    import bot
    _tmp_db(monkeypatch, tmp_path)
    lid = _log(bot.analytics, ans="Bilmiyorum.")
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        bot.record_feedback(lid, 1, ip=ip)
    assert bot.analytics.all_learnings() == []   # promote edilemez → hiç aday yok


def test_alakasiz_soru_ogrenilmis_cevabi_almaz(monkeypatch, tmp_path):
    import bot
    _tmp_db(monkeypatch, tmp_path)
    lid = bot.analytics.log_chat("yatay geçiş başvurusu ne zaman", "yatay geçiş",
                                 "web", 50, "Yatay geçiş başvuruları Ağustos'ta alınır.")
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        bot.record_feedback(lid, 1, ip=ip)   # aktif oldu
    # farklı konu → sıkı eşik nedeniyle sunulmamalı
    assert bot.find_learned_answer("yemekhanede bugün ne var") is None
    # kendi sorusu → gelir
    assert bot.find_learned_answer("yatay geçiş başvurusu ne zaman") is not None


def test_sablon_sorular_yanlis_eslesmez(monkeypatch, tmp_path):
    """'redis ne işe yarar' aktifken 'mongodb ne işe yarar' ONA eşleşmemeli
    (4 ortak şablon kelime yüksek Jaccard verse de konu token'ı farklı)."""
    import bot
    _tmp_db(monkeypatch, tmp_path)
    lid = bot.analytics.log_chat("redis ne işe yarar kısaca", "redis", "web",
                                 50, "Redis, bellek-içi bir veri deposudur.")
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        bot.record_feedback(lid, 1, ip=ip)   # redis aktif
    assert bot.find_learned_answer("redis ne işe yarar kısaca") is not None      # kendi
    assert bot.find_learned_answer("mongodb ne işe yarar kısaca") is None        # farklı konu


def test_run_ogrenilmis_cevabi_dogrudan_dondurur(monkeypatch, tmp_path):
    """_run öğrenilmiş güçlü eşleşmeyi pipeline'a girmeden döndürür."""
    import bot
    _tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(bot, "find_learned_answer",
                        lambda q: "ÖĞRENİLMİŞ CEVAP" if "kütüphane" in q else None)
    monkeypatch.setattr(bot, "classify_intent",
                        lambda q: (_ for _ in ()).throw(AssertionError("pipeline'a girilmemeli")))
    out = bot._run("kütüphane nerede")
    assert out["answer"] == "ÖĞRENİLMİŞ CEVAP"
    assert out["intent"] == "learned"
