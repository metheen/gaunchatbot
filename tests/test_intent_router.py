"""intent_router saf fonksiyonlarının birim testleri (DB/ağ gerektirmez)."""

from intent_router import classify_intent, extract_content_tokens, match_department


# --- classify_intent -------------------------------------------------------

def test_classify_yapisal_numara():
    # görevdeki kritik sorgu SQL yoluna gitmeli
    assert classify_intent("Bilgi İşlem numarası kaç?") == "structural"


def test_classify_yapisal_iletisim_kelimeleri():
    assert classify_intent("Veli Örnek'in dahili telefonu?") == "structural"
    assert classify_intent("Rektörlüğün e-postası nedir?") == "structural"


def test_classify_yapisal_kisi_arama():
    assert classify_intent("Canan Deneme kimdir?") == "structural"
    assert classify_intent("canan denemenin numarası ne") == "structural"


def test_classify_rektor_rol_sorusu_semantige_gider():
    # GERÇEK REGRESYON (2026-07-22): 'rektor', 'REKTÖRLÜK' departman adının alt
    # dizesi olduğu için match_department yanlışlıkla departman personel
    # listesini döndürüyordu. 'kim' PERSON_KEYWORDS'te olsa da bu artık semantic'e
    # gitmeli (küratörlü SSS cevabı), departman rosterine değil.
    assert classify_intent("rektör kimdir") == "semantic"
    assert classify_intent("Gaziantep Üniversitesi Rektörü kim?") == "semantic"
    # Birim-iletişim sorusu (CONTACT_KEYWORDS) istisnaya girmemeli — hâlâ yapısal.
    assert classify_intent("Rektörlüğün e-postası nedir?") == "structural"


def test_classify_yemekhane_yanlislikla_canli_rotaya_dusmez():
    # GERÇEK REGRESYON (2026-07-22, 102 soruluk stress test): çıplak "yemek"
    # anahtar kelimesi "yemekhane" içinde alt-dize olarak eşleşip yemekhaneyle
    # ilgili HER soruyu (turnike, misafir, kedi vb.) canlı-veri rotasına
    # düşürüyordu. Bu sorular artık semantic (SSS'deki gerçek cevaba) gitmeli.
    assert classify_intent("Kedileri gizlice kampüs yemekhanesine sokmak yasak mı?") == "semantic"
    assert classify_intent("Misafirimi kampüs yemekhanesine sokabilir miyim?") == "semantic"
    # Gerçek canlı-menü niyeti hâlâ doğru yakalanmalı (bugun/yarin/menu üzerinden).
    assert classify_intent("Bugün yemekte ne var?") == "live"
    assert classify_intent("Yarın yemekhanede ne çıkacak?") == "live"


def test_classify_semantik():
    assert classify_intent("Yatay geçiş başvuru şartları nelerdir?") == "semantic"
    assert classify_intent("Kayıt dondurma nasıl yapılır?") == "semantic"


def test_classify_general_knowledge_fast_track():
    # Üniversiteyle İLGİSİZ genel/teknik sorular → general_knowledge (RAG atlanır).
    # NOT: yön/mekan (nereye/nerede/nasıl) İÇERMEYEN genel sorular seçildi —
    # kısa+yön soruları artık bilerek semantic'e (kampüs) zorlanıyor.
    assert classify_intent("Docker ne işe yarar?") == "general_knowledge"
    assert classify_intent("python programlama dili nedir") == "general_knowledge"
    assert classify_intent("bitcoin nedir") == "general_knowledge"
    assert classify_intent("dünyanın en yüksek dağı hangisidir") == "general_knowledge"
    # "einstein kimdir" → structural (kimdir); genel kişi cevabı çıplak-isim→web yolundan.


def test_implicit_kampus_sorulari_rag_e_gider():
    # 2026-07-28 canlı bug: "arabamı nereye park edebilirim" web'e kaçıp ABD park
    # bilgisi getiriyordu. Artık kampüs RAG'ına (semantic) gitmeli.
    assert classify_intent("arabamı nereye park edebilirim") == "semantic"
    assert classify_intent("otobüs nereden kalkıyor") == "semantic"
    assert classify_intent("yemekhane kartımı nereden alırım") == "semantic"
    # Anahtar kelime İÇERMEYEN kısa yön sorusu da (kişisel yakalayıcı) → semantic.
    assert classify_intent("en yakın tuvalet nerede") == "semantic"


def test_needs_gaun_scope_izolasyon_karari():
    from intent_router import needs_gaun_scope
    # Üniversite/kampüs/akademik → arama GAÜN'e izole (True)
    assert needs_gaun_scope("yaz okulu ücreti ne kadar") is True
    assert needs_gaun_scope("bilgi işlem daire başkanı kim") is True
    assert needs_gaun_scope("kütüphane çalışma saatleri") is True
    # Günlük hayat / genel kültür → saf internet (False)
    # (yön/mekan kelimesi İÇERMEYEN net-genel örnekler — 'nasıl/nerede' kısa
    # sorularda kampüs lehine semantic'e zorlanır)
    assert needs_gaun_scope("kripto para nedir") is False
    assert needs_gaun_scope("dünyanın en yüksek dağı hangisidir") is False


def test_gaun_sorulari_web_e_KACMAZ():
    # REGRESYON: kampüs/mevzuat/öğrenci-işleri soruları general_knowledge OLMAMALI
    # (yanlışlıkla web'e gitmesin — önce RAG/SQL). Hepsi semantic/structural kalmalı.
    for q in ("Yatay geçiş şartları nelerdir?", "Kayıt dondurma nasıl yapılır?",
              "Bütünleme sınavına kimler girebilir?", "Harç ücreti ne kadar?",
              "Yaz okulu ne zaman başlıyor?", "Erasmus başvurusu nasıl yapılır?",
              "Kütüphane çalışma saatleri", "Mühendislik Fakültesi hakkında bilgi",
              "Öğrenci belgesi nasıl alınır?", "Staj zorunlu mu?",
              "arabamı nereye park edebilirim", "vize notum kaç oldu",
              "büt sınavı ne zaman", "ring saatleri nedir"):
        assert classify_intent(q) != "general_knowledge", q


def test_classify_canli_veri():
    assert classify_intent("Bugün yemekte ne var?") == "live"
    assert classify_intent("Güncel duyurular neler?") == "live"
    assert classify_intent("Yarın menüde ne var?") == "live"


def test_live_topic():
    from intent_router import live_topic
    assert live_topic("Bugün yemekte ne var?") == "yemek"
    assert live_topic("menü nedir") == "yemek"
    assert live_topic("son duyurular") == "duyuru"
    assert live_topic("bugün güncel haberler") == "duyuru"  # varsayılan


def test_classify_sohbet():
    assert classify_intent("Selam, nasılsın?") == "chitchat"
    assert classify_intent("Merhaba") == "chitchat"
    assert classify_intent("teşekkürler") == "chitchat"
    assert classify_intent("Çok teşekkürler!") == "chitchat"  # dolgu kelimesi yutmamalı
    assert classify_intent("kimsin") == "chitchat"
    assert classify_intent("günaydın") == "chitchat"


def test_sohbet_gercek_soruyu_yutmaz():
    # selamla BAŞLAYAN ama gerçek içerik taşıyan sorular sohbet DEĞİL
    assert classify_intent("merhaba yatay geçiş şartları nedir") == "semantic"
    assert classify_intent("selam Bilgi İşlem numarası kaç") == "structural"
    # 'nasıl yapılır' sohbet değil (nasilsin değil)
    assert classify_intent("Kayıt dondurma nasıl yapılır?") == "semantic"


# --- extract_content_tokens ------------------------------------------------

def test_tokens_birim_sorgusu():
    # 'numarasi' ve 'kac' stopword; geriye birim adayı kalır
    assert extract_content_tokens("Bilgi İşlem numarası kaç?") == ["bilgi", "islem"]


def test_tokens_kisi_sorgusu():
    assert extract_content_tokens("Veli Örnek dahili numarası") == ["veli", "ornek"]


def test_tokens_iyelik_ekini_kirpar():
    assert extract_content_tokens("canan denemenin numarası ne") == ["canan", "deneme"]
    assert extract_content_tokens("ahmetin telefonu kaç") == ["ahmet"]


def test_tokens_turkce_katlama():
    # Türkçe karakterler ascii'ye katlanır (search_name ile eşleşsin)
    assert extract_content_tokens("Çelik Yıldız kimdir") == ["celik", "yildiz"]


# --- match_department ------------------------------------------------------

DEPTS = [
    {"id": 1, "name": "BİLGİ İŞLEM DAİRE BŞK."},
    {"id": 2, "name": "BİLGİSAYAR MÜHENDİSLİĞİ"},
    {"id": 3, "name": "ÖĞRENCİ İŞLERİ DAİRE BŞK."},
    {"id": 4, "name": "TIP FAKÜLTESİ"},
]


def test_match_department_bilgi_islem():
    # 'bilgi islem' yalnız BİDB'yi eşlemeli (bilgisayar'ı DEĞİL)
    dept = match_department("Bilgi İşlem numarası kaç?", DEPTS)
    assert dept is not None and dept["id"] == 1


def test_match_department_eslesme_yok():
    # var olmayan birim -> None (halüsinasyon tuzağı deterministik reddedilir)
    assert match_department("Uzay Bilimleri Fakültesi dekanı kim?", DEPTS) is None


def test_match_department_en_spesifik():
    # 'ogrenci isleri' tek eşleşme
    dept = match_department("Öğrenci İşleri nerede?", DEPTS)
    assert dept is not None and dept["id"] == 3


def test_match_department_bos_token_none():
    assert match_department("numarası kaç?", DEPTS) is None


# --- match_department: tür süzgeci + ana kampüs varsayılanı -----------------
# 2026-07-27 canlı bug: "eğitim fakültesindeki akademisyenler" tek kişilik
# EĞİTİM KOORDİNATÖRLÜĞÜNE düşüyordu. Gerçek veri yapısını yansıtan birim seti
# (aynı 'eğitim' kökünü paylaşan farklı TÜR ve KAMPÜS varyantları):
DEPTS_EGITIM = [
    {"id": 204, "name": "AFRİN EĞİTİM FAKÜLTESİ"},
    {"id": 89, "name": "EĞİTİM BİLİMLERİ ENSTİTÜSÜ"},
    {"id": 191, "name": "EĞİTİM KOORDİNATÖRLÜĞÜ"},
    {"id": 51, "name": "GAZİANTEP EĞİTİM FAKÜLTESİ"},
    {"id": 5, "name": "NİZİP EĞİTİM FAKÜLTESİ"},
    {"id": 4, "name": "TIP FAKÜLTESİ"},
]


def test_egitim_fakultesi_koordinatorluge_dusmez():
    # Öğrenci: "eğitim fakültesindeki akademisyenleri getirir misin"
    # Tür süzgeci 'fakülte' der → tek kişilik KOORDİNATÖRLÜK elenir; kampüs
    # belirtilmediği için ana kampüs GAZİANTEP EĞİTİM FAKÜLTESİ döner.
    dept = match_department(
        "iyi bana eğitim fakültesindeki akademisyenleri getirir misin",
        DEPTS_EGITIM)
    assert dept is not None and dept["id"] == 51


def test_egitim_fakultesi_kisa_bicim_ana_kampus():
    # Öğrenci: "eğitim fakültesi hocaları" → yine merkez Gaziantep.
    dept = match_department("eğitim fakültesi hocaları kimler", DEPTS_EGITIM)
    assert dept is not None and dept["id"] == 51


def test_nizip_acikca_anilinca_nizip_gelir():
    # Öğrenci: "nizip eğitim fakültesindeki akademisyenler" → Nizip (varsayılan
    # DEĞİL; kampüs açıkça anıldı).
    dept = match_department(
        "nizip eğitim fakültesindeki akademisyenler", DEPTS_EGITIM)
    assert dept is not None and dept["id"] == 5


def test_koordinatorluk_sorusu_koordinatorluge_gider():
    # Öğrenci: "eğitim koordinatörlüğü personeli" → tür 'koordinatörlük' →
    # fakülteye DEĞİL koordinatörlüğe gider (tür süzgeci iki yönlü dürüst).
    dept = match_department("eğitim koordinatörlüğü personeli", DEPTS_EGITIM)
    assert dept is not None and dept["id"] == 191


def test_enstitu_fakulteden_ayrilir():
    # Öğrenci: "eğitim bilimleri enstitüsü" → enstitü; fakülteyle karışmaz.
    dept = match_department("eğitim bilimleri enstitüsü akademik kadro",
                            DEPTS_EGITIM)
    assert dept is not None and dept["id"] == 89


def test_olmayan_fakulte_hala_none():
    # Var olmayan fakülte türünde bir istek yine dürüstçe None (uydurmaz).
    assert match_department("ziraat fakültesi akademisyenleri", DEPTS_EGITIM) is None
