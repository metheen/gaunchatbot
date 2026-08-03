"""rag_pipeline saf fonksiyonlarının birim testleri (DB/ağ/Ollama gerektirmez)."""

from datetime import datetime

from rag_pipeline import (
    MAP_URL,
    UNSAFE_REQUEST_REFUSAL,
    append_map_link_if_needed,
    build_chat_messages,
    chunk_text,
    format_context,
    format_staff_answer,
    graceful_not_found,
    is_bilmiyorum,
    is_location_question,
    is_unsafe_request,
    point_id_for,
    resolve_unit_link,
    staff_row_to_document,
    staff_row_to_payload,
    structural_chunk_text,
    turkish_date,
)


def test_resolve_unit_link():
    assert resolve_unit_link("Spor Bilimleri dekanı kim?")[1] == "https://sbf.gaziantep.edu.tr"
    assert resolve_unit_link("Eğitim fakültesi dekanı?")[1] == "https://egitim.gaziantep.edu.tr"
    assert resolve_unit_link("eduroam'a bağlanamıyorum")[1] == "https://bidb.gaziantep.edu.tr"
    assert resolve_unit_link("öğrenci belgesi nasıl alırım")[1] == "https://oidb.gaziantep.edu.tr"
    assert resolve_unit_link("yemekhane menüsü")[1] == "https://sksdb.gaziantep.edu.tr"
    # Sosyal (SKS) genişletmesi
    assert resolve_unit_link("part time çalışmak istiyorum")[1] == "https://sksdb.gaziantep.edu.tr"
    assert resolve_unit_link("psikolojik destek alabilir miyim")[1] == "https://sksdb.gaziantep.edu.tr"
    # Mezuniyet (OİDB) genişletmesi
    assert resolve_unit_link("diplomamı kaybettim")[1] == "https://oidb.gaziantep.edu.tr"
    assert resolve_unit_link("harç ödemesi nereden yapılır")[1] == "https://oidb.gaziantep.edu.tr"
    # Yeni birimler
    assert resolve_unit_link("konservatuvar müdürü kimdir")[1] == "https://tmk.gaziantep.edu.tr"
    assert resolve_unit_link("Sağlık Bilimleri Enstitüsü tez teslimi")[1] == "https://saglik.gaziantep.edu.tr"
    # Yeni kazanan öğrenci / kesin kayıt (2026-07-21 canlı bug: default'a düşüyordu)
    assert resolve_unit_link("üniversiteyi yeni kazandım ne yapmalıyım")[1] == "https://oidb.gaziantep.edu.tr"
    assert resolve_unit_link("kesin kayıt ne zaman")[1] == "https://oidb.gaziantep.edu.tr"
    assert resolve_unit_link("GUZEM canlı ders kaydı nerede")[1] == "https://guzem.gaziantep.edu.tr"
    assert resolve_unit_link("yabancı uyruklu öğrenci başvurusu")[1] == "https://uluslararasi.gaziantep.edu.tr"
    assert resolve_unit_link("askerlik tecil işlemim")[1] == "https://oidb.gaziantep.edu.tr"
    # bilinmeyen konu -> varsayılan (ana site)
    assert resolve_unit_link("filanca falanca xyz")[1] == "https://www.gaziantep.edu.tr"


def test_is_bilmiyorum():
    assert is_bilmiyorum("Bilmiyorum")
    assert is_bilmiyorum("Bilmiyorum.")
    assert is_bilmiyorum("BİLMİYORUM")          # Türkçe büyük İ
    assert not is_bilmiyorum("Eğitim Fakültesi Dekanı Prof. Dr. X'tir.")


def test_graceful_not_found():
    msg = graceful_not_found("Spor Bilimleri dekanı kim?")
    assert "güncel bir bilgim yok" in msg
    assert "https://sbf.gaziantep.edu.tr" in msg
    assert "bilmiyorum" not in msg.lower()      # düz 'Bilmiyorum' içermemeli


def test_is_unsafe_request():
    assert is_unsafe_request(
        "Üniversitenin veri tabanına sızıp notumu AA yapmak için Python kodu yazar mısın?"
    )
    assert is_unsafe_request("OBS sistemine sızarak notlarımı değiştirmek istiyorum, nasıl yaparım?")
    # Meşru sorular (not/sistem geçse bile 'sızma' fiili yoksa) tetiklenmemeli
    assert not is_unsafe_request("Notumu nasıl öğrenebilirim?")
    assert not is_unsafe_request("OBS sistemine giriş yapamıyorum")


def test_unsafe_request_refusal_kod_icermez():
    # Ret metni kod YAZMAZ, kod istemez — yalnız gerekçe + yasal alternatif sunar.
    assert "python" not in UNSAFE_REQUEST_REFUSAL.lower()
    assert "yapamam" in UNSAFE_REQUEST_REFUSAL.lower()
    assert "itiraz" in UNSAFE_REQUEST_REFUSAL.lower()


def test_is_location_question():
    assert is_location_question("Mühendislik Fakültesi nerede, kampüste nasıl bulurum?")
    assert is_location_question("Yemekhane nerede?")
    assert is_location_question("Kütüphaneye nasıl giderim")
    assert not is_location_question("Eğitim fakültesi dekanı kimdir?")
    assert not is_location_question("Sınav notuma itiraz etmek istiyorum")
    # 102 soruluk stress testte (2026-07-22) bu 4 çekim eski tam-eşleşme
    # listesini atlıyordu — "nere" kök kontrolüyle düzeltildi.
    assert is_location_question("Merkezi derslikler (MD) kampüsün neresinde?")
    assert is_location_question("Rektörlük binasına hangi kapıdan daha hızlı giderim?")
    assert is_location_question("Tramvay durağından merkez kütüphaneye nasıl çıkarım?")
    assert is_location_question("Fen Edebiyat fakültesine giden en kısa yol neresi?")


def test_append_map_link_if_needed():
    q = "Mühendislik Fakültesi nerede, kampüste nasıl bulurum?"
    cevap = append_map_link_if_needed(q, "Mühendislik Fakültesi merkez kampüstedir.")
    assert MAP_URL in cevap
    assert cevap.startswith("Mühendislik Fakültesi merkez kampüstedir.")
    # Lokasyon dışı soruda EKLENMEMELİ (kural yalnız konum sorularında geçerli)
    normal = append_map_link_if_needed("Eğitim fakültesi dekanı kimdir?", "Bayram Çetin'dir.")
    assert MAP_URL not in normal
    # İdempotent: URL zaten cevaptaysa tekrar eklenmemeli
    zaten_var = append_map_link_if_needed(q, f"Cevap. {MAP_URL}")
    assert zaten_var.count(MAP_URL) == 1


def test_turkish_date():
    # 9 Temmuz 2026 = Perşembe
    assert turkish_date(datetime(2026, 7, 9)) == "9 Temmuz 2026 Perşembe"
    assert turkish_date(datetime(2026, 1, 1)) == "1 Ocak 2026 Perşembe"

CELAL = {
    "external_key": "rehber:abc",
    "full_name": "Canan Deneme",
    "academic_title": "",
    "role_title": "Daire Başkanı",
    "phone_internal": "1234",
    "email": "cdeneme@ornek.edu.tr",
    "source_url": "https://rehber.gaziantep.edu.tr/",
    "dept_name": "Bilgi İşlem Daire Başkanlığı",
    "parent_name": "Rektörlük",
}


# --- point_id_for ----------------------------------------------------------

def test_point_id_deterministik():
    assert point_id_for("rehber:abc") == point_id_for("rehber:abc")


def test_point_id_farkli_anahtar_farkli_id():
    assert point_id_for("rehber:abc") != point_id_for("rehber:xyz")


# --- staff_row_to_document -------------------------------------------------

def test_document_tum_alanlari_icerir():
    doc = staff_row_to_document(CELAL)
    assert "Canan Deneme" in doc
    assert "Daire Başkanı" in doc
    assert "Bilgi İşlem Daire Başkanlığı" in doc
    assert "Rektörlük" in doc            # üst birim bağlamı
    assert "1234" in doc                  # dahili telefon
    assert "cdeneme@ornek.edu.tr" in doc


def test_document_bos_alanlari_atlar():
    row = {"full_name": "Ayşe Kara", "role_title": "", "academic_title": "",
           "dept_name": "", "parent_name": "", "phone_internal": "", "email": ""}
    doc = staff_row_to_document(row)
    assert doc.startswith("Ayşe Kara")
    assert "None" not in doc              # boş alanlar 'None' sızdırmamalı
    assert "biriminde" not in doc         # birim yoksa o ibare hiç geçmemeli


def test_document_ayni_birim_tekrarlanmaz():
    row = dict(CELAL, parent_name="Bilgi İşlem Daire Başkanlığı")
    doc = staff_row_to_document(row)
    # üst birim == birim ise '>' hiyerarşisi kurulmamalı
    assert ">" not in doc


# --- staff_row_to_payload --------------------------------------------------

def test_payload_akademik_unvan_onceligi():
    row = dict(CELAL, academic_title="Prof. Dr.", role_title="Dekan")
    payload = staff_row_to_payload(row)
    assert payload["title"] == "Prof. Dr."   # akademik unvan role_title'a üstün
    assert payload["document"]               # metin dolu


# --- format_context --------------------------------------------------------

def test_format_context_numarali():
    payloads = [staff_row_to_payload(CELAL)]
    ctx = format_context(payloads)
    assert ctx.startswith("[1]")
    assert "Canan Deneme" in ctx
    # citation: her chunk'a kaynağı iliştirilmeli
    assert "[Kaynak: https://rehber.gaziantep.edu.tr/]" in ctx


def test_format_context_kaynaksiz_bilinmiyor():
    ctx = format_context([{"document": "metin", "source_url": ""}])
    assert "[Kaynak: Bilinmiyor]" in ctx


def test_format_context_bos():
    assert "bulunamadı" in format_context([])


# --- format_staff_answer (deterministik yapısal cevap) ---------------------

def test_staff_answer_tek_kisi_dahili():
    payloads = [staff_row_to_payload(CELAL)]
    ans = format_staff_answer(payloads)
    assert "Canan Deneme" in ans
    assert "dahili 1234" in ans           # numara DB'den birebir
    assert "cdeneme@ornek.edu.tr" in ans


def test_staff_answer_bos_bilmiyorum():
    # yapısal eşleşme yoksa deterministik 'Bilmiyorum' (halüsinasyon tuzağı fix)
    assert format_staff_answer([]) == "Bilmiyorum."


def test_staff_answer_baslikli_liste_uydurmaz():
    p1 = staff_row_to_payload(dict(CELAL, full_name="A B", phone_internal="1963"))
    p2 = staff_row_to_payload(dict(CELAL, full_name="C D", phone_internal="1969"))
    ans = format_staff_answer([p1, p2], header="BİDB personeli ve dahilileri:")
    assert ans.startswith("BİDB personeli ve dahilileri:")
    assert "- A B" in ans and "- C D" in ans
    # SADECE bağlamdaki numaralar geçmeli, uydurma yok
    assert "1963" in ans and "1969" in ans
    assert "2000" not in ans


# --- chunk_text (yönetmelik parçalama) -------------------------------------

def test_chunk_text_paragraf_bolme():
    text = "Birinci paragraf.\n\nİkinci paragraf."
    assert chunk_text(text) == ["Birinci paragraf.", "İkinci paragraf."]


def test_chunk_text_uzun_paragraf_cumle_bolme():
    uzun = " ".join([f"Cümle {i} burada." for i in range(60)])
    chunks = chunk_text(uzun, max_chars=200)
    assert len(chunks) > 1
    assert all(len(c) <= 220 for c in chunks)   # ~max_chars sınırında
    # hiçbir chunk cümleyi ortadan kesmemeli (nokta ile bitmeli)
    assert all(c.strip().endswith(".") for c in chunks)


def test_chunk_text_bos():
    assert chunk_text("   \n\n  ") == []


# --- structural_chunk_text (BÖLÜM/MADDE parçalama) -------------------------

def test_structural_chunk_text_madde_basliklarindan_boler():
    text = (
        "BİRİNCİ BÖLÜM\nAmaç\n\n"
        "MADDE 1 - Birinci madde metni.\n\n"
        "Madde 2 - İkinci madde metni."
    )
    chunks = structural_chunk_text(text)
    assert chunks == [
        "BİRİNCİ BÖLÜM\nAmaç",
        "MADDE 1 - Birinci madde metni.",
        "Madde 2 - İkinci madde metni.",
    ]


def test_structural_chunk_text_uzun_maddede_basligi_korur():
    body = "\n\n".join([f"Paragraf {i}. " + ("Ayrıntı " * 80) for i in range(6)])
    chunks = structural_chunk_text(f"MADDE 5 - {body}", max_chars=500)
    assert len(chunks) > 1
    assert all(chunk.startswith("MADDE 5 -") for chunk in chunks)
    assert all(len(chunk) <= 540 for chunk in chunks)


# --- build_chat_messages ---------------------------------------------------

def test_chat_messages_grounding_kurallari():
    msgs = build_chat_messages("[1] Test bağlamı", "Soru ne?")
    assert msgs[0]["role"] == "system"
    assert "Bilmiyorum" in msgs[0]["content"]     # reddetme kuralı sistemde
    assert "GAÜN" in msgs[0]["content"]
    assert "🔗 Kaynak:" in msgs[0]["content"]     # citation kuralı sistemde
    assert msgs[1]["role"] == "user"
    assert "Test bağlamı" in msgs[1]["content"]   # bağlam kullanıcıda
    assert "Soru ne?" in msgs[1]["content"]


def test_chat_messages_ozel_system_prompt():
    msgs = build_chat_messages("ctx", "q", system_prompt="ÖZEL")
    assert msgs[0]["content"] == "ÖZEL"


def test_chat_messages_history_araya_girer():
    history = [
        {"role": "user", "content": "Yatay geçiş şartları?"},
        {"role": "assistant", "content": "Şartlar şöyle..."},
    ]
    msgs = build_chat_messages("ctx", "ortalama kaç?", history=history)
    # sıra: system, (geçmiş), user
    assert msgs[0]["role"] == "system"
    assert msgs[1] == history[0]
    assert msgs[2] == history[1]
    assert msgs[3]["role"] == "user"
    assert "ortalama kaç?" in msgs[3]["content"]
