#!/usr/bin/env python3
"""GAUN Chatbot — çevrimdışı doküman ingest hattı (Faz 7).

offline_data/ altındaki .txt, .md ve .pdf dosyalarını okur, yapısal chunking
ile böler, bge-m3 ile embed eder ve Qdrant 'regulations' koleksiyonuna upsert
eder. DNS/intranet arkasında kalan SKS, Eduroam vb. yerel bilgi dosyaları için
tasarlanmıştır.

  python offline_ingest.py
"""

import logging
import os
from pathlib import Path

import ollama
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from rag_pipeline import point_id_for, structural_chunk_text

log = logging.getLogger("offline_ingest")

DATA_DIR = Path("offline_data")
SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}
DEFAULT_COLLECTION = "regulations"
DEFAULT_EMBED_MODEL = "bge-m3"


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_pdf_file(path: Path) -> str:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise SystemExit("PDF ingest için PyMuPDF gerekir: .venv/bin/python -m pip install PyMuPDF") from exc

    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc).strip()
    finally:
        doc.close()


def read_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return read_text_file(path)
    if suffix == ".pdf":
        return read_pdf_file(path)
    raise ValueError(f"Desteklenmeyen dosya türü: {path}")


def embed(text: str, model: str) -> "list[float]":
    return ollama.embeddings(model=model, prompt=text)["embedding"]


def iter_documents(data_dir: Path = DATA_DIR) -> "list[Path]":
    if not data_dir.exists():
        return []
    return sorted(
        path for path in data_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


# bge-m3 bağlam sınırını aşan (nokta/başlık içermeyen menü/gövde blob'ları)
# chunk'ları böler ve boşları eler; yoksa ollama 'input length exceeds context
# length' (500) verip ingest'i çökertir.
MAX_EMBED_CHARS = 4000


def _prepare_chunks(chunks: "list[str]") -> "list[str]":
    out = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        if len(chunk) <= MAX_EMBED_CHARS:
            out.append(chunk)
        else:
            for i in range(0, len(chunk), MAX_EMBED_CHARS):
                out.append(chunk[i:i + MAX_EMBED_CHARS])
    return out


def ingest_file(client: QdrantClient, collection: str, embed_model: str, path: Path) -> int:
    source_url = f"local://{path.name}"
    text = read_document(path)
    chunks = _prepare_chunks(structural_chunk_text(text))
    if not chunks:
        log.warning("Boş/okunamayan dosya atlandı: %s", path)
        return 0

    points = []
    for i, chunk in enumerate(chunks):
        points.append(PointStruct(
            id=point_id_for(f"{source_url}#{i}"),
            vector=embed(chunk, embed_model),
            payload={
                "document": chunk,
                "source_url": source_url,
                "title": path.stem,
                "chunk_index": i,
                "origin": "offline",
            },
        ))
    client.upsert(collection_name=collection, points=points)
    return len(points)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    load_dotenv()

    collection = os.getenv("REGULATIONS_COLLECTION", DEFAULT_COLLECTION)
    embed_model = os.getenv("OLLAMA_EMBED_MODEL", DEFAULT_EMBED_MODEL)
    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
    )
    if not client.collection_exists(collection):
        raise SystemExit(f"'{collection}' koleksiyonu yok — önce setup_regulations_db.py çalıştır.")

    files = iter_documents()
    if not files:
        log.warning("offline_data/ içinde .txt, .md veya .pdf dosyası yok.")
        return 0

    total = 0
    for path in files:
        n = ingest_file(client, collection, embed_model, path)
        total += n
        log.info("%s -> %d chunk upsert edildi.", path, n)

    count = client.count(collection_name=collection).count
    log.info("Offline ingest tamam: %d dosya, %d chunk. '%s' toplam: %d.",
             len(files), total, collection, count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
