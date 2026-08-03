#!/usr/bin/env python3
"""GAUN Chatbot — Yönetmelik çekici (İSKELET, Faz 4).

Şimdilik gerçek crawl yok: hard-coded örnek bir yönetmelik metnini bge-m3 ile
vektörleştirip 'regulations' koleksiyonuna upsert eden temel akışı barındırır.
İleride (roadmap) crawler_rehber.py deseninde gerçek yönetmelik/duyuru/PDF
kaynaklarından beslenecek; bu dosya o hattın çekirdeğidir.

İdempotent: chunk point ID'leri kaynak+sıra hash'inden deterministik (aynı
metin iki kez ingest edilince çoğalmaz). Koleksiyonu önce setup_regulations_db
ile oluştur. Sırlar .env'den.

  python crawler_yonetmelik.py           # örnek metni ingest et (upsert)
"""

import logging
import os

import ollama
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

from rag_pipeline import point_id_for, structural_chunk_text

log = logging.getLogger("crawler_yonetmelik")

DEFAULT_COLLECTION = "regulations"
DEFAULT_EMBED_MODEL = "bge-m3"

# --- Örnek (hard-coded) yönetmelik metni -----------------------------------
# Kaynak temsili; gerçek metin GAÜN mevzuat sayfasından çekilecek (roadmap).
ORNEK_KAYNAK = "GAÜN Ön Lisans ve Lisans Eğitim-Öğretim Yönetmeliği"
ORNEK_METIN = """\
GAÜN Ön Lisans ve Lisans Eğitim-Öğretim Yönetmeliği — Yatay Geçiş Şartları

Kurum içi ve kurumlar arası yatay geçişler, Yükseköğretim Kurumlarında Ön
Lisans ve Lisans Düzeyindeki Programlar Arasında Geçiş, Çift Anadal, Yan Dal
ile Kurumlar Arası Kredi Transferi Yapılması Esaslarına İlişkin Yönetmelik
hükümlerine göre yürütülür.

Kurumlar arası yatay geçiş için öğrencinin, kayıtlı olduğu programda bitirmiş
olduğu dönemlere ait genel not ortalamasının en az 100 üzerinden 60 (4 üzerinden
2,29) olması gerekir. Genel not ortalaması yeterli olmayan ancak merkezi
yerleştirme puanı, geçmek istediği diploma programının taban puanına eşit veya
yüksek olan adaylar da başvuru yapabilir.

Yatay geçiş başvuruları yalnızca ilan edilen kontenjanlar dahilinde ve akademik
takvimde belirtilen başvuru tarihleri içinde yapılır. Başvurular değerlendirilirken
öğrencinin genel not ortalaması ve tamamladığı ders sayısı dikkate alınır.

Birinci sınıfta ve son sınıfta yatay geçiş yapılamaz. Öğrencinin disiplin cezası
almamış olması şarttır. Eksik belgeyle veya süre dışında yapılan başvurular
değerlendirmeye alınmaz.
"""


def embed(text: str, model: str) -> "list[float]":
    return ollama.embeddings(model=model, prompt=text)["embedding"]


def ingest_document(client: QdrantClient, collection: str, embed_model: str,
                    source: str, title: str, text: str) -> int:
    """Bir metni parçalayıp embed edip 'regulations' koleksiyonuna upsert eder."""
    chunks = structural_chunk_text(text)
    points = []
    for i, chunk in enumerate(chunks):
        points.append(PointStruct(
            id=point_id_for(f"{source}#{i}"),
            vector=embed(chunk, embed_model),
            payload={
                "document": chunk,
                "source": source,
                "title": title,
                "chunk_index": i,
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

    n = ingest_document(client, collection, embed_model, ORNEK_KAYNAK, ORNEK_KAYNAK, ORNEK_METIN)
    toplam = client.count(collection_name=collection).count
    log.info("Ingest tamam: %d chunk yazıldı ('%s'). Koleksiyon toplam: %d.",
             n, collection, toplam)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
