#!/usr/bin/env python3
"""GAUN Chatbot — Mevzuat/SSS web scraper (Faz 5).

Gerçek web scraper: üniversitenin resmi sayfalarından yönetmelik/SSS/rehber
gövde metinlerini çeker, temizler, chunk'lar, bge-m3 ile gömer ve Qdrant
'regulations' koleksiyonuna zengin payload'la (source_url, title, chunk_index)
yazar. Böylece LLM cevabında kaynağı gösterebilir.

Mimari:
  * requests + BeautifulSoup. header/footer/nav/script çöpe atılır; asıl gövde
    (main/article/#content/.content, yoksa body) içinden p/li/table/başlık
    metinleri düz metne çevrilir.
  * Chunking: rag_pipeline.structural_chunk_text (BÖLÜM/MADDE sınırları korunur).
  * Idempotency: point_id_for(f"{url}#{i}") — aynı sayfa tekrar taranınca
    chunk'lar çoğalmaz, üzerine yazılır.
  * Kibar tarama: sayfalar arası time.sleep(2), dürüst User-Agent.

Sırlar .env'den. Not: edu/gov TR sertifikaları sık sık zincir sorunlu olduğu
için verify=False (kampus içi araç); üretimde kurum CA paketi pinlenmeli.

  python crawler_mevzuat.py
"""

import logging
import os
import re
import time

import fitz  # PyMuPDF — PDF metin çıkarımı
import ollama
import requests
import urllib3
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from rag_pipeline import point_id_for, structural_chunk_text

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger("crawler_mevzuat")

COLLECTION = os.getenv("REGULATIONS_COLLECTION", "regulations")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")
VECTOR_SIZE = 1024  # bge-m3
USER_AGENT = os.getenv("CRAWLER_USER_AGENT", "GaunChatbotCrawler/0.1 (BIDB)")
SLEEP_SECONDS = 2

# Başlangıç (seed) sayfaları — canlı sitede doğrulandı (2026-07-09).
SEED_PAGES = [
    {
        "url": "https://www.resmigazete.gov.tr/eskiler/2017/08/20170821-5.htm",
        "title": "GAÜN Önlisans ve Lisans Eğitim-Öğretim Yönetmeliği",
    },
    {
        "url": "https://oidb.gaziantep.edu.tr/pages.php?url=sikca-sorulan-sorular-sss-50",
        "title": "Öğrenci İşleri Daire Başkanlığı — Sıkça Sorulan Sorular",
    },
    {
        "url": "https://oidb.gaziantep.edu.tr/pages.php?url=universitemiz-yonetmelikleri-10",
        "title": "Üniversitemiz Yönetmelikleri",
    },
    {
        # PDF örneği — Content-Type application/pdf; fitz ile çıkarılır.
        "url": "https://oidb.gantep.edu.tr/upload/files/YATAY%20GECIS%20ESASLARINA%20ILISKIN%20YONERGE.pdf",
        "title": "GAÜN Yatay Geçiş Esaslarına İlişkin Yönerge (PDF)",
    },
    {
        "url": "https://eduroam.gaziantep.edu.tr/",
        "title": "Eduroam Bağlantı Rehberi",
    },
]

# Gövde metnini taşıyan olası container'lar (öncelik sırasıyla).
_CONTENT_SELECTORS = "main, article, #content, .content, .page-content, .icerik"
# Çöpe atılacak yapısal etiketler.
_DROP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "form", "aside")
# Gövde metni alınacak etiketler.
_TEXT_TAGS = ("h1", "h2", "h3", "h4", "p", "li", "td", "th")


def extract_main_text(html: str) -> str:
    """header/footer/nav'ı atıp asıl gövde metnini temiz düz metne çevirir."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()

    container = soup.select_one(_CONTENT_SELECTORS)
    # Bilinen container yoksa ya da çok kısaysa (sadece link listesi vb.) body'e düş.
    if container is None or len(container.get_text(strip=True)) < 200:
        container = soup.body or soup

    blocks = []
    for el in container.find_all(_TEXT_TAGS):
        text = el.get_text(" ", strip=True)
        if len(text) > 1:
            blocks.append(text)
    return " ".join(blocks)


def fetch(session: requests.Session, url: str) -> "requests.Response | None":
    # Gov/edu sunucuları yavaş olabilir: kısa connect, uzun read timeout + retry.
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=(15, 90), verify=False)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            log.warning("  deneme %d başarısız (%s): %s", attempt + 1, url, type(exc).__name__)
            time.sleep(2)
    return None


def extract_pdf_text(data: bytes) -> str:
    """PDF baytlarını bellekte açıp tüm sayfaların metnini birleştirir (fitz)."""
    doc = fitz.Document(stream=data, filetype="pdf")
    try:
        parts = [page.get_text() for page in doc]
    finally:
        doc.close()
    return re.sub(r"\s+", " ", "\n".join(parts)).strip()


def is_pdf(resp: requests.Response, url: str) -> bool:
    """Content-Type application/pdf ise ya da URL .pdf ile bitiyorsa PDF'tir."""
    ctype = resp.headers.get("Content-Type", "").lower()
    return "application/pdf" in ctype or url.lower().split("?")[0].endswith(".pdf")


def extract_document_text(resp: requests.Response, url: str) -> str:
    """PDF ise fitz ile, değilse HTML gövdesinden metin çıkarır."""
    if is_pdf(resp, url):
        return extract_pdf_text(resp.content)
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return extract_main_text(resp.text)


def embed(text: str) -> "list[float]":
    return ollama.embeddings(model=EMBED_MODEL, prompt=text)["embedding"]


def ensure_collection(client: QdrantClient) -> None:
    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        log.info("Koleksiyon oluşturuldu: %s (dim=%d, cosine)", COLLECTION, VECTOR_SIZE)


def dedupe(chunks: "list[str]") -> "list[str]":
    """Sayfa içi birebir tekrar eden chunk'ları atar (bazı resmi sayfalar
    metni ekran/yazdırma kopyası olarak 2-3 kez içeriyor)."""
    seen, unique = set(), []
    for ch in chunks:
        if ch not in seen:
            seen.add(ch)
            unique.append(ch)
    return unique


def ingest_text(client: QdrantClient, url: str, title: str, text: str) -> int:
    """Çıkarılmış metni chunk'layıp zengin payload'la regulations'a upsert eder."""
    chunks = dedupe(structural_chunk_text(text))
    if not chunks:
        log.warning("  içerik çıkarılamadı: %s", url)
        return 0
    points = []
    for i, chunk in enumerate(chunks):
        points.append(PointStruct(
            id=point_id_for(f"{url}#{i}"),
            vector=embed(chunk),
            payload={
                "document": chunk,
                "source_url": url,   # LLM 'şu linkten aldım' diyebilsin
                "title": title,
                "chunk_index": i,
            },
        ))
    client.upsert(collection_name=COLLECTION, points=points)
    return len(points)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    load_dotenv()

    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
    )
    ensure_collection(client)

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    toplam_chunk = 0
    for i, page in enumerate(SEED_PAGES):
        if i:
            time.sleep(SLEEP_SECONDS)  # kibar tarama
        url, title = page["url"], page["title"]
        log.info("Çekiliyor: %s", url)
        resp = fetch(session, url)
        if resp is None:
            log.error("  ATLANDI (indirilemedi): %s", url)
            continue
        tur = "PDF" if is_pdf(resp, url) else "HTML"
        text = extract_document_text(resp, url)
        n = ingest_text(client, url, title, text)
        toplam_chunk += n
        log.info("  '%s' [%s] -> %d chunk yazıldı.", title, tur, n)

    say = client.count(collection_name=COLLECTION).count
    log.info("Bitti: %d chunk işlendi. '%s' koleksiyonu toplam: %d.",
             toplam_chunk, COLLECTION, say)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
