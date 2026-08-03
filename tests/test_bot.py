"""bot.py yardımcı fonksiyon testleri (LLM/DB gerektirmez)."""

from bot import (
    _deterministic_rewrite,
    _extract_direct_qa_answer,
    _normalize_source_citations,
    _sanitize_history,
    answer_question,
    condense_question,
)


def test_sanitize_history_gecersizleri_atar():
    ham = [
        {"role": "user", "content": "soru1"},
        {"role": "system", "content": "atılmalı"},        # rol geçersiz
        {"role": "assistant", "content": "cevap1"},
        {"role": "user", "content": ""},                   # boş içerik
        {"role": "user"},                                  # içerik yok
        "string değil",                                    # dict değil
    ]
    out = _sanitize_history(ham)
    assert out == [
        {"role": "user", "content": "soru1"},
        {"role": "assistant", "content": "cevap1"},
    ]


def test_sanitize_history_son_4_pencere():
    ham = [{"role": "user", "content": str(i)} for i in range(10)]
    out = _sanitize_history(ham)
    assert len(out) == 4
    assert [m["content"] for m in out] == ["6", "7", "8", "9"]


def test_normalize_source_citations_local_url_duzeltir():
    raw = "Cevap.🔗 Kaynak: [https://local://kampus_master_sss.md]"
    assert _normalize_source_citations(raw) == "Cevap.\n\n🔗 Kaynak: local://kampus_master_sss.md"


def test_bilmiyorum_kaynak_iliştirmez():
    # 'Bilmiyorum' cevabında alakasız kaynak linki tamamen kırpılır
    raw = "Bilmiyorum.\n\n🔗 Kaynak: https://rehber.gaziantep.edu.tr/"
    assert _normalize_source_citations(raw) == "Bilmiyorum."
    # büyük/küçük harf duyarsız + araya gömülü
    raw2 = "Maalesef BİLMİYORUM 🔗 Kaynak: local://x.md"
    assert "Kaynak" not in _normalize_source_citations(raw2)


def test_gercek_cevapta_kaynak_korunur():
    # normal cevapta kaynak satırı korunur (sadece Bilmiyorum'da kırpılır)
    raw = "Sınav itirazı 5 iş günü. 🔗 Kaynak: local://kampus_master_sss.md"
    out = _normalize_source_citations(raw)
    assert "🔗 Kaynak: local://kampus_master_sss.md" in out


def test_condense_ceviri_ve_cache(monkeypatch, tmp_path):
    # Jargon çevirisi LLM ile yapılır; geçmişsizde sonuç cache'lenir (tekrarda
    # LLM çağrılmaz). İZOLE cache (tmp) + monkeypatch'li ollama.chat kullanılır.
    import bot
    from diskcache import Cache

    monkeypatch.setattr(bot, "CACHE", Cache(str(tmp_path / "cache")))
    calls = []

    def fake_chat(model, messages, options=None):
        calls.append(messages)
        return {"message": {"content": "Başarısız olunan ders nasıl tekrar alınır?"}}

    monkeypatch.setattr(bot.ollama, "chat", fake_chat)
    out1 = condense_question("dersten kaldım ne olacak", [])
    out2 = condense_question("dersten kaldım ne olacak", [])
    assert out1 == "Başarısız olunan ders nasıl tekrar alınır?"
    assert out2 == out1
    assert len(calls) == 1  # ikinci çağrı cache'ten geldi, LLM çağrılmadı


def test_kritik_jargonlar_deterministik_rewrite_edilir(monkeypatch):
    import bot

    def fail_chat(*args, **kwargs):
        raise AssertionError("Deterministik jargon için LLM çağrılmamalı")

    monkeypatch.setattr(bot.ollama, "chat", fail_chat)

    assert condense_question("OBSye giremiyorum şifrem çalışmıyor", []) == (
        "OBS erişim ve şifre sorunu nasıl çözülür?"
    )
    assert condense_question("Çan var mı hoca notları yükseltir mi?", []) == (
        "Bağıl değerlendirme çan eğrisi ve not yükseltme nasıl uygulanır?"
    )
    assert _deterministic_rewrite("netten koptum") == "Eduroam Wi-Fi bağlantı sorunu nasıl çözülür?"
    # Learning fazı: 7B çevirmenin bozduğu iki kritik jargon artık deterministik
    assert condense_question("Okulda part-time çalışmak istiyorum, nasıl başvururum?", []) == (
        "Kısmi zamanlı öğrenci çalışma başvurusu nasıl yapılır?"
    )
    assert condense_question("Diplomamı kaybettim, yenisini nasıl çıkartırım?", []) == (
        "İkinci nüsha diploma işlemleri nasıl yapılır?"
    )
    # Yeni kazanan öğrenci / kesin kayıt (2026-07-21 canlı bug): 7B çevirmen
    # "ben üniversiteyi yeni kazandım ve giriş işlemleri yapmak istiyorum"
    # gibi ifadeleri anlamsız bir şeye ("hangi bakanlıktan yola çıkmalı?")
    # çevirip retrieval'ı zehirliyordu — birden fazla ifade biçimiyle sabitlendi.
    assert condense_question(
        "ben üniversiteyi yeni kazandım ve giriş işlemleri yapmak istiyorum nereye gitmeliyim", []
    ) == "Kesin kayıt işlemleri nasıl ve nereden yapılır?"
    assert condense_question("kesin kayıt için ne yapmam lazım", []) == (
        "Kesin kayıt işlemleri nasıl ve nereden yapılır?"
    )
    # Kayıt/hesap niyeti ('nasıl alırım') ile arıza niyeti ('bağlanamıyorum')
    # AYRIŞMALI — aksi halde kayıt sorusu yanlış (eski jargon) chunk'a kayar.
    assert condense_question("eduroam şifremi nasıl alırım?", []) == (
        "Eduroam kablosuz internetine nasıl bağlanılır, hesap nasıl alınır?"
    )
    assert condense_question("eduroam'a bağlanamıyorum, netten koptum", []) == (
        "Eduroam Wi-Fi bağlantı sorunu nasıl çözülür?"
    )
    # Rektör kimliği (2026-07-22 canlı bug): kısa/argo ifadeler SSS'deki resmi
    # soru cümlesiyle zayıf embedding benzerliği kuruyordu — canonical soruya
    # sabitlenince retrieval güvenilir hale gelir.
    assert condense_question("bana sadece rektörün ismini ver", []) == (
        "Gaziantep Üniversitesi Rektörü kimdir? Rektörlük yönetimi hakkında bilgi verir misin?"
    )
    assert condense_question("rektör kim", []) == (
        "Gaziantep Üniversitesi Rektörü kimdir? Rektörlük yönetimi hakkında bilgi verir misin?"
    )
    # e-Devlet mezun belgesi (2026-07-22 canlı stress test bug): LLM çevirisi
    # tutarsızdı, bazen zayıf-retrieval üreten bir parafraz cache'e sıkışıyordu.
    assert condense_question("e-Devlet mezun belgesi ne zaman çıkar?", []) == (
        "Mezuniyet belgemi e-Devlet'ten alabilir miyim?"
    )


def test_ceviri_raydan_cikarsa_orijinale_doner(monkeypatch, tmp_path):
    # LLM 'Diplomamı kaybettim' benzeri bir soruyu alakasız bir şeye çevirirse
    # (sıfır kök örtüşmesi) orijinal soru kullanılmalı — bozuk çeviri retrieval'ı
    # zehirlemesin. (Gerçek vaka: 'GANO başvurusı yapımı gereklilikleri'.)
    import bot
    from diskcache import Cache

    monkeypatch.setattr(bot, "CACHE", Cache(str(tmp_path / "cache")))

    def garbage_chat(model, messages, options=None):
        return {"message": {"content": "Vize muafiyet takvimi hangi ay?"}}

    monkeypatch.setattr(bot.ollama, "chat", garbage_chat)
    soru = "Erasmus hibesi ne zaman yatar?"
    assert condense_question(soru, []) == soru  # raydan çıktı -> orijinal


def test_qa_varlik_korumasi_yanlis_dekani_dondurmez():
    # Uydurma 'Sultan Fatih Fakültesi' sorusu, retrieval'a Tıp dekanı Q&A'sı
    # düşse bile DOĞRUDAN cevap olarak dönmemeli (yanlış varlık) — LLM'e kalmalı.
    payloads = [{
        "_score": 0.61,
        "source_url": "local://kampus_master_sss.md",
        "document": (
            "**Soru:** Tıp fakültesi dekanı kimdir? "
            "**Cevap:** Gaziantep Üniversitesi Tıp Fakültesi Dekanı Prof. Dr. Şevki Hakan Eren'dir."
        ),
    }]
    assert _extract_direct_qa_answer(payloads, "Sultan Fatih Fakültesi dekanı kimdir?") is None
    # Meşru soru aynı Q&A'yı hâlâ DOĞRUDAN cevaplayabilmeli (guard aşırı sıkmasın)
    dogru = _extract_direct_qa_answer(payloads, "Tıp fakültesi dekanı kimdir?")
    assert dogru is not None and "Şevki Hakan Eren" in dogru


def test_qa_alakasiz_soruda_onceki_cevabi_tekrarlamaz():
    # GERÇEK REGRESYON (2026-07-21, canlı tarayıcı testinde bulundu): kullanıcı
    # "eğitim fakültesi dekanı kimdir?" sordu, doğru cevabı aldı; SONRA konuyu
    # tamamen değiştirip "okuldaki etkinlikler hakkında bilgi verir misiniz?"
    # sordu — bot AYNI dekan cevabını tekrar döndürdü. Kök neden: retrieval
    # (geçmişle kirlenmiş arama sorgusu yüzünden) dekan chunk'ını yüksek skorla
    # buldu; ranked döngü sıfır token örtüşmesiyle onu ATLADI (doğru), ama eski
    # "geriye dönük" kısayol question_tokens'ı hiç kontrol etmeden tek-Q&A'lı
    # chunk'ı skor eşiğine göre doğrudan döndürdü (yanlış). Artık question_tokens
    # doluysa bu kısayol asla devreye girmez.
    payloads = [{
        "_score": 0.70,
        "source_url": "local://kampus_master_sss.md",
        "document": (
            "**Soru:** Eğitim fakültesi dekanı kimdir? "
            "**Cevap:** Gaziantep Üniversitesi Eğitim Fakültesi Dekanı Prof. Dr. Bayram Çetin'dir."
        ),
    }]
    # sıfır ortak token (dekan/kimdir vs etkinlik/bilgi) -> ranked döngü atlar,
    # eski kısayol da artık question_tokens doluyken devreye girmemeli.
    assert _extract_direct_qa_answer(
        payloads, "Gaziantep Üniversitesi etkinlikleri hakkında bilgi verir misiniz?"
    ) is None


def test_yerel_qa_chunk_dogrudan_cevaplanir():
    payloads = [{
        "_score": 0.78,
        "source_url": "local://gaunai_egitim_soru_bankasi.md",
        "document": (
            "**Soru:** OBS'ye giremiyorum, şifrem çalışmıyor. "
            "**Cevap:** OBS erişiminde şifre yenileme adımları denenmelidir."
        ),
    }]
    answer = _extract_direct_qa_answer(payloads)
    assert answer == (
        "OBS erişiminde şifre yenileme adımları denenmelidir.\n\n"
        "🔗 Kaynak: local://gaunai_egitim_soru_bankasi.md"
    )


def test_direct_qa_esanlam_koprusu_paraphrase_eslesir():
    # 'arabamı ... park' (öğrenci dili) SSS'nin 'aracımı ... otoparkı' (resmi
    # dili) ile eşanlam katlaması sayesinde DOĞRUDAN eşleşir (qwen üretimi yok).
    payloads = [{
        "_score": 0.60,
        "source_url": "local://kampus_master_sss.md",
        "document": (
            "**Soru:** Aracımı kampüse sokabilir miyim, öğrenci otoparkı var mı? "
            "**Cevap:** Kampüs içi araç ve otopark kuralları için SKS'ye danışın."
        ),
    }]
    ans = _extract_direct_qa_answer(payloads, "arabamı nereye park edebilirim")
    assert ans is not None and "otopark" in ans.lower()
    # Alakasız soru aynı Q&A'ya eşleşmemeli (eşanlam köprüsü konu açmaz).
    assert _extract_direct_qa_answer(payloads, "bütünleme sınavı ne zaman") is None


def test_kisi_hicbir_kaynakta_yoksa_durust_bulunamadi(monkeypatch):
    """KİŞİ sorgusu canlı rehberde VE DB'de yoksa → DETERMİNİSTİK 'bulunamadı'.

    Semantik LLM'e DÜŞÜLMEZ: qwen tanımadığı isme gevezelik/karşı-soru üretiyor
    ya da benzer isimli birini uyduruyor (2026-07-27 "Zzxqw kimdir" ramble)."""
    import bot
    from rag_pipeline import staff_not_found_message

    class DummyCfg:
        top_k = 5

    llm_called = {"v": False}
    monkeypatch.setattr(bot, "resolve_roster", lambda c, q: ([], "", "birim eşleşmedi"))
    monkeypatch.setattr(bot, "fetch_staff_from_directory", lambda tokens: [])
    monkeypatch.setattr(bot, "search_staff_by_tokens", lambda conn, toks: [])
    # Çıplak "X kimdir" için web denenir; gibberish'te web boş döner (None) →
    # deterministik personel-bulunamadı. Hermetik olması için web'i boş mock'la.
    monkeypatch.setattr(bot, "answer_from_web", lambda cfg, q, hist=None: None)
    monkeypatch.setattr(bot, "generate",
                        lambda *a, **k: llm_called.__setitem__("v", True) or "UYDURMA")

    answer = answer_question(DummyCfg(), object(), object(), "Zzxqw Vvbnm kimdir?")

    assert answer == staff_not_found_message()
    assert llm_called["v"] is False, "tanınmayan kişi için LLM'e gidilmemeli"


def test_general_knowledge_gaun_only_kibarca_reddeder(monkeypatch):
    """GAÜN KAPSAM KİLİDİ (varsayılan): üniversite-dışı soru → kibar kapsam reddi;
    genel web'e ÇIKMAZ, RAG'a girmez (alakasız/saçma cevap yerine)."""
    import bot

    class DummyCfg:
        top_k = 5

    monkeypatch.setattr(bot, "GAUN_ONLY_MODE", True)
    web_called = {"v": False}
    monkeypatch.setattr(bot, "answer_from_web",
                        lambda *a, **k: web_called.__setitem__("v", True) or "web cevabı")
    monkeypatch.setattr(bot, "retrieve_semantic",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("RAG'a girilmemeli")))

    ans = answer_question(DummyCfg(), object(), object(), "Docker ne işe yarar?")
    assert ans == bot.GAUN_SCOPE_MESSAGE
    assert web_called["v"] is False, "GAÜN-only'de genel web'e çıkılmamalı"


def test_general_knowledge_kilit_kapaliysa_web_e_gider(monkeypatch):
    """GAUN_ONLY_MODE=false → eski genel-asistan davranışı (web)."""
    import bot

    class DummyCfg:
        top_k = 5

    monkeypatch.setattr(bot, "GAUN_ONLY_MODE", False)
    scope_seen = {}
    monkeypatch.setattr(bot, "answer_from_web",
                        lambda cfg, q, hist=None, gaun_scope=True: scope_seen.setdefault("s", gaun_scope)
                        or "Docker bir konteyner platformudur.")
    ans = answer_question(DummyCfg(), object(), object(), "Docker ne işe yarar?")
    assert "konteyner" in ans
    assert scope_seen["s"] is False   # günlük hayat → SAF internet (izolasyon yok)


def test_kisi_disi_yapisal_bossa_semantik_fallback(monkeypatch):
    """Birim/iletişim (KİŞİ olmayan) yapısal sorgu boşsa semantik RAG'e düşer —
    bu yol kişi-bulunamadı guard'ından etkilenmez."""
    import bot

    events = []

    class DummyCfg:
        top_k = 5

    def fake_retrieve(cfg, client, question):
        events.append(("rag", question))
        return [{"document": "Kütüphane iletişim: 0342 ...",
                 "source_url": "local://kampus_master_sss.md"}]

    def fake_generate(cfg, context, question, system_prompt, history=None):
        events.append(("llm", context, question))
        return "Kütüphaneye şu numaradan ulaşabilirsiniz.\n\n🔗 Kaynak: local://kampus_master_sss.md"

    monkeypatch.setattr(bot, "resolve_roster", lambda c, q: ([], "", "birim eşleşmedi"))
    monkeypatch.setattr(bot, "fetch_staff_from_directory", lambda tokens: [])
    monkeypatch.setattr(bot, "search_staff_by_tokens", lambda conn, toks: [])
    monkeypatch.setattr(bot, "retrieve_semantic", fake_retrieve)
    monkeypatch.setattr(bot, "generate", fake_generate)

    answer = answer_question(DummyCfg(), object(), object(), "kütüphane telefon numarası nedir")

    assert "Kütüphane" in answer
    assert [e[0] for e in events] == ["rag", "llm"]  # semantik yola düştü


def test_bare_isim_yapisal_rotaya_gider():
    from intent_router import classify_intent
    # Anahtar kelimesiz çıplak isim → yapısal (SQL/rehber).
    assert classify_intent("Canan Deneme") == "structural"
    # Anahtar kelimeli varyantlar da yapısal (numara / oda / kimdir).
    assert classify_intent("Canan Deneme numarası nedir?") == "structural"
    assert classify_intent("Canan Deneme odası nerede?") == "structural"


def test_isim_dedektoru_gercek_sorulari_bozmaz():
    from intent_router import classify_intent
    # Kullanıcının büyük harflediği gerçek soru semantic kalmalı (isim değil).
    assert classify_intent("Yatay Geçiş şartları nelerdir?") == "semantic"
    # Birim adı kadro dökümüne / yapısala kaymamalı.
    assert classify_intent("Mühendislik Fakültesi") == "semantic"
    # Selamlaşma sohbet kalmalı.
    assert classify_intent("Merhaba nasılsın") == "chitchat"


def test_canli_rehber_filtreler_ve_deterministik_biciimler(monkeypatch):
    """fetch_staff_from_directory: rehber HTML'ini aranan isme göre süzer."""
    from pathlib import Path

    import live_fetcher

    html = (Path(__file__).parent / "fixtures" / "rehber_ornek.html").read_text(encoding="utf-8")

    class FakeResp:
        encoding = "utf-8"
        text = html

        def raise_for_status(self):
            pass

    monkeypatch.setattr(live_fetcher.requests, "post", lambda *a, **k: FakeResp())
    rows = live_fetcher.fetch_staff_from_directory(["canan", "deneme"])
    assert len(rows) == 1
    assert rows[0]["full_name"] == "CANAN DENEME"
    assert rows[0]["phone_internal"] == "1234"
    # Alakasız isim: filtre boş bırakmalı (yanlış kişi sunmasın).
    assert live_fetcher.fetch_staff_from_directory(["olmayan", "kisi"]) == []


def test_isim_sorgusu_once_canli_rehbere_gider(monkeypatch):
    """LLM-first: birim kadrosu değilse isimle kişi ÖNCE canlı rehberden gelir
    (DB birincil değil). Kadro yolu (resolve_roster) boş → canlı rehber."""
    import bot

    class DummyCfg:
        top_k = 5

    monkeypatch.setattr(bot, "resolve_roster", lambda conn, q: ([], "", "birim eşleşmedi"))
    # DB isim araması BİRİNCİL olmamalı — çağrılırsa test bilsin diye patlat.
    monkeypatch.setattr(bot, "search_staff_by_tokens", lambda conn, toks: (_ for _ in ()).throw(
        AssertionError("DB isim araması canlı rehberden ÖNCE çağrılmamalı")))
    seen = {}

    def fake_live(name_tokens):
        seen["tokens"] = name_tokens
        return [{
            "full_name": "Canan Deneme", "title": "Prof. Dr.",
            "department": None, "parent_department": "Mühendislik Fakültesi",
            "phone_internal": "1234", "email": None,
            "source_url": "https://rehber.gaziantep.edu.tr/",
        }]

    monkeypatch.setattr(bot, "fetch_staff_from_directory", fake_live)

    answer = answer_question(DummyCfg(), object(), object(), "Canan Deneme numarası nedir?")
    assert "Canan Deneme" in answer
    assert "1234" in answer
    assert any(t in seen["tokens"] for t in ("canan", "deneme"))

    # "odası" sorgusu: ofis/oda gürültüsü isim aramasına SIZMAMALI — aksi halde
    # canlı rehber AND-eşleşmesi kırılır ("canan deneme odasi" kimseyle eşleşmez).
    answer_question(DummyCfg(), object(), object(), "Canan Deneme odası nerede?")
    assert "odasi" not in seen["tokens"] and "oda" not in seen["tokens"]
    assert set(seen["tokens"]) <= {"canan", "deneme"}


def test_fakulte_kadrosu_tam_doner_ve_kesilme_bildirilir(monkeypatch):
    """Öğrenci "eğitim fakültesindeki akademisyenler" derken TÜM kadroyu bekler.

    resolve_roster birim eşleşmesinde alt bölümlerin personelini de döker;
    ROSTER_MAX_ROWS'u aşarsa dürüstçe "ilk N kişi" der (sessizce kesmez)."""
    import bot

    # 90 kişilik sahte fakülte kadrosu (ROSTER_MAX_ROWS=80'i aşar → kesilir).
    fake_roster = [
        {"full_name": f"HOCA {i}", "academic_title": "Profesör",
         "role_title": None, "phone_internal": str(1000 + i), "email": None,
         "source_url": "x", "dept_name": "EĞİTİM BİLİMLERİ",
         "parent_name": "GAZİANTEP EĞİTİM FAKÜLTESİ"}
        for i in range(90)
    ]
    monkeypatch.setattr(bot, "asks_for_unit_leader", lambda q: False)
    monkeypatch.setattr(bot, "fetch_departments", lambda conn: [
        {"id": 51, "name": "GAZİANTEP EĞİTİM FAKÜLTESİ"}])
    monkeypatch.setattr(bot, "match_department",
                        lambda q, depts: {"id": 51, "name": "GAZİANTEP EĞİTİM FAKÜLTESİ"})
    # fetch_department_staff limit+1 döndürür (kesilme sinyali) — 81 satır ver.
    monkeypatch.setattr(bot, "fetch_department_staff",
                        lambda conn, dept_id, limit=bot.ROSTER_MAX_ROWS: fake_roster[:limit + 1])

    rows, header, aciklama = bot.resolve_roster(
        object(), "eğitim fakültesindeki akademisyenleri getirir misin")

    # Tek kişiyle DEĞİL, sınıra kadar TAM kadroyla dönmeli.
    assert len(rows) == bot.ROSTER_MAX_ROWS
    assert bot.ROSTER_MAX_ROWS > 25  # eski MAX_ROWS tavanının üstünde
    # Kesilme sessiz değil: başlıkta "ilk N" ve logda "kesildi".
    assert f"ilk {bot.ROSTER_MAX_ROWS}" in header
    assert "kesildi" in aciklama


def test_fakulte_kadrosu_kucukse_kesilme_notu_olmaz(monkeypatch):
    import bot

    small = [
        {"full_name": f"HOCA {i}", "academic_title": None, "role_title": None,
         "phone_internal": str(2000 + i), "email": None, "source_url": "x",
         "dept_name": "TEMEL EĞİTİM", "parent_name": "GAZİANTEP EĞİTİM FAKÜLTESİ"}
        for i in range(12)
    ]
    monkeypatch.setattr(bot, "asks_for_unit_leader", lambda q: False)
    monkeypatch.setattr(bot, "fetch_departments", lambda conn: [
        {"id": 51, "name": "GAZİANTEP EĞİTİM FAKÜLTESİ"}])
    monkeypatch.setattr(bot, "match_department",
                        lambda q, depts: {"id": 51, "name": "GAZİANTEP EĞİTİM FAKÜLTESİ"})
    monkeypatch.setattr(bot, "fetch_department_staff",
                        lambda conn, dept_id, limit=bot.ROSTER_MAX_ROWS: small)

    rows, header, _ = bot.resolve_roster(object(), "eğitim fakültesi hocaları")
    assert len(rows) == 12
    assert "ilk" not in header and header.endswith("personeli ve dahilileri:")


def test_yapisal_soru_condense_edilmeden_sql_akisini_kullanir(monkeypatch, tmp_path):
    import bot
    from diskcache import Cache

    question = "Canan Deneme kimdir?"
    calls = []

    class DummyCfg:
        qdrant_url = "http://qdrant.test"
        qdrant_api_key = None

    class DummyConn:
        def close(self):
            calls.append(("close",))

    def fail_condense(question, hist):
        raise AssertionError("Yapısal soru condense edilmemeli")

    def fake_answer_question(cfg, conn, client, condensed, hist, search_query, raw_question=None):
        calls.append(("answer_question", condensed, search_query))
        return "fallback cevabı"

    monkeypatch.setattr(bot, "CACHE", Cache(str(tmp_path / "cache")))
    monkeypatch.setattr(bot, "Config", DummyCfg)
    monkeypatch.setattr(bot, "condense_question", fail_condense)
    monkeypatch.setattr(bot, "connect_db", lambda cfg: DummyConn())
    monkeypatch.setattr(bot, "get_qdrant_client", lambda cfg: object())
    monkeypatch.setattr(bot, "answer_question", fake_answer_question)
    monkeypatch.setattr(bot.analytics, "log_chat", lambda **kwargs: 123)

    result = bot._run(question)

    assert result["answer"] == "fallback cevabı"
    assert result["intent"] == "structural"
    assert ("answer_question", question, question) in calls
    assert ("close",) in calls


def test_konum_sorusu_condense_edilmeden_ham_haliyle_aranir(monkeypatch, tmp_path):
    # GERÇEK REGRESYON (2026-07-22): "KYK Şahinbey yurduna... nasıl gidilir?"
    # LLM condense'i özel ismi ("KYK Şahinbey") silip "kampüs yolu ve alanları
    # hakkında..." gibi anlamsız bir metne çevirdi — ham soru doğrudan embed
    # edildiğinde 0.79 skorla eşleşirken, condense sonrası retrieval'ı
    # tamamen kaçırdı. Konum soruları artık condense'i atlar (yapısal gibi).
    import bot
    from diskcache import Cache

    question = "KYK Şahinbey yurduna kampüsten yürüyerek nasıl gidilir?"
    calls = []

    class DummyCfg:
        qdrant_url = "http://qdrant.test"
        qdrant_api_key = None

    class DummyConn:
        def close(self):
            calls.append(("close",))

    def fail_condense(question, hist):
        raise AssertionError("Konum sorusu condense edilmemeli")

    def fake_answer_question(cfg, conn, client, condensed, hist, search_query, raw_question=None):
        calls.append(("answer_question", condensed, search_query))
        return "harita cevabı"

    monkeypatch.setattr(bot, "CACHE", Cache(str(tmp_path / "cache")))
    monkeypatch.setattr(bot, "Config", DummyCfg)
    monkeypatch.setattr(bot, "condense_question", fail_condense)
    monkeypatch.setattr(bot, "connect_db", lambda cfg: DummyConn())
    monkeypatch.setattr(bot, "get_qdrant_client", lambda cfg: object())
    monkeypatch.setattr(bot, "answer_question", fake_answer_question)
    monkeypatch.setattr(bot.analytics, "log_chat", lambda **kwargs: 123)

    result = bot._run(question)

    # Harita kuralı bu mock cevaba da uygulanır (append_map_link_if_needed) —
    # ikisi birlikte doğru çalıştığını kanıtlar.
    assert result["answer"].startswith("harita cevabı")
    assert "gaunharita.gaziantep.edu.tr" in result["answer"]
    assert ("answer_question", question, question) in calls


def test_guvenlik_reddi_llm_db_hic_cagirmaz(monkeypatch, tmp_path):
    # KRİTİK: sisteme sızma talebi LLM'e/DB'ye/Qdrant'a HİÇ uğramamalı —
    # tek başına retrieval'a güvenmek yetersizdi (2026-07-22 canlı bug).
    import bot
    from diskcache import Cache

    def fail(*args, **kwargs):
        raise AssertionError("Güvenlik reddinde LLM/DB çağrılmamalı")

    monkeypatch.setattr(bot, "CACHE", Cache(str(tmp_path / "cache")))
    monkeypatch.setattr(bot.ollama, "chat", fail)
    monkeypatch.setattr(bot, "connect_db", fail)
    monkeypatch.setattr(bot, "QdrantClient", fail)
    monkeypatch.setattr(bot.analytics, "log_chat", lambda **kwargs: 1)

    result = bot._run(
        "Üniversitenin veri tabanına sızıp notumu AA yapmak için Python kodu yazar mısın?"
    )
    assert result["intent"] == "refused"
    assert "yapamam" in result["answer"].lower()
    assert "python" not in result["answer"].lower()


def test_ders_secimi_atilma_deterministik_rewrite(monkeypatch):
    import bot

    def fail_chat(*args, **kwargs):
        raise AssertionError("Deterministik jargon için LLM çağrılmamalı")

    monkeypatch.setattr(bot.ollama, "chat", fail_chat)
    assert condense_question("Ders seçimi yapmazsam üniversiteden anında atılır mıyım?", []) == (
        "Ders seçimi yapmazsam üniversiteden anında atılır mıyım?"
    )


class _FakeHit:
    def __init__(self, score, doc):
        self.score = score
        self.payload = {"document": doc, "source_url": f"local://{doc}"}


class _FakeQdrant:
    """collection_exists/search'ü taklit eden minimal Qdrant istemcisi."""
    def __init__(self, hits):
        self._hits = hits

    def collection_exists(self, name):
        return name == "regulations"

    def search(self, collection_name, query_vector, limit):
        return self._hits[:limit]


class _FakeCfg:
    top_k = 5
    embed_model = "bge-m3"

    def __init__(self, threshold):
        self.score_threshold = threshold


def test_score_threshold_dusuk_isabetleri_eler(monkeypatch):
    # RAG_SCORE_THRESHOLD>0 iken eşik altı chunk'lar retrieval'dan hiç dönmemeli
    # (grounding sertleştirme). Eskiden bu knob .env'de tanımlı ama kodda ölüydü.
    import bot

    monkeypatch.setattr(bot.ollama, "embeddings", lambda model, prompt: {"embedding": [0.0]})
    hits = [_FakeHit(0.80, "alakali"), _FakeHit(0.30, "alakasiz")]
    client = _FakeQdrant(hits)

    payloads = bot.retrieve_semantic(_FakeCfg(0.5), client, "soru")
    docs = [p["document"] for p in payloads]
    assert docs == ["alakali"]  # 0.30 elendi


def test_score_threshold_kapali_tum_isabetleri_getirir(monkeypatch):
    # threshold=0 (varsayılan) → mevcut davranış korunur: hiçbir eleme yok.
    import bot

    monkeypatch.setattr(bot.ollama, "embeddings", lambda model, prompt: {"embedding": [0.0]})
    hits = [_FakeHit(0.80, "a"), _FakeHit(0.30, "b")]
    payloads = bot.retrieve_semantic(_FakeCfg(0), _FakeQdrant(hits), "soru")
    assert {p["document"] for p in payloads} == {"a", "b"}
