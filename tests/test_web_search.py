"""web_search + bot.answer_from_web birim testleri (gerçek ağ YAPMAZ)."""

import web_search


_DDG_HTML = """
<html><body>
<div class="result__body">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftr.wikipedia.org%2Fwiki%2FPython">Python - Vikipedi</a>
  <a class="result__snippet">Python, nesne yönelimli yüksek seviye bir programlama dilidir.</a>
</div>
<div class="result__body">
  <a class="result__a" href="https://ornek.com/python">Python Nedir</a>
  <a class="result__snippet">Guido van Rossum tarafından geliştirildi.</a>
</div>
</body></html>
"""


class _FakeResp:
    encoding = "utf-8"
    text = _DDG_HTML

    def raise_for_status(self):
        pass


def test_web_search_parse_ve_uddg_cozer(monkeypatch):
    monkeypatch.setattr(web_search.requests, "post", lambda *a, **k: _FakeResp())
    rows = web_search.web_search("python nedir")
    assert len(rows) == 2
    # DDG yönlendirme linki gerçek hedefe çözülmeli
    assert rows[0]["url"] == "https://tr.wikipedia.org/wiki/Python"
    assert rows[0]["title"] == "Python - Vikipedi"
    assert "programlama" in rows[0]["snippet"]
    # düz URL olduğu gibi kalmalı
    assert rows[1]["url"] == "https://ornek.com/python"


def test_web_search_tum_kaynaklar_coktuyse_bos_doner(monkeypatch):
    # SearXNG/Brave yapılandırılmamış; DDG (post) VE Wikipedia (get) çökerse [].
    def boom(*a, **k):
        raise web_search.requests.RequestException("down")
    monkeypatch.setattr(web_search, "SEARXNG_URL", "")
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "")
    monkeypatch.setattr(web_search.requests, "post", boom)
    monkeypatch.setattr(web_search.requests, "get", boom)
    assert web_search.web_search("herhangi") == []


class _FakeWiki:
    """Wikipedia iki-aşamalı API'sini taklit eder (search + extracts)."""
    def __init__(self, kind):
        self._kind = kind

    def raise_for_status(self):
        pass

    def json(self):
        if self._kind == "search":
            return {"query": {"search": [{"title": "Python (programlama dili)"}]}}
        return {"query": {"pages": {"1": {
            "title": "Python (programlama dili)",
            "extract": "Python, yüksek seviyeli bir programlama dilidir."}}}}


def test_wikipedia_son_care_calisir(monkeypatch):
    # SearXNG/Brave yok, DDG boş → Wikipedia'dan cevap gelmeli.
    monkeypatch.setattr(web_search, "SEARXNG_URL", "")
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "")
    monkeypatch.setattr(web_search.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(web_search.requests.RequestException()))
    calls = {"n": 0}

    def fake_get(url, **k):
        calls["n"] += 1
        return _FakeWiki("search" if calls["n"] == 1 else "extract")

    monkeypatch.setattr(web_search.requests, "get", fake_get)
    rows = web_search.web_search("python nedir")
    assert len(rows) == 1
    assert "programlama dili" in rows[0]["snippet"]
    assert rows[0]["url"].startswith("https://tr.wikipedia.org/wiki/")


_PAGE_HTML = """
<html><head><title>T</title>
<script>var x=1;</script><style>.a{}</style></head>
<body>
<nav>MENÜ Ana Sayfa İletişim</nav>
<header>ÜST BAŞLIK</header>
<main>
  <h1>Yaz Okulu</h1>
  <p>Yaz okulu dersleri 29 Haziran 2026 tarihinde başlar ve dört hafta sürer.</p>
  <p>Ücret ders başına belirlenir; detaylar öğrenci işlerinden alınır.</p>
</main>
<footer>ALT BİLGİ telif</footer>
</body></html>
"""


class _FakePage:
    status_code = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}
    encoding = "utf-8"
    text = _PAGE_HTML

    def raise_for_status(self):
        pass


def test_fetch_page_text_gurultu_temizler_ve_kirpar(monkeypatch):
    monkeypatch.setattr(web_search.requests, "get", lambda *a, **k: _FakePage())
    txt = web_search.fetch_page_text("https://x.gaziantep.edu.tr/yaz")
    # ana metin var
    assert "Yaz Okulu" in txt and "29 Haziran 2026" in txt
    # script/style/nav/header/footer temizlendi
    assert "var x" not in txt and "MENÜ" not in txt and "ALT BİLGİ" not in txt
    # kırpma sınırı uygulanır
    short = web_search.fetch_page_text("https://x.gaziantep.edu.tr/yaz", max_chars=20)
    assert len(short) <= 20


def test_fetch_page_text_hata_bos_doner(monkeypatch):
    monkeypatch.setattr(web_search.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(web_search.requests.RequestException()))
    assert web_search.fetch_page_text("https://x.edu.tr") == ""
    # geçersiz şema
    assert web_search.fetch_page_text("ftp://x") == ""


def test_fetch_page_text_binary_atlar(monkeypatch):
    class _Bin:
        headers = {"Content-Type": "application/pdf"}
        def raise_for_status(self): pass
    monkeypatch.setattr(web_search.requests, "get", lambda *a, **k: _Bin())
    assert web_search.fetch_page_text("https://x/belge.pdf") == ""


def test_build_grounding_tam_sayfa_kullanir(monkeypatch):
    monkeypatch.setattr(web_search, "fetch_page_text",
                        lambda url, **k: "A" * 300)   # anlamlı tam sayfa
    ctx = web_search.build_grounding(
        "soru", [{"title": "T", "snippet": "önemli özet", "url": "https://gaziantep.edu.tr/x"}])
    # Artık SNIPPET (yüksek sinyal) + TAM SAYFA (derinlik) birlikte verilir
    assert "önemli özet" in ctx                                   # snippet
    assert "[TAM SAYFA — Kaynak: https://gaziantep.edu.tr/x]" in ctx and "AAA" in ctx


def test_build_grounding_sayfa_bosonca_snippet_dondurur(monkeypatch):
    monkeypatch.setattr(web_search, "fetch_page_text", lambda url, **k: "")  # sayfa çekilemedi
    ctx = web_search.build_grounding(
        "soru", [{"title": "Baslik", "snippet": "ozet metin", "url": "https://x.com"}])
    assert "ozet metin" in ctx and "Kaynak: https://x.com" in ctx   # snippet'e geri döndü


def test_build_grounding_universite_domainini_secer(monkeypatch):
    picked = {}
    monkeypatch.setattr(web_search, "fetch_page_text",
                        lambda url, **k: picked.setdefault("url", url) or "A" * 300)
    web_search.build_grounding("soru", [
        {"title": "genel", "snippet": "s", "url": "https://baska.com/a"},
        {"title": "uni", "snippet": "s", "url": "https://egitim.gaziantep.edu.tr/b"},
    ])
    assert picked["url"] == "https://egitim.gaziantep.edu.tr/b"   # üni domaini önce


def test_searxng_yapilandirilmissa_birincil(monkeypatch):
    monkeypatch.setattr(web_search, "SEARXNG_URL", "http://searx.local")

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"results": [
            {"title": "T", "content": "içerik", "url": "https://ornek.com"}]}
    monkeypatch.setattr(web_search.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(web_search.requests, "post", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("SearXNG varken DDG'ye gidilmemeli")))
    rows = web_search.web_search("bir soru")
    assert rows == [{"title": "T", "snippet": "içerik", "url": "https://ornek.com"}]


def test_web_search_bos_sorgu_bos_doner():
    assert web_search.web_search("   ") == []


def test_domain_izolasyonu_gaun_dork_ekler(monkeypatch):
    """gaun_scope=True → sorguya site:gaziantep.edu.tr eklenir + Wikipedia ATLANIR."""
    monkeypatch.setattr(web_search, "SEARXNG_URL", "")
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "")
    seen = []
    monkeypatch.setattr(web_search, "_fetch", lambda url, q: seen.append(q) or "")
    wiki = {"called": False}
    monkeypatch.setattr(web_search, "_wikipedia_search",
                        lambda q, n: wiki.__setitem__("called", True) or [])
    web_search.web_search("yaz okulu ücreti", gaun_scope=True)
    assert all("site:gaziantep.edu.tr" in q for q in seen)   # dork zorunlu
    assert wiki["called"] is False   # GAÜN-izole aramada genel Wikipedia atlanır


def test_saf_arama_gunluk_hayat_dork_yok(monkeypatch):
    """gaun_scope=False (günlük hayat) → dork YOK, Wikipedia devrede."""
    monkeypatch.setattr(web_search, "SEARXNG_URL", "")
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "")
    seen = []
    monkeypatch.setattr(web_search, "_fetch", lambda url, q: seen.append(q) or "")
    wiki = {"called": False}
    monkeypatch.setattr(web_search, "_wikipedia_search",
                        lambda q, n: wiki.__setitem__("called", True) or [])
    web_search.web_search("bugün hava nasıl", gaun_scope=False)
    assert all("site:" not in q for q in seen)   # saf sorgu
    assert wiki["called"] is True   # genel kaynak (Wikipedia) devrede


class _FakeBrave:
    def raise_for_status(self):
        pass

    @staticmethod
    def json():
        return {"web": {"results": [
            {"title": "Python", "description": "yüksek seviye dil",
             "url": "https://python.org"}]}}


def test_web_search_brave_anahtar_varsa_onu_kullanir(monkeypatch):
    # Anahtar ayarlıysa DDG'ye HİÇ gidilmemeli; Brave sonucu dönmeli.
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "test-key")
    monkeypatch.setattr(web_search.requests, "get", lambda *a, **k: _FakeBrave())
    monkeypatch.setattr(web_search.requests, "post", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("Brave varken DDG'ye gidilmemeli")))
    rows = web_search.web_search("python nedir")
    assert len(rows) == 1 and rows[0]["url"] == "https://python.org"


def test_web_search_brave_yoksa_ddg_kullanir(monkeypatch):
    # Anahtar yoksa Brave atlanır, DDG kullanılır.
    monkeypatch.setattr(web_search, "BRAVE_API_KEY", "")
    monkeypatch.setattr(web_search.requests, "post", lambda *a, **k: _FakeResp())
    rows = web_search.web_search("python nedir")
    assert len(rows) == 2  # DDG fixture'ından


def test_format_web_context_kaynakli_blok():
    ctx = web_search.format_web_context([
        {"title": "T", "snippet": "S", "url": "https://x.com"}])
    assert "[1] T" in ctx and "Kaynak: https://x.com" in ctx
    assert web_search.format_web_context([]) == ""


def test_answer_from_web_grounded_cevap(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "web_search",
                        lambda q, *a, **k: [{"title": "Python", "snippet": "yüksek seviye dil",
                                             "url": "https://tr.wikipedia.org/wiki/Python"}])
    monkeypatch.setattr(bot, "generate",
                        lambda cfg, ctx, q, sp, hist=None: "Python bir programlama dilidir.\n\n🔗 Kaynak: https://tr.wikipedia.org/wiki/Python")

    out = bot.answer_from_web(object(), "python nedir")
    assert out is not None and "programlama" in out


def test_answer_from_web_sonuc_yoksa_none(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "web_search", lambda q, *a, **k: [])
    assert bot.answer_from_web(object(), "asdf") is None


def test_answer_from_web_model_bilmiyorsa_none(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "web_search",
                        lambda q, *a, **k: [{"title": "x", "snippet": "y", "url": "https://z.com"}])
    monkeypatch.setattr(bot, "generate", lambda *a, **k: "Bu konuda net bir bilgi bulamadım.")
    assert bot.answer_from_web(object(), "çok belirsiz soru") is None


def test_reformulation_retry_ikinci_arama(monkeypatch):
    """ANTİ-KİLİTLENME: ilk arama boşsa sorgu reformüle edilip 2. kez aranır."""
    import bot
    calls = []

    def fake_ws(q, max_results=5, gaun_scope=False):
        calls.append(q)
        return [] if len(calls) == 1 else [{"title": "t", "snippet": "cvp", "url": "x"}]

    monkeypatch.setattr(bot, "web_search", fake_ws)
    monkeypatch.setattr(bot, "_reformulate_query", lambda cfg, q: "bütünleme sınav tarihleri")
    monkeypatch.setattr(bot, "build_grounding", lambda q, r, gaun_scope=False: "ctx")
    monkeypatch.setattr(bot, "generate", lambda *a, **k: "Bütünleme sınavı Ağustos'ta yapılır.")

    out = bot.answer_from_web(object(), "bütler ne zaman", gaun_scope=True)
    assert out is not None and "Ağustos" in out
    assert len(calls) == 2 and calls[1] == "bütünleme sınav tarihleri"


def test_dynamic_not_found_yonlendirici_link_temizler(monkeypatch):
    """Statik 'Bilmiyorum' yerine dinamik yönlendirici mesaj; uydurma link silinir."""
    import bot

    class Cfg:
        llm_model = "x"

    monkeypatch.setattr(bot.ollama, "chat", lambda **k: {"message": {"content":
        "Bu spesifik bilgiye ulaşamadım; Öğrenci İşleri Daire Başkanlığı'na "
        "danışabilir veya farklı kelimelerle sorabilirsin. https://uydurma.link"}})
    msg = bot._dynamic_not_found(Cfg(), "spesifik bir soru")
    assert "Öğrenci İşleri" in msg
    assert "http" not in msg   # temperature>0'da sızan uydurma link temizlendi


def test_dynamic_not_found_llm_cokerse_statige_doner(monkeypatch):
    import bot
    from rag_pipeline import graceful_not_found

    class Cfg:
        llm_model = "x"

    monkeypatch.setattr(bot.ollama, "chat",
                        lambda **k: (_ for _ in ()).throw(Exception("ollama down")))
    msg = bot._dynamic_not_found(Cfg(), "yatay geçiş şartları")
    assert msg == graceful_not_found("yatay geçiş şartları")


def test_semantic_no_answer_garbage_ve_bilmiyorum_yakalar():
    import bot
    # Temiz "Bilmiyorum"
    assert bot._semantic_no_answer("Bilmiyorum.")
    # qwen talimat/grounding-yok sızıntısı (is_bilmiyorum kaçırıyordu)
    assert bot._semantic_no_answer(
        'Cevap yoksa "Bilmiyorum" de. Soru, verilen metinlerle ilgili değil.')
    assert bot._semantic_no_answer("Bu konuda bilgi bulamadım.")
    # Gerçek cevap → False (web fallback'e düşmemeli)
    assert not bot._semantic_no_answer(
        "Kubernetes, konteyner orkestrasyon aracıdır.")
