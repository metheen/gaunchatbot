#!/usr/bin/env python3
"""GAUN Chatbot — Otonom Veri Hasadı Crawler (Faz 16).

Üniversitenin subdomain'lerini (fakülteler, enstitüler, daire başkanlıkları)
depth=1 tarar: ana sayfa + 'Hakkımızda/Yönetim/Dekanlık/Bölümler/İletişim' gibi
alt sayfalar. Temizlenmiş metni offline_data/scraped_<anahtar>.md olarak yazar,
sonunda offline_ingest.py'yi tetikleyip Qdrant 'regulations' koleksiyonuna gömer.

Kurumsal nezaket: her istek arasında time.sleep(2); dürüst User-Agent;
bağlantı hataları try-except ile atlanır (script çökmüz). edu/gov TR
sertifikaları için verify=False.

  ./.venv/bin/python gaun_crawler.py
  ./.venv/bin/python gaun_crawler.py --limit 3   # ilk 3 siteyle deneme
"""

import argparse
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger("gaun_crawler")

TARGET_URLS = {
    "ana_sayfa": "https://www.gaziantep.edu.tr",
    # "mevzuat": KALDIRILDI — URL 404 (ölü). Öğrenci işleri zaten 'ogrenci_isleri'
    #   (oidb.gaziantep.edu.tr) ile kapsanıyor; mevzuat/yönetmelik korpusu ise
    #   crawler_mevzuat.py + Qdrant 'regulations' ile besleniyor.
    # "obs_duyurular": KALDIRILDI — obs.gaziantep.edu.tr bir JS login SPA'sı;
    #   render edilse bile crawllanacak DOM içeriği yok (gövde boş).
    "uzaktan_egitim": "https://guzem.gaziantep.edu.tr",
    "erasmus_dis_iliskiler": "https://int.gaziantep.edu.tr",
    "ogrenci_isleri": "https://oidb.gaziantep.edu.tr",
    "sks_daire": "https://sksdb.gaziantep.edu.tr",
    "bilgi_islem": "https://bidb.gaziantep.edu.tr",
    "personel_daire": "https://personel.gaziantep.edu.tr",
    "kutuphane": "https://kutuphane.gaziantep.edu.tr",
    "strateji_gelistirme": "https://strateji.gaziantep.edu.tr",
    "idari_mali_isler": "https://imidb.gaziantep.edu.tr",
    "yapi_isleri": "https://yapi.gaziantep.edu.tr",
    "muhendislik_fakultesi": "https://mf.gaziantep.edu.tr",
    "tip_fakultesi": "https://tip.gaziantep.edu.tr",
    "egitim_fakultesi": "https://egitim.gaziantep.edu.tr",
    "dis_hekimligi": "https://dis.gaziantep.edu.tr",
    "fen_edebiyat": "https://fef.gaziantep.edu.tr",
    "iktisadi_idari": "https://iibf.gaziantep.edu.tr",
    "havacilik_uzay": "https://havacilik.gaziantep.edu.tr",
    "mimarlik_fakultesi": "https://mimarlik.gaziantep.edu.tr",
    "hukuk_fakultesi": "https://hukuk.gaziantep.edu.tr",
    "iletisim_fakultesi": "https://iletisim.gaziantep.edu.tr",
    "ilahiyat_fakultesi": "https://ilahiyat.gaziantep.edu.tr",
    "guzel_sanatlar": "https://gsf.gaziantep.edu.tr",
    "saglik_bilimleri_fak": "https://sbf.gaziantep.edu.tr",
    "turizm_fakultesi": "https://turizm.gaziantep.edu.tr",
    "spor_bilimleri": "https://spor.gaziantep.edu.tr",
    "fen_bilimleri_enst": "https://fbe.gaziantep.edu.tr",
    "sosyal_bilimler_enst": "https://sbe.gaziantep.edu.tr",
    "saglik_bilimleri_enst": "https://saglik.gaziantep.edu.tr",
    "egitim_bilimleri_enst": "https://ebe.gaziantep.edu.tr",
    "goc_enstitusu": "https://goc.gaziantep.edu.tr",
    "yabanci_diller_yo": "https://yabancidiller.gaziantep.edu.tr",
    "turk_musikisi_kons": "https://tmk.gaziantep.edu.tr",
    "gaziantep_myo": "https://gmyo.gaziantep.edu.tr",
    "naci_topcuoglu_myo": "https://nacitopcuoglu.gaziantep.edu.tr",
    "saglik_hizmetleri_myo": "https://shmyo.gaziantep.edu.tr",
}

USER_AGENT = "GaunAI-Bot/1.0 (Bilgi Islem Daire Bsk. Projesi)"
SLEEP_SECONDS = 2
TIMEOUT = (8, 15)  # (connect, read) — ölü subdomain'lerde hızlı vazgeçme
MAX_SUBPAGES = 5   # site başına alt sayfa üst sınırı (tarama bounded kalsın)
OUT_DIR = Path(__file__).resolve().parent / "offline_data"

# Silinecek yapısal etiketler (gürültü azaltma).
_DROP_TAGS = ("nav", "footer", "aside", "header", "script", "style", "form", "noscript")
# İçerik kökü adayları — clean_text bunlar + <body> arasından EN ÇOK metin
# üreteni seçer (küçük bir yan panele kilitlenip gövdeyi kaçırmamak için).
_CONTENT_SELECTORS = (
    "main", "article", "#content", "#icerik", "#main",
    ".content", ".page-content", ".icerik", ".main-content", ".entry-content",
)
_TEXT_TAGS = ("h1", "h2", "h3", "h4", "h5", "p", "li", "td", "th", "blockquote", "dd")
# Alt sayfa (depth=1) filtresi: link METNİ değil, URL YOLU şu kalıba uymalı
# (yönetim/kadro/iletişim). Böylece başlığında 'akademik/idari' geçen DUYURU
# linkleri elenir; ayrıca duyuru/haber/detay yolları kesin dışlanır.
_SUBPAGE_PATH_RE = re.compile(
    r"(?:^|[/_-])("
    r"hakkimizda|hakkinda|yonetim|yonetici|"
    r"dekan|dekanlik|dekanlar|mudur|mudurluk|baskan|baskanlik|rektor|"
    r"iletisim|idari|personel|kadro|akademik-personel|akademik-kadro"
    r")(?:[/_.-]|$)"
)
_SUBPAGE_DENY_RE = re.compile(
    r"(duyuru|haber|detay|etkinlik|ilan|galeri|foto|video|arsiv|takvim|"
    r"slider|announce|news|event)"
)


def fetch(session: requests.Session, url: str) -> "str | None":
    """URL'yi çeker; hatalarda None döner (script çökmesin)."""
    try:
        resp = session.get(url, timeout=TIMEOUT, verify=False)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "").lower()
        if "text/html" not in ctype and "text" not in ctype:
            return None  # PDF/binary alt sayfaları atla
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return resp.text
    except requests.RequestException as exc:
        log.warning("    atlandı (%s): %s", type(exc).__name__, url)
        return None


def _root_text(root) -> str:
    """Bir kökten metin bloklarını toplayıp normalize eder."""
    blocks = []
    for el in root.find_all(_TEXT_TAGS):
        text = el.get_text(" ", strip=True)
        if len(text) > 1:
            blocks.append(text)
    return re.sub(r"\s+", " ", " ".join(blocks)).strip()


def clean_text(html: str) -> str:
    """nav/footer/aside/header/script/style'ı siler; içerik kökü adayları
    (_CONTENT_SELECTORS) + <body> arasından EN ÇOK metin üreteni seçer.

    Eski select_one("a, b, c") virgüllü grupta belge sırasındaki ilk eşleşeni
    döndürüyordu; erken bir küçük .content/.container'a kilitlenince gövde
    kaçıyordu (134/65 karakterlik boş çıktılar). En-çok-metin heuristiği bunu
    çözer: gerçek gövde adayların en büyüğüdür."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()

    roots = []
    for sel in _CONTENT_SELECTORS:
        roots.extend(soup.select(sel))
    if soup.body:
        roots.append(soup.body)
    if not roots:
        roots = [soup]

    best = ""
    for root in roots:
        text = _root_text(root)
        if len(text) > len(best):
            best = text
    return best


def page_title(html: str, fallback: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return fallback


def find_subpages(html: str, base_url: str) -> "list[tuple[str, str]]":
    """Aynı domain'de, URL YOLU yönetim/kadro/iletişim kalıbına uyan alt
    sayfa linklerini bulur (depth=1).

    Eşleşme link METNİNE göre DEĞİL, yalnız URL yoluna göre yapılır: başlığında
    'akademik/idari' geçen duyuru linkleri (ör. duyuru_detay.php) artık elenir.
    Ayrıca duyuru/haber/detay içeren yollar _SUBPAGE_DENY_RE ile kesin dışlanır."""
    soup = BeautifulSoup(html, "html.parser")
    base_dom = urlparse(base_url).netloc
    base_norm = base_url.rstrip("/")
    found, seen = [], set()
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"]).split("#")[0]
        if urlparse(full).netloc != base_dom:
            continue
        path = urlparse(full).path.lower()
        if not _SUBPAGE_PATH_RE.search(path) or _SUBPAGE_DENY_RE.search(path):
            continue
        if full.rstrip("/") == base_norm or full in seen:
            continue
        seen.add(full)
        found.append((a.get_text(" ", strip=True) or full, full))
        if len(found) >= MAX_SUBPAGES:
            break
    return found


def crawl_site(session: requests.Session, key: str, url: str) -> "str | None":
    """Bir siteyi depth=1 tarar, Markdown metni döndürür (yoksa None)."""
    log.info("Taranıyor [%s]: %s", key, url)
    html = fetch(session, url)
    if not html:
        return None
    title = page_title(html, key)
    main_text = clean_text(html)
    sections = [f"# {title}", f"Kaynak: {url}", "", main_text]

    subpages = find_subpages(html, url)
    log.info("  %d alt sayfa bulundu", len(subpages))
    for stitle, surl in subpages:
        time.sleep(SLEEP_SECONDS)  # nezaket: her istek arası bekleme
        log.info("    alt sayfa: %s", surl)
        shtml = fetch(session, surl)
        if not shtml:
            continue
        stext = clean_text(shtml)
        if stext:
            sections += ["", f"## {stitle}", f"Kaynak: {surl}", "", stext]
    return "\n".join(sections)


def trigger_ingest() -> None:
    """Tarama bitince offline_ingest.py'yi çalıştırır (inen md'ler Qdrant'a)."""
    log.info("=== offline_ingest.py tetikleniyor (Qdrant gömme) ===")
    script = Path(__file__).resolve().parent / "offline_ingest.py"
    subprocess.run([sys.executable, str(script)], check=False)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description="GAÜN otonom crawler (Faz 16)")
    ap.add_argument("--limit", type=int, default=0, help="ilk N siteyle sınırla (deneme)")
    ap.add_argument("--no-ingest", action="store_true", help="sonunda ingest tetikleme")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    items = list(TARGET_URLS.items())
    if args.limit:
        items = items[:args.limit]

    ok, fail = [], []
    total = len(items)
    for i, (key, url) in enumerate(items, 1):
        if i > 1:
            time.sleep(SLEEP_SECONDS)  # siteler arası bekleme
        log.info("[%d/%d] --------------------------------", i, total)
        try:
            md = crawl_site(session, key, url)
        except Exception as exc:  # hiçbir site tüm taramayı düşürmesin
            log.warning("  beklenmeyen hata [%s]: %s", key, exc)
            md = None
        if md and md.strip():
            path = OUT_DIR / f"scraped_{key}.md"
            path.write_text(md, encoding="utf-8")
            log.info("  ✓ KAYDEDİLDİ: %s (%d karakter)", path.name, len(md))
            ok.append(key)
        else:
            fail.append(key)

    log.info("==================================================")
    log.info("TARAMA BİTTİ: %d başarılı, %d atlandı/başarısız (toplam %d).",
             len(ok), len(fail), total)
    log.info("Başarılı: %s", ", ".join(ok) or "-")
    log.info("Atlandı : %s", ", ".join(fail) or "-")

    if ok and not args.no_ingest:
        trigger_ingest()
    log.info("Faz 16 tamamlandı.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
