"""crawler_rehber saf fonksiyonlarının birim testleri (DB/ağ gerektirmez)."""

from pathlib import Path

import crawler_rehber as crawler_module
from crawler_rehber import (
    StaffRecord,
    build_external_key,
    clean_phone,
    normalize_turkish,
    parse_rehber_html,
    record_external_key,
    slugify,
    split_title,
)

FIXTURE = Path(__file__).parent / "fixtures" / "rehber_ornek.html"


# --- normalize_turkish ---------------------------------------------------

def test_normalize_temel():
    assert normalize_turkish("Çelik Yıldız") == "celik yildiz"


def test_normalize_buyuk_i_ve_noktasiz():
    # Türkçe I/İ ayrımı: ikisi de ascii 'i'ye katlanmalı
    assert normalize_turkish("İsmail IŞIK") == "ismail isik"


def test_normalize_sapkali_harfler():
    assert normalize_turkish("Kâzım") == "kazim"


def test_normalize_fazla_bosluk():
    assert normalize_turkish("  Ali   Veli ") == "ali veli"


# --- slugify --------------------------------------------------------------

def test_slugify_migration_tohumuyla_uyumlu():
    # 001_entity_tables.sql'deki örnek tohum slug'ıyla birebir aynı üretilmeli
    assert slugify("Bilgi İşlem Daire Başkanlığı") == "bilgi-islem-daire-baskanligi"


# --- split_title -----------------------------------------------------------

def test_split_title_akademik():
    assert split_title("Prof. Dr.") == ("Prof. Dr.", None)
    assert split_title("Öğr. Gör.") == ("Öğr. Gör.", None)
    assert split_title("Arş. Gör.") == ("Arş. Gör.", None)
    # canlı sitedeki uzun yazımlar
    assert split_title("Profesör") == ("Profesör", None)
    assert split_title("Doktor Öğretim Üyesi") == ("Doktor Öğretim Üyesi", None)


def test_split_title_idari():
    assert split_title("Daire Başkanı") == (None, "Daire Başkanı")
    # 'Müdür' ve 'Kadrolu' içindeki 'dr' harfleri yanlış eşleşmemeli
    assert split_title("Müdür") == (None, "Müdür")
    assert split_title("Kadrolu İşçi") == (None, "Kadrolu İşçi")


def test_split_title_bos():
    assert split_title(None) == (None, None)
    assert split_title("") == (None, None)


# --- build_external_key ----------------------------------------------------

def test_external_key_profil_url_oncelikli():
    key = build_external_key("https://x.edu.tr/p/1", "https://kaynak", "ali veli")
    assert key == "https://x.edu.tr/p/1"


def test_external_key_deterministik():
    a = build_external_key(None, "https://kaynak", "ali veli", "TIP FAKÜLTESİ")
    b = build_external_key(None, "https://kaynak", "ali veli", "TIP FAKÜLTESİ")
    assert a == b
    assert a.startswith("rehber:")


def test_external_key_ayni_isim_farkli_birim_cakismaz():
    # Farklı birimlerdeki aynı isimli iki kişi tek kayda ezilmemeli
    a = build_external_key(None, "https://kaynak", "ahmet yilmaz", "TIP FAKÜLTESİ")
    b = build_external_key(None, "https://kaynak", "ahmet yilmaz", "MÜHENDİSLİK FAKÜLTESİ")
    assert a != b


def test_record_external_key_upsert_girdileriyle_uyumlu():
    rec = StaffRecord(
        full_name="Caner Yılmaz",
        academic_title=None,
        role_title=None,
        ust_birim="MÜHENDİSLİK FAKÜLTESİ",
        alt_birim="BİLGİSAYAR MÜHENDİSLİĞİ",
        phone_internal="1234",
        email=None,
        profile_url=None,
        source_url="https://rehber.gaziantep.edu.tr/",
    )
    search_name = normalize_turkish(rec.full_name)
    birim_context = f"{rec.ust_birim or ''} {rec.alt_birim or ''}"
    assert record_external_key(rec) == build_external_key(
        rec.profile_url, rec.source_url, search_name, birim_context)


# --- clean_phone ------------------------------------------------------------

def test_clean_phone_gecerli():
    assert clean_phone(" 1234 ") == "1234"


def test_clean_phone_rakamsiz():
    assert clean_phone("yok") is None


# --- parse_rehber_html (POST cevabı / tables[3], 5-td markup şablonu) --------

def test_parse_fixture_uc_gecerli_kayit():
    html = FIXTURE.read_text(encoding="utf-8")
    records = parse_rehber_html(html, "https://rehber.gaziantep.edu.tr/")

    # bozuk başlık, 3-td'li satır ve adsız satır atlanır -> 3 kayıt
    assert len(records) == 3

    ahmet = records[0]
    assert ahmet.full_name == "VELİ ÖRNEK"          # td[1] kalan metin + td[2]
    assert ahmet.academic_title == "Profesör"         # td[1] <i> içeriği
    assert ahmet.role_title is None
    assert ahmet.ust_birim == "MÜHENDİSLİK FAKÜLTESİ"  # td[4] <b>
    assert ahmet.alt_birim == "FİZİK MÜHENDİSLİĞİ"     # td[4] <i>
    assert ahmet.phone_internal == "9001"

    celal = records[1]
    assert celal.full_name == "CANAN DENEME"
    assert celal.academic_title is None
    assert celal.role_title == "Daire Başkanı"        # idari unvan
    assert celal.ust_birim == "BİLGİ İŞLEM DAİRE BAŞKANLIĞI"
    assert celal.alt_birim is None                    # <i> yok -> None

    merve = records[2]                                # geri düşüş yolu
    assert merve.full_name == "ZEHRA TEST"
    assert merve.academic_title is None and merve.role_title is None
    assert merve.ust_birim == "GENEL SEKRETERLİK"     # <b> yok -> düz metin
    assert merve.alt_birim is None


def test_parse_dort_tablodan_az_ise_bos_liste():
    # Sonuçsuz cevap 4 tablo döndürür (canlıda doğrulandı) -> boş liste + alarm
    html = "<table><tr><td>tek tablo</td></tr></table>"
    assert parse_rehber_html(html, "x") == []


def test_parse_personel_tablosu_bos_ise_bos_liste():
    html = ("<table></table><table></table><table></table>"
            "<table><tr><th>Bozuk</th></tr></table><table></table>")
    assert parse_rehber_html(html, "x") == []


# --- main seed POST döngüsü --------------------------------------------------

def test_main_seed_list_uzerinden_post_atip_seedleri_strip_etmez(monkeypatch):
    calls = []
    sleeps = []

    def fake_fetch(url, user_agent, payload):
        calls.append((url, user_agent, payload.copy()))
        return "<html></html>"

    def fake_parse(html, source_url):
        return [StaffRecord(
            full_name="CANER YILMAZ",
            academic_title=None,
            role_title=None,
            ust_birim="MÜHENDİSLİK FAKÜLTESİ",
            alt_birim="BİLGİSAYAR MÜHENDİSLİĞİ",
            phone_internal="1234",
            email=None,
            profile_url=None,
            source_url=source_url,
        )]

    monkeypatch.setattr(crawler_module, "SEED_LIST", ("er ", "can"))
    monkeypatch.setattr(crawler_module, "robots_allows", lambda url, user_agent: True)
    monkeypatch.setattr(crawler_module, "fetch", fake_fetch)
    monkeypatch.setattr(crawler_module, "parse_rehber_html", fake_parse)
    monkeypatch.setattr(crawler_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(crawler_module.random, "uniform", lambda start, end: 2.0)

    assert crawler_module.main(["--url", "https://rehber.gaziantep.edu.tr/"]) == 0

    assert [payload["kelime"] for _, _, payload in calls] == ["er ", "can"]
    assert all(payload["arama"] == "" for _, _, payload in calls)
    assert sleeps == [2.0]
