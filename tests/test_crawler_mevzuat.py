"""crawler_mevzuat saf fonksiyonlarının birim testleri (ağ/DB gerektirmez)."""

import fitz

from crawler_mevzuat import dedupe, extract_main_text, extract_pdf_text, is_pdf


def test_extract_main_text_nav_header_footer_atilir():
    html = """
    <html><body>
      <nav>Menü Ana Sayfa İletişim</nav>
      <header>Site Başlığı</header>
      <main>
        <h2>Sınav Sonuçlarına İtiraz</h2>
        <p>Öğrenciler beş iş günü içinde itiraz edebilir.</p>
        <p>İtiraz dilekçe ile yapılır.</p>
      </main>
      <footer>Telif hakkı 2026</footer>
    </body></html>
    """
    text = extract_main_text(html)
    assert "beş iş günü içinde itiraz" in text
    assert "İtiraz dilekçe ile yapılır." in text
    # nav/header/footer gövdeye sızmamalı
    assert "Menü" not in text
    assert "Telif hakkı" not in text


def test_extract_main_text_container_yoksa_body_fallback():
    # bilinen container yok -> body'deki p/li alınır
    html = "<html><body><p>Gerçek içerik cümlesi budur.</p></body></html>"
    assert "Gerçek içerik cümlesi budur." in extract_main_text(html)


def test_dedupe_birebir_tekrari_atar():
    chunks = ["A metni.", "B metni.", "A metni.", "C metni.", "B metni."]
    assert dedupe(chunks) == ["A metni.", "B metni.", "C metni."]


def test_dedupe_sirayi_korur():
    assert dedupe(["ilk", "ikinci", "ilk"]) == ["ilk", "ikinci"]


# --- is_pdf ----------------------------------------------------------------

class _FakeResp:
    def __init__(self, ctype):
        self.headers = {"Content-Type": ctype}


def test_is_pdf_content_type():
    assert is_pdf(_FakeResp("application/pdf"), "https://x/dosya")


def test_is_pdf_url_uzantisi():
    # HTML content-type dönse bile .pdf uzantısı PDF sayılır
    assert is_pdf(_FakeResp("text/html"), "https://x/yonerge.PDF")


def test_is_pdf_html_degil():
    assert not is_pdf(_FakeResp("text/html; charset=utf-8"), "https://x/pages.php?url=sss")


# --- extract_pdf_text (fitz ile bellekte üretilen PDF) ---------------------

def test_extract_pdf_text():
    doc = fitz.open()
    page = doc.new_page()
    # ASCII: fitz varsayılan fontu TR karakterlerde sorun çıkarmasın
    page.insert_text((72, 72), "Sinav itirazi bes is gunu icinde yapilir.")
    data = doc.tobytes()
    doc.close()

    text = extract_pdf_text(data)
    assert "Sinav itirazi bes is gunu icinde yapilir." in text
