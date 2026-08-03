#!/usr/bin/env python3
"""GAUN Chatbot — Anlık (canlı) veri çekici (Faz 9).

Cevabı anlık değişen konular (bugünkü yemek, güncel duyurular) için Qdrant/cache
YERİNE doğrudan üniversite sitesinden istek atıp temiz metni döndürür. bot.py
bu metni LLM'e bağlam olarak verir ve canlı cevap ürettirir.

Yalnız requests + BeautifulSoup kullanır (hafif, bağımsız). Seed URL'ler .env
ile geçersiz kılınabilir; edu/gov TR sertifikaları için verify=False.
"""

import os
import re

import requests
import urllib3
from bs4 import BeautifulSoup

from rag_pipeline import _TR_AYLAR, normalize_turkish

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USER_AGENT = os.getenv("CRAWLER_USER_AGENT", "GaunChatbotCrawler/0.1 (BIDB)")

# Canlı personel rehberi: SQL'de bulunamayan kişi için son-çare anlık POST
# sorgusu (crawler_rehber ile aynı hedef/şablon). .env ile override edilebilir.
REHBER_LIVE_URL = os.getenv("REHBER_LIVE_URL", "https://rehber.gaziantep.edu.tr/")

# Konu -> seed URL. Canlı sitede DOĞRULANMALI (.env ile override edilebilir).
LIVE_SOURCES = {
    # Canlı sitede doğrulandı (2026-07-09): günlük yemek listesi + ana sayfa.
    "yemek": os.getenv("LIVE_YEMEK_URL", "https://www.gaziantep.edu.tr/yemek/"),
    "duyuru": os.getenv("LIVE_DUYURU_URL", "https://www.gaziantep.edu.tr/"),
}

_DROP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "form", "aside")
_MAX_CHARS = 3000  # LLM bağlamını şişirmemek için çekilen metin kırpılır

# Duyuru için hedeflenmiş bloklar — ana sayfanın tümü yerine yalnız duyuru/haber
# alanı (canlı sitede doğrulandı: .duyurular ~ tarih+başlık listesi, .haber_container).
_DUYURU_SELECTORS = (".duyurular", ".haber_container", ".gaun_haber",
                     ".news", ".announcements", ".duyuru")
_TEXT_TAGS = ("h1", "h2", "h3", "h4", "p", "li", "td", "th")


def clean_text(html: str, selectors: "tuple | None" = None) -> str:
    """HTML'den nav/footer/script'i atıp temiz metni çıkarır.

    selectors verilirse (örn. duyuru blokları) YALNIZ o container'ların metni
    alınır (hedefli); yoksa genel gövde (main/#content/body) taranır.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()

    if selectors:
        roots = soup.select(", ".join(selectors))
        if roots:
            text = " ".join(r.get_text(" ", strip=True) for r in roots)
            return re.sub(r"\s+", " ", text).strip()

    container = soup.select_one("main, article, #content, .content") or soup.body or soup
    blocks = []
    for el in container.find_all(list(_TEXT_TAGS)):
        text = el.get_text(" ", strip=True)
        if len(text) > 1:
            blocks.append(text)
    return re.sub(r"\s+", " ", " ".join(blocks)).strip()


def fetch_unit_page(url: str) -> str:
    """Verilen (küratörlü/doğrulanmış) birim URL'sinden anlık temiz metin çeker.

    Genel internet gezintisi DEĞİLDİR — yalnız _UNIT_LINKS'teki (rag_pipeline.py)
    zaten doğrulanmış, sabit birim URL'lerine bakılır; keyfi bir URL çekilmez.
    Son çare kullanılır: yerel SSS/DB'de cevap yoksa, "Bilmiyorum" demeden önce
    ilgili birimin kendi resmi sayfasından o an bir şey bulunabiliyor mu diye
    bakılır (2026-07-22, "Mühendislik Fakültesi dekanı" örneği — kaynak veride
    yoktu). Ulaşılamazsa veya hata alırsa sessizce boş döner (bot.py mevcut
    'Bilmiyorum' zincirine devam eder)."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                            timeout=(10, 20), verify=False)
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return clean_text(resp.text)[:_MAX_CHARS]
    except requests.RequestException:
        return ""


def parse_todays_menu(page_text: str, now) -> "list[str]":
    """Yemek sayfasının HAM metninden BUGÜNÜN menü kalemlerini DETERMİNİSTİK
    çıkarır (LLM YOK). Sayfa aylık bir ızgara + 'afiyet olsun' sonrası günlük
    panel içerir; LLM bu karışık yapıda günü/menüyü şaşırıyordu (2026-07-24:
    "24 Temmuz Cumartesi" — 24 Temmuz aslında Cuma; hem gün yanlış hem menü
    başka güne aitti). Bugünün bloğu 'afiyet olsun' ibaresinden sonra başlar
    ve bir sonraki (farklı) gün işaretine kadar sürer. Kalemler 'AD (NNN kcal)'
    kalıbından alınır. Bulunamazsa boş liste döner.

    now: Türkiye saatiyle bugünün datetime'ı (rag_pipeline.now_turkey())."""
    day, month = now.day, _TR_AYLAR[now.month]
    text = re.sub(r"\s+", " ", page_text)
    anchor = text.find("afiyet olsun")
    if anchor >= 0:
        block = text[anchor + len("afiyet olsun"):]
    else:
        m = re.search(rf"\b{day} {month}\b", text)
        if not m:
            return []
        block = text[m.start():]
    # Bir sonraki FARKLI gün işaretine (örn. '23 Temmuz') kadar kes.
    nxt = re.search(rf"\b(?!{day} {month}\b)\d{{1,2}} {month}\b", block)
    if nxt:
        block = block[:nxt.start()]
    # Baştaki 'DD Ay YYYY Gün' tarih başlığını at.
    block = re.sub(rf"^\s*{day} {month}\s+\d{{4}},?\s+\S+\s+", "", block)
    items = []
    for raw in re.findall(r"([^()]+?\(\s*\d+\s*kcal\s*\))", block):
        it = re.sub(r"\s+", " ", raw).strip(" ,")
        it = re.sub(r"\s*\(\s*(\d+)\s*kcal\s*\)", r" (\1 kcal)", it)
        if it:
            items.append(it)
    return items


def fetch_todays_menu_raw() -> str:
    """Yemek sayfasının HAM (kırpılmamış) düz metnini çeker — parse_todays_menu
    için. Ulaşılamazsa boş string."""
    url = LIVE_SOURCES["yemek"]
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                            timeout=(10, 30), verify=False)
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(list(_DROP_TAGS)):
            tag.decompose()
        return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    except requests.RequestException:
        return ""


def fetch_staff_from_directory(name_tokens: "list[str]") -> "list[dict]":
    """SQL'de bulunamayan personeli CANLI rehberden son çare olarak çeker ve
    ARANAN İSME göre sıkı filtreler.

    Akış: rehberin POST formuna en ayırt edici (en uzun) ad token'ı 'kelime'
    olarak gönderilir; dönen personel tablosu crawler_rehber.parse_rehber_html
    ile ayrıştırılır; SADECE normalize edilmiş tam adı name_tokens'ın TÜMÜNÜ
    (AND) içeren kayıtlar bırakılır — rehber alt-dize eşlemesi çok sonuç
    döndürebilir, yanlış kişiyi sunmamak için filtre zorunlu (search_staff_by_
    tokens ile aynı mantık).

    Dönen liste format_staff_answer'ın beklediği DETERMİNİSTİK payload'lardır;
    LLM'e VERİLMEZ (personel isim halüsinasyonu kalkanı korunur). Ulaşılamaz /
    şablon değişmiş / eşleşme yoksa boş liste döner (bot.py dürüst 'personel
    bulunamadı' yönlendirmesine devam eder). Ağı asla dışarı sızdırmaz: yalnız
    tek, sabit rehber URL'sine POST atar."""
    tokens = [normalize_turkish(t) for t in (name_tokens or []) if len(t) >= 3]
    if not tokens:
        return []
    kelime = max(tokens, key=len)  # en uzun token en ayırt edicidir
    try:
        resp = requests.post(
            REHBER_LIVE_URL, data={"kelime": kelime, "arama": ""},
            headers={"User-Agent": USER_AGENT}, timeout=(5, 12), verify=False)
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        html = resp.text
    except requests.RequestException:
        return []
    # Tembel import: crawler_rehber ağır bağımlılıkları (mysql) modül yüklenirken
    # değil, yalnız bu son-çare çağrıldığında gelsin.
    from crawler_rehber import parse_rehber_html

    matched: "list[dict]" = []
    for rec in parse_rehber_html(html, REHBER_LIVE_URL):
        name_n = normalize_turkish(rec.full_name)
        if all(t in name_n for t in tokens):
            matched.append({
                "full_name": rec.full_name,
                "title": rec.academic_title or rec.role_title,
                "department": rec.alt_birim,
                "parent_department": rec.ust_birim,
                "phone_internal": rec.phone_internal,
                "email": rec.email,
                "source_url": REHBER_LIVE_URL,
            })
            if len(matched) >= 10:  # bir isim için makul üst sınır
                break
    return matched


def fetch_live_data(intent_type: str, query: str = "") -> dict:
    """Konuya göre anlık metni çeker. {'document', 'source_url'} döndürür.

    'duyuru' konusunda ana sayfanın tümü yerine hedefli duyuru/haber blokları
    çekilir. Ulaşılamazsa document boş döner (bot.py bunu 'Bilmiyorum'a çevirir).
    """
    url = LIVE_SOURCES.get(intent_type, LIVE_SOURCES["duyuru"])
    selectors = _DUYURU_SELECTORS if intent_type == "duyuru" else None
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                            timeout=(10, 30), verify=False)
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        text = clean_text(resp.text, selectors)[:_MAX_CHARS]
    except requests.RequestException:
        text = ""
    return {"document": text, "source_url": url}
