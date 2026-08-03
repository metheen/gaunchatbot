#!/usr/bin/env python3
"""GAUN Chatbot — JS-render destekli Otonom Crawler (Fable 5 / Autonomy).

Statik crawler (gaun_crawler.py) fakülte/GUZEM gibi JavaScript ile render edilen
sayfalarda boş kabuk çekiyordu. Bu betik Playwright (headless Chromium) ile
sayfayı TAM render edip (DOM oturana kadar bekler) BeautifulSoup ile temizler,
'dekanlık/yönetim/idari kadro' gibi alt sayfaları da izler ve
offline_data/scraped_<birim>.md olarak yazar. Sonunda offline_ingest.py'yi
tetikleyerek Qdrant havuzunu günceller.

Temizleme/alt-sayfa/başlık mantığı gaun_crawler.py'den yeniden kullanılır (DRY);
tek fark: requests.get yerine Playwright ile render. Dosya isimlendirmesi
resolve_unit_link (rag_pipeline) birim anahtarlarıyla uyumludur.

Ön koşul (Ubuntu):
  pip install playwright && playwright install --with-deps chromium

  ./.venv/bin/python playwright_crawler.py
  ./.venv/bin/python playwright_crawler.py --limit 3        # deneme
  ./.venv/bin/python playwright_crawler.py --no-ingest      # ingest tetikleme
"""

import argparse
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from playwright.async_api import async_playwright

# Temiz metin / alt sayfa / başlık / hedef listesi tek kaynaktan (DRY):
from gaun_crawler import TARGET_URLS, clean_text, find_subpages, page_title

log = logging.getLogger("playwright_crawler")

USER_AGENT = "GaunAI-Bot/1.0 (Bilgi Islem Daire Bsk. Projesi)"
SLEEP_SECONDS = 2          # kurumsal nezaket: her sayfa arası bekleme
NAV_TIMEOUT_MS = 30000     # sayfa yükleme üst sınırı
RENDER_WAIT_MS = 1500      # goto sonrası geç-render JS için ek bekleme
# Boş/SPA sayfalar "# Başlık\nKaynak: url" gibi ~60-140 baytlık çöp stub üretir.
# Bu eşiğin altındaki çıktı kaydedilmez ve ingest'e girmez (korpus kirlenmesin).
MIN_CONTENT_CHARS = 250
OUT_DIR = Path(__file__).resolve().parent / "offline_data"


async def render_html(page, url: str) -> "str | None":
    """Sayfayı render eder; DOM oturana kadar bekler. Hata olursa None (çökmüz)."""
    for wait_until in ("networkidle", "domcontentloaded"):
        try:
            await page.goto(url, wait_until=wait_until, timeout=NAV_TIMEOUT_MS)
            await page.wait_for_timeout(RENDER_WAIT_MS)  # geç JS render'ı için
            return await page.content()
        except Exception as exc:  # timeout / navigasyon hatası -> bir sonraki stratejiyi dene
            log.warning("    render denemesi (%s) başarısız: %s", wait_until, type(exc).__name__)
    return None


async def crawl_site(page, key: str, url: str) -> "str | None":
    """Bir siteyi render ederek depth=1 tarar; Markdown döndürür (yoksa None)."""
    log.info("Render ediliyor [%s]: %s", key, url)
    html = await render_html(page, url)
    if not html:
        return None
    title = page_title(html, key)
    main_text = clean_text(html)
    sections = [f"# {title}", f"Kaynak: {url}", "", main_text]

    subpages = find_subpages(html, url)   # yönetim/dekanlık/idari/iletişim linkleri
    log.info("  %d alt sayfa bulundu", len(subpages))
    for stitle, surl in subpages:
        await asyncio.sleep(SLEEP_SECONDS)   # nezaket
        log.info("    alt sayfa render: %s", surl)
        shtml = await render_html(page, surl)
        if not shtml:
            continue
        stext = clean_text(shtml)
        if stext:
            sections += ["", f"## {stitle}", f"Kaynak: {surl}", "", stext]
    return "\n".join(sections)


def trigger_ingest() -> None:
    """Tarama bitince offline_ingest.py'yi çalıştırır (inen md'ler -> Qdrant)."""
    log.info("=== offline_ingest.py tetikleniyor (Qdrant gömme) ===")
    script = Path(__file__).resolve().parent / "offline_ingest.py"
    subprocess.run([sys.executable, str(script)], check=False)


async def run(items, do_ingest: bool) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    ok, fail = [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            ignore_https_errors=True,   # edu/gov TR sertifikaları
        )
        page = await context.new_page()
        total = len(items)
        for i, (key, url) in enumerate(items, 1):
            if i > 1:
                await asyncio.sleep(SLEEP_SECONDS)
            log.info("[%d/%d] --------------------------------", i, total)
            try:
                md = await crawl_site(page, key, url)
            except Exception as exc:  # hiçbir site tüm taramayı düşürmesin
                log.warning("  beklenmeyen hata [%s]: %s", key, exc)
                md = None
            n = len(md.strip()) if md else 0
            if n >= MIN_CONTENT_CHARS:
                path = OUT_DIR / f"scraped_{key}.md"
                path.write_text(md, encoding="utf-8")
                log.info("  ✓ KAYDEDİLDİ: %s (%d karakter)", path.name, len(md))
                ok.append(key)
            else:
                log.info("  ⤫ atlandı [%s]: içerik yetersiz (%d < %d) — boş/SPA sayfa",
                         key, n, MIN_CONTENT_CHARS)
                fail.append(key)
        await browser.close()

    log.info("==================================================")
    log.info("TARAMA BİTTİ: %d başarılı, %d atlandı (toplam %d).", len(ok), len(fail), len(items))
    log.info("Başarılı: %s", ", ".join(ok) or "-")
    log.info("Atlandı : %s", ", ".join(fail) or "-")
    if ok and do_ingest:
        trigger_ingest()
    log.info("Autonomy (JS-render hasadı) tamamlandı.")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description="GAÜN Playwright JS-render crawler")
    ap.add_argument("--limit", type=int, default=0, help="ilk N siteyle sınırla (deneme)")
    ap.add_argument("--no-ingest", action="store_true", help="sonunda ingest tetikleme")
    args = ap.parse_args()

    items = list(TARGET_URLS.items())
    if args.limit:
        items = items[:args.limit]

    asyncio.run(run(items, do_ingest=not args.no_ingest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
