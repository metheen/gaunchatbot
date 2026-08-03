#!/usr/bin/env python3
"""GAUN Chatbot — RAG Teşhis Betiği (debug_rag.py).

Amaç: bir sorunun cevabı yanlışsa hatanın VEKTÖR VERİTABANINDAN (Qdrant —
ilgili chunk hiç yok/alakasız) mı yoksa LLM'DEN (chunk var ama model yanlış
üretiyor) mi kaynaklandığını izole etmek.

Yöntem: sorguyu embedding modelinden geçirip 'regulations' koleksiyonunda top_k
arama yapar ve dönen chunk'ların skor + kaynak + metnini gösterir. Böylece
"retrieval doğru chunk'ı getiriyor mu?" sorusu gözle görülür.

ÖNEMLİ: Embedding modeli, verinin gömüldüğü modelle AYNI olmalı (bu projede
bge-m3, 1024 boyut). all-MiniLM-L6-v2 (384 boyut) kullanılırsa Qdrant boyut
uyuşmazlığıyla hata verir.

  ./.venv/bin/python debug_rag.py
  ./.venv/bin/python debug_rag.py "Başka bir soru?"
"""

import os
import sys

import ollama
from dotenv import load_dotenv
from qdrant_client import QdrantClient

# ANSI renkler
BOLD = "\033[1m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
CYAN = "\033[0;36m"
RED = "\033[0;31m"
DIM = "\033[2m"
NC = "\033[0m"


def score_color(score: float) -> str:
    if score >= 0.60:
        return GREEN
    if score >= 0.45:
        return YELLOW
    return RED


def main() -> int:
    load_dotenv(".env")
    collection = os.getenv("REGULATIONS_COLLECTION", "regulations")
    embed_model = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")
    query = sys.argv[1] if len(sys.argv) > 1 else "Eğitim fakültesi dekanı kimdir?"
    top_k = 3

    print(f"{BOLD}=== GaunAI RAG Teşhis ==={NC}")
    print(f"  Koleksiyon : {CYAN}{collection}{NC}")
    print(f"  Embedding  : {CYAN}{embed_model}{NC}  {DIM}(koleksiyonla aynı olmalı){NC}")
    print(f"  Sorgu      : {CYAN}{query}{NC}")
    print(f"  top_k      : {top_k}\n")

    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
    )

    if not client.collection_exists(collection):
        print(f"{RED}HATA: '{collection}' koleksiyonu yok. Önce ingest et.{NC}")
        return 2

    dim = client.get_collection(collection).config.params.vectors.size
    vec = ollama.embeddings(model=embed_model, prompt=query)["embedding"]
    if len(vec) != dim:
        print(f"{RED}HATA: model boyutu ({len(vec)}) koleksiyon boyutuyla ({dim}) "
              f"uyuşmuyor. Doğru embedding modelini kullan (bge-m3).{NC}")
        return 2

    hits = client.search(collection_name=collection, query_vector=vec, limit=top_k)
    if not hits:
        print(f"{RED}Hiç sonuç yok — koleksiyon boş olabilir.{NC}")
        return 1

    dekan_gorundu = False
    for i, h in enumerate(hits, 1):
        p = h.payload or {}
        doc = (p.get("document") or "").strip()
        source = p.get("source_url") or p.get("source") or p.get("source_file") or "?"
        title = p.get("title") or "?"
        sc = score_color(h.score)
        print(f"{BOLD}[{i}] {sc}score={h.score:.3f}{NC}")
        print(f"    {DIM}kaynak :{NC} {source}")
        print(f"    {DIM}başlık :{NC} {title}")
        print(f"    {DIM}metin  :{NC} {doc[:300]}{'...' if len(doc) > 300 else ''}\n")
        if "dekan" in doc.lower():
            dekan_gorundu = True

    # --- Teşhis yorumu: sorun Qdrant'ta mı LLM'de mi? ---
    print(f"{BOLD}--- Teşhis ---{NC}")
    best = hits[0].score
    if dekan_gorundu and best >= 0.5:
        print(f"{GREEN}✔ Retrieval ilgili bilgiyi getiriyor (chunk 'dekan' içeriyor).{NC}")
        print(f"  → Cevap hâlâ yanlışsa sorun {BOLD}LLM üretiminde{NC} (prompt/model).")
    else:
        print(f"{YELLOW}✖ Retrieval doğru bilgiyi GETİRMİYOR "
              f"(en iyi skor {best:.3f}, 'dekan' içeren chunk yok).{NC}")
        print(f"  → Sorun {BOLD}VERİDE/Qdrant'ta{NC}: bu bilgi hiç gömülmemiş. "
              "Crawler fakülte sayfalarından dekan bilgisini hasat edememiş "
              "(JS-render sınırı). Çözüm: doğru veriyi ingest et, LLM'i değil.")

    # --- Cache hatırlatması ---
    print(f"\n{YELLOW}⚠ HATIRLATMA:{NC} Veriyi güncelledikten sonra eski/yanlış "
          "cache'lenmiş cevapları geçersiz kılmak için "
          f"{BOLD}bot.py içindeki ANSWER_CACHE_PREFIX değerini 'ans:v6:' yap.{NC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
