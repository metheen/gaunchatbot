#!/usr/bin/env python3
"""GAUN Chatbot — 'regulations' Qdrant koleksiyonunu kurar (Faz 4).

Yönetmelik / duyuru / SSS gibi serbest metinler için bge-m3 boyutuna (1024)
uygun, Cosine distance kullanan yeni bir koleksiyon oluşturur.

İdempotent: koleksiyon varsa boyutunu doğrulayıp dokunmaz (--recreate ile
sıfırdan kurulur). Sırlar .env'den; koda gömülü sır yok.

  python setup_regulations_db.py
  python setup_regulations_db.py --recreate
"""

import argparse
import logging
import os

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

log = logging.getLogger("setup_regulations_db")

DEFAULT_COLLECTION = "regulations"
VECTOR_SIZE = 1024  # bge-m3


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    load_dotenv()

    ap = argparse.ArgumentParser(description="regulations koleksiyonunu kur")
    ap.add_argument("--recreate", action="store_true", help="Varsa silip yeniden kur")
    args = ap.parse_args()

    collection = os.getenv("REGULATIONS_COLLECTION", DEFAULT_COLLECTION)
    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
    )

    exists = client.collection_exists(collection)
    if exists and not args.recreate:
        size = client.get_collection(collection).config.params.vectors.size
        if size != VECTOR_SIZE:
            log.warning("'%s' zaten var ama boyut %d (beklenen %d) — --recreate ile düzelt.",
                        collection, size, VECTOR_SIZE)
        else:
            log.info("'%s' zaten var (dim=%d, cosine) — dokunulmadı.", collection, size)
        return 0

    if exists:
        client.delete_collection(collection)
        log.info("Mevcut '%s' silindi (--recreate).", collection)

    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    log.info("Koleksiyon oluşturuldu: %s (dim=%d, cosine).", collection, VECTOR_SIZE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
