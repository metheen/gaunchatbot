#!/usr/bin/env python3
"""GAUN Chatbot — Personel verisini Qdrant'a gömen script (Faz 2).

Akış: MariaDB `staff` -> Türkçe metin -> bge-m3 (1024) -> Qdrant `staff`
koleksiyonu (recreate).

NOT: Talimatta 'personel' tablosu geçiyor ancak şemada böyle bir tablo YOK;
gerçek personel tablosu `staff`'tır (crawler_rehber.py --write ile doldurulur).
Bu script `staff`'ı kullanır. Metin/payload üretimi mevcut ve test edilmiş
rag_pipeline yardımcılarından gelir (yeniden yazılmadı).

Sırlar .env'den (MARIADB_USER/PASSWORD/DATABASE); koda gömülü sır yoktur.
"""

import logging
import os

import mysql.connector
import ollama
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from rag_pipeline import point_id_for, staff_row_to_document, staff_row_to_payload

log = logging.getLogger("embed_data")

# Faz 3: Türkçe başarısı için bge-m3 (1024 boyut). Koleksiyon boyutu ilk
# vektörden dinamik alınır, yani model değişse de kod değişmez.
DEFAULT_COLLECTION = "staff"
DEFAULT_EMBED_MODEL = "bge-m3"

# staff + bağlı birim (üst birim dahil) — anlamlı metin için gerekli alanlar.
STAFF_QUERY = """
SELECT s.external_key, s.full_name, s.academic_title, s.role_title,
       s.phone_internal, s.email, s.source_url,
       d.name AS dept_name,
       p.name AS parent_name
FROM staff s
LEFT JOIN departments d ON s.department_id = d.id
LEFT JOIN departments p ON d.parent_id     = p.id
WHERE s.is_active = 1
"""


def fetch_staff() -> "list[dict]":
    """localhost MariaDB'den aktif personeli çeker (.env kimlik bilgileriyle)."""
    password = os.getenv("MARIADB_PASSWORD")
    if not password:
        raise SystemExit("MARIADB_PASSWORD tanımlı değil — .env dosyanı kontrol et.")
    conn = mysql.connector.connect(
        host=os.getenv("MARIADB_HOST", "127.0.0.1"),
        port=int(os.getenv("MARIADB_PORT", "3306")),
        user=os.getenv("MARIADB_USER", "gaun_app"),
        password=password,
        database=os.getenv("MARIADB_DATABASE", "gaun_assistant"),
        charset="utf8mb4",
    )
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(STAFF_QUERY)
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def embed(text: str, model: str) -> "list[float]":
    """bge-m3 ile 1024 boyutlu vektör."""
    return ollama.embeddings(model=model, prompt=text)["embedding"]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    load_dotenv()

    collection = os.getenv("QDRANT_COLLECTION", DEFAULT_COLLECTION)
    embed_model = os.getenv("OLLAMA_EMBED_MODEL", DEFAULT_EMBED_MODEL)
    rows = fetch_staff()
    if not rows:
        log.error("staff tablosunda aktif kayıt yok — önce crawler_rehber.py --write çalıştır.")
        return 2
    log.info("MariaDB'den %d personel kaydı okundu.", len(rows))

    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
    )

    # İlk vektör koleksiyon boyutunu belirler; recreate ile sıfırdan kurulur.
    first_doc = staff_row_to_document(rows[0])
    first_vec = embed(first_doc, embed_model)
    client.recreate_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=len(first_vec), distance=Distance.COSINE),
    )
    log.info("Koleksiyon (yeniden) oluşturuldu: %s (dim=%d, cosine)", collection, len(first_vec))

    points, batch = [], 100
    for i, row in enumerate(rows):
        payload = staff_row_to_payload(row)
        vec = first_vec if i == 0 else embed(payload["document"], embed_model)
        points.append(PointStruct(
            id=point_id_for(payload["external_key"]), vector=vec, payload=payload))
        if len(points) >= batch:
            client.upsert(collection_name=collection, points=points)
            log.info("  %d/%d gömüldü...", i + 1, len(rows))
            points = []
    if points:
        client.upsert(collection_name=collection, points=points)

    toplam = client.count(collection_name=collection).count
    log.info("Bitti: %d kayıt '%s' koleksiyonuna yazıldı (Qdrant sayımı: %d).",
             len(rows), collection, toplam)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
