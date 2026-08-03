#!/usr/bin/env python3
"""Canlı web arama (anahtarsız DuckDuckGo) — botun 'güncel internet' kaynağı.

Yerel qwen'in bilmediği GENEL veya GÜNCEL bilgileri anlık web'den getirir;
sonuçlar LLM'e GROUNDING olarak verilir (model kendi kafasından uydurmaz,
snippet'lerden cevaplar ve kaynağı gösterir). API anahtarı GEREKTİRMEZ.

Tasarım ilkeleri:
  * Yalnız requests + BeautifulSoup (hafif, bağımsız — live_fetcher ile aynı).
  * Erişilemez/boş/şablon değişmiş → boş liste döner (çağıran dürüstçe
    'bilmiyorum' zincirine devam eder; asla çökmez).
  * DDG yönlendirme (uddg) linkleri gerçek hedef URL'ye çözülür.
  * .env ile sağlayıcı/UA override edilebilir (ileride Brave/Serper'a geçiş).
"""

from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup

# GÜVENLİK: Genel web (DDG/Brave) GEÇERLİ TLS sertifikalarına sahiptir; bu yüzden
# burada SSL doğrulaması AÇIK (verify=True — MITM koruması). verify=False yalnız
# kurum içi self-signed edu.tr siteleri için (live_fetcher) kullanılır.

# Gerçek tarayıcı UA: DDG bot-benzeri UA'lara boş sayfa dönebiliyor.
_UA = os.getenv(
    "WEB_SEARCH_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
)
DDG_HTML_URL = os.getenv("DDG_HTML_URL", "https://html.duckduckgo.com/html/")
# DDG html ucu aralıklı boş dönebiliyor (rate-limit); lite ucu daha toleranslı
# bir yedektir. İkisi de denenip ilk dolu sonuç kullanılır.
DDG_LITE_URL = os.getenv("DDG_LITE_URL", "https://lite.duckduckgo.com/lite/")

# ÜCRETSİZ + KURUMSAL için ÖNERİLEN: kendi sunucunda SearXNG (açık kaynak
# metasearch) — anahtar YOK, rate-limit YOK, sınırsız. .env'de SEARXNG_URL
# tanımlıysa BİRİNCİL kaynak olur (ör. http://127.0.0.1:8888). 8GB dar gelirse
# Oracle Cloud Always Free VM'de çalıştırılabilir. https://docs.searxng.org/
SEARXNG_URL = os.getenv("SEARXNG_URL", "").strip().rstrip("/")

# Opsiyonel anahtarlı sağlayıcı (Brave) — .env'de BRAVE_SEARCH_API_KEY varsa.
BRAVE_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
BRAVE_URL = os.getenv("BRAVE_SEARCH_URL", "https://api.search.brave.com/res/v1/web/search")

# Wikipedia (TR): anahtarsız, sınırsız, KARARLI — genel bilgi soruları için
# ücretsiz son-çare (DDG engellense bile ansiklopedik sorular yanıtlanır).
WIKI_API = os.getenv("WIKI_API_URL", "https://tr.wikipedia.org/w/api.php")

_MAX_SNIPPET = 320  # LLM bağlamını şişirmemek için snippet kırpılır

# ----- Tam sayfa okuma (derin bağlam) -----
# Arama özeti yerine en iyi sonucun SAYFA metnini çekip LLM'e verir. RAM/bağlam
# koruması: qwen 7B'yi taşırmamak ve temperature=0 tutarlılığını bozmamak için
# çekilen temiz metin MAX_PAGE_CHARS ile kırpılır. Katı timeout ile sistemi
# kilitlemez; başarısızsa çağıran snippet'e döner.
MAX_PAGE_CHARS = int(os.getenv("WEB_PAGE_MAX_CHARS", "3000"))
PAGE_TIMEOUT = float(os.getenv("WEB_PAGE_TIMEOUT", "4"))
UNI_DOMAIN = os.getenv("UNI_DOMAIN", "gaziantep.edu.tr")
_PAGE_DROP_TAGS = ("script", "style", "noscript", "nav", "header",
                   "footer", "aside", "form", "iframe")
_PAGE_TEXT_TAGS = ("h1", "h2", "h3", "h4", "p", "li", "td", "th")


def _resolve_ddg_url(href: str) -> str:
    """DDG sonuç linki genelde /l/?uddg=<encoded> yönlendirmesidir; gerçek
    hedef URL'yi çıkarır. Zaten düz URL ise olduğu gibi döner."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        q = parse_qs(urlparse(href).query)
        if "uddg" in q and q["uddg"]:
            return unquote(q["uddg"][0])
    except ValueError:
        pass
    return href


def _fetch(url: str, query: str) -> str:
    """Verilen DDG ucuna POST atar, HTML döndürür (hata → '')."""
    try:
        resp = requests.post(
            url, data={"q": query}, headers={"User-Agent": _UA},
            timeout=(5, 15))  # verify=True (varsayılan) — genel web MITM koruması
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return resp.text
    except requests.RequestException:
        return ""


def _parse_html_endpoint(html: str, max_results: int) -> "list[dict]":
    """html.duckduckgo.com sonuçları (.result__body)."""
    soup = BeautifulSoup(html, "html.parser")
    out: "list[dict]" = []
    for body in soup.select(".result__body"):
        a = body.select_one(".result__a")
        if a is None:
            continue
        sn = body.select_one(".result__snippet")
        snippet = re.sub(r"\s+", " ", sn.get_text(" ", strip=True)) if sn else ""
        out.append({
            "title": re.sub(r"\s+", " ", a.get_text(" ", strip=True)),
            "snippet": snippet[:_MAX_SNIPPET],
            "url": _resolve_ddg_url(a.get("href", "")),
        })
        if len(out) >= max_results:
            break
    return out


def _parse_lite_endpoint(html: str, max_results: int) -> "list[dict]":
    """lite.duckduckgo.com sonuçları (.result-link + .result-snippet)."""
    soup = BeautifulSoup(html, "html.parser")
    links = soup.select("a.result-link")
    snippets = soup.select(".result-snippet")
    out: "list[dict]" = []
    for i, a in enumerate(links):
        sn = snippets[i].get_text(" ", strip=True) if i < len(snippets) else ""
        out.append({
            "title": re.sub(r"\s+", " ", a.get_text(" ", strip=True)),
            "snippet": re.sub(r"\s+", " ", sn)[:_MAX_SNIPPET],
            "url": _resolve_ddg_url(a.get("href", "")),
        })
        if len(out) >= max_results:
            break
    return out


def _brave_search(query: str, max_results: int) -> "list[dict] | None":
    """Brave Search API (anahtar gerektirir). Yapılandırılmamışsa None (→ DDG'ye
    düş); yapılandırılmış ve çağrı yapıldıysa (boş olsa da) liste döner."""
    if not BRAVE_API_KEY:
        return None
    try:
        resp = requests.get(
            BRAVE_URL, params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": BRAVE_API_KEY,
                     "Accept": "application/json"},
            timeout=(5, 15))
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None  # geçici hata → DDG yedeğine düş
    out: "list[dict]" = []
    for item in (data.get("web", {}).get("results") or [])[:max_results]:
        out.append({
            "title": re.sub(r"\s+", " ", item.get("title", "") or ""),
            "snippet": re.sub(r"\s+", " ", item.get("description", "") or "")[:_MAX_SNIPPET],
            "url": item.get("url", "") or "",
        })
    return out


def _searxng_search(query: str, max_results: int) -> "list[dict] | None":
    """Kendi SearXNG örneğinden JSON arama (anahtarsız, sınırsız). Yapılandırma
    yoksa None (→ sonraki kaynağa düş)."""
    if not SEARXNG_URL:
        return None
    try:
        resp = requests.get(
            f"{SEARXNG_URL}/search",
            params={"q": query, "format": "json"},
            headers={"User-Agent": _UA}, timeout=(5, 15))
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    out: "list[dict]" = []
    for item in (data.get("results") or [])[:max_results]:
        out.append({
            "title": re.sub(r"\s+", " ", item.get("title", "") or ""),
            "snippet": re.sub(r"\s+", " ", item.get("content", "") or "")[:_MAX_SNIPPET],
            "url": item.get("url", "") or "",
        })
    return out


def _wikipedia_search(query: str, max_results: int) -> "list[dict]":
    """Wikipedia TR: başlık araması + tek toplu özet çağrısı (kararlı, ücretsiz).
    Genel/ansiklopedik sorular için son-çare; hata → []."""
    try:
        sr = requests.get(WIKI_API, params={
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "srlimit": max_results,
        }, headers={"User-Agent": _UA}, timeout=(5, 12))
        sr.raise_for_status()
        titles = [h["title"] for h in sr.json().get("query", {}).get("search", [])]
        if not titles:
            return []
        ex = requests.get(WIKI_API, params={
            "action": "query", "prop": "extracts", "exintro": 1,
            "explaintext": 1, "titles": "|".join(titles), "format": "json",
        }, headers={"User-Agent": _UA}, timeout=(5, 12))
        ex.raise_for_status()
        pages = ex.json().get("query", {}).get("pages", {})
    except (requests.RequestException, ValueError, KeyError):
        return []
    out: "list[dict]" = []
    for p in pages.values():
        title = p.get("title", "")
        extract = re.sub(r"\s+", " ", p.get("extract", "") or "")
        if not extract:
            continue
        out.append({
            "title": title,
            "snippet": extract[:_MAX_SNIPPET],
            "url": f"https://tr.wikipedia.org/wiki/{title.replace(' ', '_')}",
        })
    return out[:max_results]


# DOMAIN İZOLASYONU (search dorking): GAÜN sorgularını yalnız gaziantep.edu.tr'ye
# kısıtlar ki BAŞKA üniversitelerin (gau.edu.tr, gazi.edu.tr...) verisi cevaba
# KARIŞMASIN. Arama motorları (SearXNG/Brave/DDG) 'site:' operatörünü destekler.
GAUN_DORK = os.getenv("GAUN_SEARCH_DORK", "site:gaziantep.edu.tr")


def _apply_dork(query: str) -> str:
    """Sorgu zaten site:/gaziantep içermiyorsa GAÜN dork'unu ekler."""
    low = query.lower()
    if "site:" in low or "gaziantep" in low:
        return query
    return f"{query} {GAUN_DORK}"


def web_search(query: str, max_results: int = 5,
               gaun_scope: bool = False) -> "list[dict]":
    """Web'de arar; [{title, snippet, url}] döndürür. Hepsi boşsa [].

    gaun_scope=True: DOMAIN İZOLASYONU — sorguya 'site:gaziantep.edu.tr' eklenir
    (başka üniversite verisi karışmasın) ve GENEL Wikipedia kaynağı ATLANIR.
    gaun_scope=False (günlük hayat: hava/tarif/genel kültür): saf internet araması.

    ÜCRETSİZ + DAYANIKLI çok-kaynaklı zincir — ilk DOLU sonuç kullanılır:
    SearXNG → Brave → DuckDuckGo(html→lite) → (yalnız gaun_scope=False) Wikipedia."""
    q = (query or "").strip()
    if not q:
        return []
    if gaun_scope:
        q = _apply_dork(q)
    searx = _searxng_search(q, max_results)
    if searx:
        return searx
    brave = _brave_search(q, max_results)
    if brave:
        return brave
    for _ in range(2):
        rows = _parse_html_endpoint(_fetch(DDG_HTML_URL, q), max_results)
        if rows:
            return rows
    lite = _parse_lite_endpoint(_fetch(DDG_LITE_URL, q), max_results)
    if lite:
        return lite
    # Wikipedia GENEL bir kaynak — GAÜN-izole aramada ATLANIR (başka-kurum riski).
    if gaun_scope:
        return []
    return _wikipedia_search(q, max_results)


def format_web_context(results: "list[dict]") -> str:
    """Web sonuçlarını LLM'e verilecek numaralı GROUNDING bloğuna çevirir.
    Her sonuç kaynağıyla gelir ki model cevabında kaynak gösterebilsin."""
    if not results:
        return ""
    parcalar = []
    for i, r in enumerate(results, 1):
        parcalar.append(
            f"[{i}] {r.get('title', '')}\n{r.get('snippet', '')}\n"
            f"Kaynak: {r.get('url', '')}")
    return "\n\n".join(parcalar)


def fetch_page_text(url: str, max_chars: int = MAX_PAGE_CHARS,
                    timeout: float = PAGE_TIMEOUT) -> str:
    """Bir URL'in ANA metnini çeker: script/style/nav/header/footer/aside/form
    temizlenir; yalnız başlık ve paragraf/gövde metni (h1-h4, p, li, td) alınır.

    Bağlam/RAM koruması: temiz metin max_chars (varsayılan 3000) ile KIRPILIR
    (qwen 7B'yi ve temperature=0 tutarlılığını korur). KATI timeout (varsayılan
    4 sn) ile yavaş sayfa sistemi kilitlemez. Hata/boşlukta '' döner (çağıran
    snippet'e düşer). SSL doğrulaması AÇIK (genel web MITM koruması)."""
    if not url or not url.startswith(("http://", "https://")):
        return ""
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype.lower() and ctype:
            return ""  # PDF/binary vs. — parse etme
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        html = resp.text
    except requests.RequestException:
        return ""
    try:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(list(_PAGE_DROP_TAGS)):
            tag.decompose()
        parts = [el.get_text(" ", strip=True) for el in soup.find_all(_PAGE_TEXT_TAGS)]
        text = re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()
    except Exception:
        return ""
    return text[:max_chars]


def _pick_page_url(results: "list[dict]") -> str:
    """Tam sayfa okumak için URL seçer: ÖNCE üniversite domaini (daha güvenilir),
    yoksa ilk sonuç."""
    for r in results:
        if UNI_DOMAIN in (r.get("url") or ""):
            return r["url"]
    return results[0].get("url", "") if results else ""


def build_grounding(query: str, results: "list[dict] | None" = None,
                    gaun_scope: bool = False) -> str:
    """Cevap için GROUNDING metni üretir. Önce en iyi sonucun (üniversite domaini
    öncelikli) TAM SAYFA metnini çeker; başarısızsa arama ÖZETLERİNE (snippet)
    güvenli biçimde döner. results verilmezse web_search(gaun_scope) çağırır."""
    results = results if results is not None else web_search(query, gaun_scope=gaun_scope)
    if not results:
        return ""
    # Snippet'ler YÜKSEK SİNYALDİR: arama motoru sayfanın en alakalı kısmını
    # çıkarır (ör. "Ad Soyad: Canan DENEME Görev: Bilgi İşlem Daire Başkanı").
    # Tam sayfa DERİNLİK katar ama başı nav-menüsü olabilir (3000 char kırpması
    # ismi kaçırabilir — 2026-07-28 bilgi-işlem başkanı bug'ı). Bu yüzden İKİSİ
    # birlikte verilir: önce snippet'ler, sonra tam sayfa.
    snippets = format_web_context(results)
    url = _pick_page_url(results)
    page = fetch_page_text(url)
    if page and len(page) >= 200:
        return f"{snippets}\n\n[TAM SAYFA — Kaynak: {url}]\n{page}"
    return snippets   # tam sayfa çekilemezse yalnız snippet'ler
