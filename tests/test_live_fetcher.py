"""live_fetcher saf fonksiyon testleri (ağ gerektirmez)."""

from live_fetcher import clean_text


def test_clean_text_nav_footer_atilir():
    html = """
    <html><body>
      <nav>Menü Ana Sayfa</nav>
      <main>
        <h2>Günün Menüsü</h2>
        <p>Mercimek çorbası, tavuk sote, pilav.</p>
      </main>
      <footer>Telif 2026</footer>
    </body></html>
    """
    text = clean_text(html)
    assert "Mercimek çorbası, tavuk sote, pilav." in text
    assert "Günün Menüsü" in text
    assert "Telif" not in text
    assert "Menü Ana Sayfa" not in text


def test_clean_text_bos():
    assert clean_text("<html><body></body></html>") == ""


def test_clean_text_hedefli_selector():
    # selector verilince YALNIZ o blok alınır; sayfa gürültüsü dışarıda kalır
    html = """
    <html><body>
      <div class="menu-gurultu">Ana Sayfa Kütüphane İletişim</div>
      <div class="duyurular">
        <a>09 Temmuz Bütünleme sınav takvimi açıklandı</a>
        <a>07 Temmuz Yaz okulu kayıtları başladı</a>
      </div>
    </body></html>
    """
    text = clean_text(html, (".duyurular",))
    assert "Bütünleme sınav takvimi açıklandı" in text
    assert "Yaz okulu kayıtları başladı" in text
    assert "Kütüphane İletişim" not in text  # hedef dışı blok gelmemeli
