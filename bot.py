#!/usr/bin/env python3
"""GAUN Chatbot — Hibrit Yönlendirici (Agentic Router) asistanı (Faz 4).

Akış:
  soru -> classify_intent
    * YAPISAL (numara/telefon/kişi arama): Qdrant ES GEÇİLİR. Doğrudan MariaDB'ye
      (departments/staff) SQL atılır, kesin veri çekilir. SQL'de kayıt yoksa
      yerel semantik hafızaya (Qdrant RAG) zincirleme düşüş yapılır.
    * SEMANTİK (karmaşık/anlamsal): bge-m3 ile vektörle -> Qdrant
      'regulations' koleksiyonundan RAG ile bağlam çekilir. (Personel
      semantic'ten çıkarıldı; isimle personel araması YALNIZ SQL'den gelir.)
  Elde edilen bağlam -> qwen2.5:7b-instruct (temperature=0, katı grounding)
  -> cevap.

Bu tasarım "Bilgi İşlem numarası kaç?" gibi yapısal sorularda uydurmayı
(örn. yanlış dahili) önler: cevap RAG tahmininden değil, DB'deki kesin
kayıttan gelir. Personel gömme embed_data.py, yönetmelik gömme
crawler_yonetmelik.py ile yapılır.

Tüm bağlantı/model ayarları .env'den; koda gömülü sır/host yok.
"""

import argparse
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from functools import lru_cache

import mysql.connector
import ollama
from diskcache import Cache
from dotenv import load_dotenv
from qdrant_client import QdrantClient

import analytics
from security_guard import INJECTION_REFUSAL, contains_injection_attempt
from intent_router import (
    PERSON_KEYWORDS,
    PERSON_QUERY_NOISE,
    PERSON_TITLE_KEYWORDS,
    SEMANTIC_COLLECTIONS,
    classify_intent,
    extract_content_tokens,
    live_topic,
    looks_like_person_name,
    match_department,
    needs_gaun_scope,
)
from live_fetcher import (
    LIVE_SOURCES,
    fetch_live_data,
    fetch_staff_from_directory,
    fetch_todays_menu_raw,
    fetch_unit_page,
    parse_todays_menu,
)
from web_search import build_grounding, web_search
from rag_pipeline import (
    CHITCHAT_SYSTEM_PROMPT,
    CONDENSE_SYSTEM_PROMPT,
    DEFAULT_UNIT_URL,
    SYSTEM_PROMPT,
    LEADER_ROLE_WORDS,
    UNSAFE_REQUEST_REFUSAL,
    WEB_SEARCH_SYSTEM_PROMPT,
    append_map_link_if_needed,
    asks_for_unit_leader,
    build_chat_messages,
    match_norm,
    format_context,
    format_staff_answer,
    graceful_not_found,
    is_bilmiyorum,
    is_garbled,
    is_location_question,
    is_unsafe_request,
    looks_like_unit_query,
    normalize_turkish,
    now_turkey,
    resolve_unit_link,
    staff_not_found_message,
    staff_row_to_payload,
    turkish_date,
    unit_not_found_message,
)

log = logging.getLogger("bot")

_OLLAMA_CONFIGURED = False


def _configure_ollama_host() -> None:
    """`.env`'deki OLLAMA_URL'i gerçekten ETKİN kılar (idempotent).

    ollama-python modül-seviye istemcisini (ollama.chat/embeddings/embed) IMPORT
    ANINDA kurar ve host'u YALNIZ OLLAMA_HOST ortam değişkeninden okur. Bizim
    .env değerimiz (OLLAMA_URL) load_dotenv ile import'tan SONRA yüklendiği için
    ollama tarafından tamamen yok sayılıyordu: Ollama farklı bir hosta taşınıp
    OLLAMA_URL ayarlansa bile tüm çağrılar sessizce 127.0.0.1:11434'e giderdi
    (gizli başarısızlık — şu an localhost'ta tesadüfen doğru çalışıyordu). Burada
    yapılandırılan host ile modül istemcisi bir kez yeniden kurulur. Kütüphane
    şekli beklenmedikse (sürüm farkı) sessizce varsayılana düşer, akışı bozmaz."""
    global _OLLAMA_CONFIGURED
    if _OLLAMA_CONFIGURED:
        return
    _OLLAMA_CONFIGURED = True
    host = os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_HOST")
    if not host:
        return
    try:
        client = ollama.Client(host=host)
        ollama._client = client
        for _name in ("chat", "embeddings", "embed", "generate"):
            fn = getattr(client, _name, None)
            if fn is not None:
                setattr(ollama, _name, fn)
        log.info("Ollama host yapılandırıldı: %s", host)
    except Exception as exc:  # kütüphane iç yapısı değişmişse akış bozulmasın
        log.warning("OLLAMA_URL uygulanamadı (%s); varsayılan host kullanılacak.", exc)


# Kalıcı yerel önbellek: statik (yapısal/semantik) cevaplar tekrar üretilmesin.
# Canlı veri BURAYA YAZILMAZ (hep tazedir). Dizin .gitignore'da.
CACHE = Cache(os.path.join(os.path.dirname(__file__), "local_cache"))
STATIC_TTL = 7 * 24 * 3600  # statik cevap önbelleği: 1 hafta
ANSWER_CACHE_PREFIX = "ans:v31:"  # v31: BULGU-2 — teknik sorular ('bilgisayar' vs 'bilgi') artık personel dalına düşmez, is_person_query ile zincirleme düşüş

# staff + bağlı birim (üst birim dahil) ortak SELECT'i; WHERE çağrı yerinde eklenir.
_STAFF_BASE = """
SELECT s.external_key, s.full_name, s.academic_title, s.role_title,
       s.phone_internal, s.email, s.source_url,
       d.name AS dept_name, p.name AS parent_name
FROM staff s
LEFT JOIN departments d ON s.department_id = d.id
LEFT JOIN departments p ON d.parent_id     = p.id
WHERE s.is_active = 1
"""

# GAÜN KAPSAM KİLİDİ: True ise bot YALNIZ üniversiteyle ilgili sorulara cevap
# verir — üniversite-dışı (general_knowledge) sorular kibarca reddedilir ve tüm
# web aramaları GAÜN'e sabitlenir (alakasız/saçma genel cevap dönmesin). .env'de
# GAUN_ONLY_MODE=false ile genel-asistan moduna dönülür.
GAUN_ONLY_MODE = os.getenv("GAUN_ONLY_MODE", "true").strip().lower() in ("1", "true", "yes", "on", "evet")
GAUN_SCOPE_MESSAGE = (
    "Ben Gaziantep Üniversitesi (GAÜN) dijital asistanıyım ve yalnızca "
    "üniversiteyle ilgili konularda (dersler, kayıt, harç, mevzuat, kampüs, "
    "yemekhane, ulaşım, personel, birimler vb.) yardımcı olabilirim. Sorunuzu "
    "GAÜN bağlamında sorarsanız memnuniyetle yanıtlarım."
)


MAX_ROWS = 25  # isim aramasında bağlamı şişirmesin (LLM'e giden yol)
# Birim kadrosu (roster) DETERMİNİSTİK listelenir (format_staff_answer, LLM'e
# GİRMEZ) — o yüzden fakülte gibi büyük birimlerin TAM kadrosu için ayrı, yüksek
# sınır. Öğrenci "eğitim fakültesindeki akademisyenler" derken tek kişi değil
# tümünü bekler (2026-07-27 eksik-cevap bug'ı).
ROSTER_MAX_ROWS = 80


class Config:
    def __init__(self) -> None:
        self.mariadb = {
            "host": os.getenv("MARIADB_HOST", "127.0.0.1"),
            "port": int(os.getenv("MARIADB_PORT", "3306")),
            "user": os.getenv("MARIADB_USER", "gaun_app"),
            "password": os.getenv("MARIADB_PASSWORD", ""),
            "database": os.getenv("MARIADB_DATABASE", "gaun_assistant"),
        }
        self.qdrant_url = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY") or None
        # Qdrant bağlantı zaman aşımı (sn). Kırmızı-Takım BULGU S4 (2026-07-29):
        # zaman aşımsız QdrantClient, port/servis kapalıysa ~90 sn asılı kalıp
        # (varsayılan httpx timeout) API thread havuzunu tüketiyordu (DoS). Kısa
        # timeout ile DB kapalıysa ~3 sn'de hata fırlatır → zarif fallback.
        self.qdrant_timeout = float(os.getenv("QDRANT_TIMEOUT", "3.0"))
        self.collection = os.getenv("QDRANT_COLLECTION", "staff")
        self.embed_model = os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")
        self.llm_model = os.getenv("OLLAMA_LLM_MODEL", "qwen2.5:7b-instruct")
        self.top_k = int(os.getenv("RAG_TOP_K", "5"))
        # Alaka eşiği: bu skorun ALTINDAKİ retrieval isabetleri elenir (grounding
        # sertleştirme). 0 = kapalı (davranış değişmez, en yakın K getirilir) —
        # .env varsayılanı. >0 verilirse alakasız düşük-skorlu chunk'lar cevaba
        # hiç girmez (halüsinasyon kalkanlarıyla aynı ruh). Eskiden .env'de
        # tanımlıydı ama kod HİÇ okumuyordu (ölü knob); artık etkin.
        self.score_threshold = float(os.getenv("RAG_SCORE_THRESHOLD", "0") or "0")
        if not self.mariadb["password"]:
            raise SystemExit("MARIADB_PASSWORD tanımlı değil — .env dosyanı kontrol et.")


# ---------------------------------------------------------------------------
# MariaDB (yapısal yol)
# ---------------------------------------------------------------------------

def connect_db(cfg: Config):
    return mysql.connector.connect(**cfg.mariadb, charset="utf8mb4")


# ---------------------------------------------------------------------------
# Qdrant (semantik yol) — TEMBEL + zaman aşımlı istemci
# ---------------------------------------------------------------------------

# DB/altyapı kesintisinde kullanıcıya dönen güvenli mesaj (iç ayrıntı SIZMAZ).
DB_UNAVAILABLE_FALLBACK = (
    "Şu anda bilgi tabanına geçici olarak ulaşılamıyor. Lütfen birkaç dakika "
    "sonra tekrar deneyin."
)


@lru_cache(maxsize=8)
def _build_qdrant_client(url: str, api_key: "str | None", timeout: float) -> QdrantClient:
    """(url, api_key, timeout) başına tek istemci — httpx bağlantı havuzu paylaşılır."""
    return QdrantClient(url=url, api_key=api_key, timeout=timeout)


def get_qdrant_client(cfg: Config) -> QdrantClient:
    """Tembel + önbellekli Qdrant istemcisi (S4 çözümü).

    Modül yüklenirken DEĞİL, RAG'e gerçekten ihtiyaç duyulduğunda oluşturulur;
    kısa `timeout` sayesinde servis kapalıysa ~3 sn'de hata fırlatır (90 sn
    boyunca thread bloklamaz). Hata durumunda lru_cache boşta kalır (istisnalar
    önbelleğe alınmaz), böylelikle DB geri gelince yeni deneme yapılır."""
    return _build_qdrant_client(cfg.qdrant_url, cfg.qdrant_api_key, cfg.qdrant_timeout)


def fetch_departments(conn) -> "list[dict]":
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, name FROM departments WHERE is_active = 1")
    rows = cur.fetchall()
    cur.close()
    return rows


def fetch_department_staff(conn, dept_id: int,
                           limit: int = ROSTER_MAX_ROWS) -> "list[dict]":
    """Birimin kendi personeli + alt birimlerinin personeli.

    Üst birim → alt birim (bölüm) → ad sırasıyla döner ki öğrenci fakülte
    kadrosunu bölümlere göre düzenli görsün. Kesilme tespiti için çağıran taraf
    'limit'ten bir fazla satır alır (bkz. resolve_roster)."""
    cur = conn.cursor(dictionary=True)
    cur.execute(
        _STAFF_BASE + " AND (s.department_id = %s OR d.parent_id = %s)"
        " ORDER BY p.name, d.name, s.full_name LIMIT %s",
        (dept_id, dept_id, limit + 1))
    rows = cur.fetchall()
    cur.close()
    return rows


def search_staff_by_tokens(conn, tokens: "list[str]") -> "list[dict]":
    """Tüm token'ları search_name içinde (AND) barındıran personeli getirir."""
    if not tokens:
        return []
    clause = " AND ".join(["LOWER(s.search_name) LIKE LOWER(%s)"] * len(tokens))
    params = [f"%{t}%" for t in tokens] + [MAX_ROWS]
    sql = _STAFF_BASE + f" AND ({clause}) LIMIT %s"
    log.info("Uygulanan SQL Sorgusu: %s | params=%s", " ".join(sql.split()), params)
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def resolve_roster(conn, question: str) -> "tuple[list[dict], str, str]":
    """BİRİM KADROSU sorgusunu DB'den çözer (fakülte/bölüm personel dökümü).

    HİBRİT mimaride DB'nin KORUNAN tek asli görevi: canlı rehber yalnız İSİM
    indeksler, birim/fakülte araması YAPAMAZ (ampirik olarak doğrulandı —
    kelime='egitim' → 0 kayıt), o yüzden "eğitim fakültesindeki akademisyenler"
    gibi kadro dökümü DB'den gelir (bkz. hafıza: birim-eslesme-tur-ve-kampus,
    rag-hibrit-mimari-gerekli).

    Yönetici (dekan/rektör) TEK-kişi sorusu ROSTER değildir → boş döner;
    çağıran semantik/curated zincire bırakır (kadro dökmek yanlış olurdu).

    (rows, header, log_aciklamasi) döner; eşleşme yoksa ([], "", <neden>)."""
    if asks_for_unit_leader(question):
        return [], "", "yönetici sorusu — kadro dökülmez, semantik/curated zincire bırakıldı"
    dept = match_department(question, fetch_departments(conn))
    if not dept:
        return [], "", "birim eşleşmedi"
    rows = fetch_department_staff(conn, dept["id"])
    if not rows:
        return [], "", f"birim eşleşti ama aktif personel yok: {dept['name']}"
    truncated = len(rows) > ROSTER_MAX_ROWS
    rows = rows[:ROSTER_MAX_ROWS]
    header = f"{dept['name']} personeli ve dahilileri:"
    if truncated:
        header = (f"{dept['name']} kadrosundan ilk {ROSTER_MAX_ROWS} "
                  "kişi (tamamı için birim sayfasına bakınız):")
    return (rows, header,
            f"birim eşleşti: {dept['name']} ({len(rows)} personel"
            f"{', kesildi' if truncated else ''})")


def person_name_tokens(question: str) -> "list[str]":
    """İsimle kişi araması için token'ları çıkarır ve kişi-sorgusu gürültüsünü
    ('hoca'/'bilgi'/'oda'…) atar ("Nihat hocanın odası" -> ['nihat']).

    Bu temizlik SADECE isim aramasına özgüdür (STOPWORDS'e konmaz; 'bilgi'
    "Bilgi İşlem" biriminin parçası, global atılamaz) — bkz.
    intent_router.PERSON_QUERY_NOISE."""
    return [t for t in extract_content_tokens(question) if t not in PERSON_QUERY_NOISE]


def is_person_query(question: str) -> bool:
    """Soru bir KİŞİ arıyor mu (birim/iletişim değil)?

    Sinyaller: kişi-unvan kelimeleri (hoca/akademisyen…), 'kim/kimdir/unvanı'
    gibi kişi kelimeleri veya çıplak kişi adı biçimi ("Canan Deneme"). Amaç:
    kişi hiçbir kaynakta (canlı rehber + DB) bulunmayınca semantik LLM'e düşüp
    GEVEZELİK/uydurma yapmasını engellemek — bunun yerine deterministik
    'personel bulunamadı' döndürülür (birim-iletişim sorguları bu kapsamda
    DEĞİLdir, onlar semantik/birim-link zincirine düşmeli)."""
    n = normalize_turkish(question)
    return (any(kw in n for kw in PERSON_TITLE_KEYWORDS)
            or any(w in n.split() for w in PERSON_KEYWORDS)
            or looks_like_person_name(question))


# ---------------------------------------------------------------------------
# Qdrant (semantik yol)
# ---------------------------------------------------------------------------

def retrieve_semantic(cfg: Config, client: QdrantClient, question: str,
                      limit: "int | None" = None) -> "list[dict]":
    """Multi-collection RAG: SEMANTIC_COLLECTIONS'ta (şu an yalnız 'regulations')
    arar, skora göre birleştirip en iyi payload'ı döndürür. Döngü çok-koleksiyonlu
    kalır (aynı bge-m3/1024 embedding'i paylaşan yeni koleksiyon eklenebilir);
    'staff' halüsinasyon nedeniyle çıkarıldı. limit verilmezse cfg.top_k."""
    top = limit or cfg.top_k
    qvec = ollama.embeddings(model=cfg.embed_model, prompt=question)["embedding"]
    hits = []
    for coll in SEMANTIC_COLLECTIONS:
        if not client.collection_exists(coll):
            continue
        hits.extend(client.search(collection_name=coll, query_vector=qvec, limit=top))
    hits.sort(key=lambda h: h.score, reverse=True)
    # Alaka eşiği (RAG_SCORE_THRESHOLD): 0 ise dokunma (mevcut davranış korunur);
    # >0 ise eşik altı isabetleri tamamen ele — alakasız chunk LLM bağlamına hiç
    # girmesin (grounding sertleştirme). Eşik top-K dilimlemesinden ÖNCE uygulanır
    # ki elenenlerin yerine bir sonraki geçerli isabet gelebilsin.
    threshold = getattr(cfg, "score_threshold", 0) or 0
    if threshold:
        hits = [h for h in hits if h.score >= threshold]
    payloads = []
    for h in hits[:top]:
        payload = dict(h.payload or {})
        payload["_score"] = h.score
        payloads.append(payload)
    return payloads


def _qa_match_tokens(text: str) -> "set[str]":
    """Q&A eşleştirmesi için küçük, gürültüsüz token seti üretir."""
    tokens = set(extract_content_tokens(text))
    if not tokens:
        tokens = set(re.findall(r"[a-z0-9çğıöşü]+", normalize_turkish(text)))
    return {t for t in tokens if len(t) > 1}


# Direct-QA eşleştirmesinde Türkçe EŞANLAM/paraphrase köprüsü: öğrencinin günlük
# ifadesi ("arabamı ... park") SSS'deki resmi ifadeden ("aracımı ... otoparkı")
# lexical olarak uzak olabilir; bu KANONİK katlama ikisini buluşturur ki hazır SSS
# cevabı qwen üretimi olmadan (~2-3s) deterministik dönebilsin. Katlama ÖN-EK
# tabanlıdır (Türkçe ekleri tolere eder: "aracımı"→arac, "otoparkı"→park). YALNIZ
# direct-QA'da kullanılır (global token mantığı DEĞİŞMEZ); eşanlam olmayan uydurma
# adlar birbirine bağlanmaz → varlık koruması (yanlış dekan/birim) aynen sürer.
_QA_CANON = (
    (("araba", "arac", "otomobil"), "arac"),
    (("otopark", "park"), "park"),
    (("tuvalet", "wc", "lavabo"), "tuvalet"),
    # NOT: 'servis'/'ring'→otobüs BİLEREK eklenmedi — 'yemek servisi' gibi
    # kullanımları yanlış köprülerdi (over-catch riski).
)


def _canon_token(t: str) -> str:
    for prefixes, canon in _QA_CANON:
        if any(t.startswith(p) for p in prefixes):
            return canon
    return t


def _qa_syn_tokens(text: str) -> "set[str]":
    """_qa_match_tokens + ön-ek tabanlı kanonik katlama (yalnız direct-QA için)."""
    return {_canon_token(t) for t in _qa_match_tokens(text)}


def _has_stem(token: str, tokens: "set[str]") -> bool:
    """Türkçe ekleri tolere eden kaba kök eşleşmesi.

    Eşleşme: birebir; kısa olan (≥4 harf) uzunun öneki ('ders'~'dersten');
    ya da ilk 5 harf ortak ('calisma'~'calismak')."""
    for t in tokens:
        if token == t:
            return True
        short, long_ = (token, t) if len(token) <= len(t) else (t, token)
        if len(short) >= 4 and long_.startswith(short):
            return True
        if len(token) >= 5 and len(t) >= 5 and token[:5] == t[:5]:
            return True
    return False


def _iter_qa_pairs(doc: str) -> "list[tuple[str, str]]":
    """Bir chunk içindeki Markdown Soru/Cevap çiftlerini döndürür."""
    if "**Soru:**" not in doc or "**Cevap:**" not in doc:
        return []
    pattern = re.compile(
        r"\*\*Soru:\*\*\s*(.*?)\s*\*\*Cevap:\*\*\s*(.*?)(?=\n\s*\*\*Soru:\*\*|\Z)",
        re.DOTALL,
    )
    pairs = []
    for match in pattern.finditer(doc):
        soru = match.group(1).strip()
        cevap = match.group(2).strip()
        cevap = re.split(r"\n\s*#{1,6}\s+", cevap, maxsplit=1)[0].strip()
        if soru and cevap:
            pairs.append((soru, cevap))
    return pairs


def _extract_direct_qa_answer(payloads: "list[dict]", question: str = "") -> "str | None":
    """Yerel SSS/Q&A chunk'larında LLM'i atlayıp en uygun cevabı doğrudan verir."""
    if not payloads:
        return None
    # Eşanlam-farkındalı token'lar (araba↔araç, otopark↔park): öğrencinin günlük
    # ifadesini SSS'nin resmi ifadesine bağlar → hazır cevap deterministik döner.
    question_tokens = _qa_syn_tokens(question)
    best: "tuple[float, str, str] | None" = None

    for payload in payloads:
        source = payload.get("source_url") or ""
        if not source.startswith("local://"):
            continue
        doc = payload.get("document") or ""
        score = float(payload.get("_score") or 0)
        for soru, cevap in _iter_qa_pairs(doc):
            soru_tokens = _qa_syn_tokens(soru)
            overlap = len(question_tokens & soru_tokens) if question_tokens else 0
            if question_tokens and overlap == 0:
                continue
            # VARLIK KORUMASI: sorunun belirleyici token'ları (örn. uydurma
            # "Sultan Fatih") eşleşen Q&A metninde hiç geçmiyorsa bu Q&A yanlış
            # varlıktır (yanlış dekan/birim cevabı riski) — atla, LLM'e bırak.
            if question_tokens:
                qa_tokens = soru_tokens | _qa_syn_tokens(cevap)
                unmatched = [t for t in question_tokens if not _has_stem(t, qa_tokens)]
                if len(unmatched) >= 2 and len(unmatched) / len(question_tokens) >= 0.34:
                    continue
            rank = (overlap * 10) + score
            if "kampus_master_sss" in source:
                rank += 2
            elif "gaunai_egitim_soru_bankasi" in source:
                rank -= 1
            if best is None or rank > best[0]:
                best = (rank, cevap, source)

    if best:
        _, answer, source = best
        return _normalize_source_citations(f"{answer}\n\n🔗 Kaynak: {source}")

    # Geriye dönük davranış: ilk isabet tek/yerel/yüksek skorlu bir Q&A ise
    # doğrudan döndür. Bu, Eduroam gibi küratörlü kampüs SSS cevaplarını hızlandırır.
    # SADECE question_tokens BOŞSA (alaka kontrolü yapacak hiçbir sinyal yoksa)
    # devreye girer — aksi halde konu değişince (ör. takip sorusunda önceki
    # soru search_query'e karışınca) alakasız chunk'ın cevabı doğrudan
    # döndürülüp önceki turun cevabı tekrarlanabilir (gerçek regresyon,
    # 2026-07-21: "dekan kimdir" sonrası "etkinlikler" sorusuna dekan cevabı
    # döndü). Yukarıdaki döngü zaten alakasızsa best=None bırakıyor; bu son
    # çare yalnız gerçekten kontrol imkânsızsa güvenlidir.
    if question_tokens:
        return None
    top = payloads[0]
    source = top.get("source_url") or ""
    score = float(top.get("_score") or 0)
    pairs = _iter_qa_pairs(top.get("document") or "")
    if score >= 0.62 and source.startswith("local://") and len(pairs) == 1:
        return _normalize_source_citations(f"{pairs[0][1]}\n\n🔗 Kaynak: {source}")
    return None


# Yönetici sorusundan birim adı token'larını çıkarırken atılacak rol/dolgu
# kelimeleri (birimin KENDİSİ değil, çevresindeki kalıp).
_LEADER_SKIP_WORDS = frozenset({
    "fakultesi", "fakulte", "bolumu", "bolum", "enstitusu", "enstitu",
    "yuksekokulu", "myo", "kim", "kimdir", "ismi", "ismini", "isim",
    "numara", "numarasi", "nedir", "kimin", "ulasmam", "lazim", "bana",
    "hangi", "acaba", "bugun", "dersim", "var", "ulasabilir", "iletisim",
    "dekanina", "dekani", "dekanligi", "rektoru", "rektorluk", "muduru",
    "mudurlugu", "baskani", "baskanligi",
} | set(LEADER_ROLE_WORDS))


def _leader_unit_tokens(question: str) -> "set[str]":
    """Yönetici sorusundaki BİRİM tanımlayıcı token'lar (≥3 harf, rol/dolgu hariç)."""
    return {w for w in match_norm(question).split()
            if len(w) >= 3 and w not in _LEADER_SKIP_WORDS}


def focused_leader_query(question: str) -> str:
    """Gürültülü yönetici sorusundan temiz bir retrieval sorgusu üretir:
    "eğitim fakültesine ulaşmam lazım dekanının ismini" -> "egitim dekani kimdir".
    Böylece küratörlü dekan Q&A'sı retrieval'da üste çıkar."""
    qn = match_norm(question)
    role = next((w for w in LEADER_ROLE_WORDS if w in qn), "dekan")
    unit = " ".join(sorted(_leader_unit_tokens(question)))
    return f"{unit} {role} kimdir".strip()


# "Hepsini listele" ipuçları — "her departmanın dekanı", "tüm dekanlar",
# "dekanları listele" gibi ÇOKLU yönetici isteği (tek birim değil).
_LIST_ALL_CUES = ("her ", "tum ", "butun ", "hepsi", "listele", "hangileri",
                  "tamami", "komple", "dekanlar", "rektorler", "mudurler",
                  "dekanlari", "yoneticiler")


def is_list_all_leaders(question: str) -> bool:
    """Soru TEK bir birimin değil, BİRDEN ÇOK yöneticinin (tüm dekanlar gibi)
    listesini mi istiyor?"""
    n = match_norm(question) + " "
    return (any(w in n for w in LEADER_ROLE_WORDS)
            and any(c in n for c in _LIST_ALL_CUES))


def _all_curated_leaders(cfg: Config, client: QdrantClient, role: str) -> "list[str]":
    """Küratörlü (local://) veriden verilen role ait TÜM yönetici cevaplarını
    toplar (geniş retrieval + tekilleştirme). role: 'dekan'/'rektor'/... —
    yalnız o rolü içeren Q&A'lar alınır (dekan sorusuna rektörü karıştırma)."""
    seen: "set[str]" = set()
    out: "list[str]" = []
    probes = (f"{role} kimdir", f"{role} fakülte yönetici", "dekan rektör yönetici kimdir")
    for probe in probes:
        for p in retrieve_semantic(cfg, client, probe, limit=20):
            if not (p.get("source_url") or "").startswith("local://"):
                continue
            for soru, cevap in _iter_qa_pairs(p.get("document") or ""):
                if role not in match_norm(soru):
                    continue
                key = cevap.strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(key)
    return out


def _leader_curated_answer(payloads: "list[dict]", question: str) -> "str | None":
    """Yönetici (dekan/rektör/müdür) sorusu için SADECE küratörlü (local://) bir
    Q&A eşleşmesi döndürür — hem soru hem küratörlü Q&A aynı yönetici rolünü
    içermeli VE ortak bir BİRİM tanımlayıcı token (≥3 harf, rol/genel/dolgu
    kelimesi değil) paylaşmalı. Bu, gürültülü ("...ulaşmam lazım ismini ve
    numarası") sorularda _extract_direct_qa_answer'ın varlık-koruması yüzünden
    reddettiği DOĞRU dekan cevabını güvenle bulur; ama birim token'ı eşleşmesi
    şart olduğu için başka fakültenin cevabına ASLA sıçramaz (2026-07-22:
    "eğitim dekanı" sorusu fef iletişim sayfasına karışıp yanlış numara
    uyduruyordu). Karışık chunk'lardan serbest üretim YOK — yalnız birebir
    küratörlü cevap."""
    if not any(w in match_norm(question) for w in LEADER_ROLE_WORDS):
        return None
    unit_tokens = _leader_unit_tokens(question)
    if not unit_tokens:
        return None
    for payload in payloads:
        source = payload.get("source_url") or ""
        if not source.startswith("local://"):
            continue
        for soru, cevap in _iter_qa_pairs(payload.get("document") or ""):
            sn = match_norm(soru)
            if not any(w in sn for w in LEADER_ROLE_WORDS):
                continue
            s_words = set(sn.split())
            if unit_tokens & s_words:  # ortak gerçek birim token'ı
                return _normalize_source_citations(f"{cevap}\n\n🔗 Kaynak: {source}")
    return None


# qwen bağlamda cevap YOKKEN temiz "Bilmiyorum" demek yerine bazen talimatı
# sızdırıyor ya da "verilen metinlerle ilgili değil" gibi meta cümleler kuruyor
# (2026-07-27 "yaml nedir" → garbage; is_bilmiyorum yalnız başa baktığı için
# kaçıyordu). Bu kalıplar da "cevap yok" sayılır ki semantik-fallback (canlı web
# araması) tetiklensin — aksi halde garbage doğrudan kullanıcıya dönüyordu.
_NO_ANSWER_MARKERS = (
    "cevap yoksa", "verilen metin", "verilen bilgiler", "verilen baglam",
    "bu metinler", "metinlerde", "metinlerden", "metinlerle ilgili degil",
    "baglamla ilgili degil", "baglamda", "bilgi bulamad", "bilgi bulunmamakta",
    "bilgi yer almamakta", "ilgili bilgi yok", "bilgi verilmedi",
    "bilgi verilmemis", "bilgi mevcut degil", "belirtilmemis",
)


def _semantic_no_answer(result: str) -> bool:
    """Semantik üretim 'cevap yok'a mı işaret ediyor — temiz Bilmiyorum, Çince
    çöp (is_garbled) ya da qwen'in grounding-yok meta/talimat sızıntısı? True ise
    çağıran canlı web fallback'ine düşer (garbage'ı kullanıcıya göstermez)."""
    if is_bilmiyorum(result) or is_garbled(result):
        return True
    return any(m in normalize_turkish(result) for m in _NO_ANSWER_MARKERS)


# ANTİ-KİLİTLENME: ilk arama boş dönerse LLM ile sorguyu eşanlamlı/resmi
# terimlerle GENİŞLETİP ikinci bir arama tetiklenir ("bütler ne zaman" →
# "bütünleme sınav tarihleri akademik takvim").
REFORMULATE_PROMPT = (
    "Bir web arama sorgusunu, daha iyi sonuç bulması için YENİDEN yaz. "
    "Kullanıcının günlük/kısaltılmış ifadesini resmi ve EŞANLAMLI terimlerle "
    "genişlet (örn. 'bütler ne zaman' -> 'bütünleme sınav tarihleri akademik "
    "takvim'). SADECE yeni arama sorgusunu yaz; açıklama, tırnak, noktalama ekleme."
)


def _reformulate_query(cfg: Config, question: str) -> str:
    """Soruyu eşanlamlı/resmi terimlerle yeniden formüle eder (retry için). LLM
    başarısızsa boş döner."""
    try:
        resp = ollama.chat(
            model=cfg.llm_model,
            messages=[{"role": "system", "content": REFORMULATE_PROMPT},
                      {"role": "user", "content": question}],
            options={"temperature": 0.3, "num_predict": 96})
        out = resp["message"]["content"].strip().strip('"').strip()
        return out[:200] if out and not is_garbled(out) else ""
    except Exception as exc:
        log.warning("Reformülasyon başarısız: %s", exc)
        return ""


def answer_from_web(cfg: Config, question: str,
                    history: "list[dict] | None" = None,
                    gaun_scope: "bool | None" = None) -> "str | None":
    """Yerel kaynaklar (GAÜN RAG/DB) boş kaldığında SON ÇARE: canlı web araması.

    gaun_scope: None ise NİYETE göre otomatik (intent_router.needs_gaun_scope —
    üniversite konusu→True). True: DOMAIN İZOLASYONU (site:gaziantep.edu.tr; başka
    üniversite verisi karışmaz). False: günlük hayat → SAF internet.

    ANTİ-KİLİTLENME: ilk arama boşsa sorgu LLM ile yeniden formüle edilip İKİNCİ
    kez aranır (reformulation retry). Grounding: tam sayfa + snippet. İki denemede
    de sonuç/cevap yoksa None (çağıran DİNAMİK yönlendirici mesaja düşer)."""
    if gaun_scope is None:
        gaun_scope = needs_gaun_scope(question)
    context, used_q, n_results = _web_grounding_context(cfg, question, gaun_scope)
    if context is None:
        return None
    result = generate(cfg, context, used_q, WEB_SEARCH_SYSTEM_PROMPT, history)
    if _web_answer_empty(result):
        log.info("Web sonuçlarında da cevap yok: %r", question)
        return None
    log.info("Canlı web aramasından yanıt üretildi (%d sonuç, scope=%s): %r",
             n_results, gaun_scope, used_q)
    return result


def _web_answer_empty(result: str) -> bool:
    """Web üretimi 'cevap yok'a mı işaret ediyor? (bilmiyorum/çöp/bulamad)."""
    low = normalize_turkish(result)
    return (is_bilmiyorum(result) or is_garbled(result)
            or "bilmiyorum" in low or "bulamad" in low)


def _generated_answer_empty(result: str) -> bool:
    """Fallback (canlı birim VEYA web) üretimi 'cevap yok' mu? Hem semantik
    (grounding-yok meta sızıntısı, çöp) hem web ('bulamad') işaretlerini kapsar —
    eski canlı-sayfa ('bilmiyorum' herhangi yerde) ve web ('bulamad') sıkı
    kontrollerinin BİRLEŞİMİ; hiçbir gevşeme yok."""
    return _semantic_no_answer(result) or _web_answer_empty(result)


def _web_grounding_context(cfg: Config, question: str,
                           gaun_scope: bool) -> "tuple[str | None, str, int]":
    """Web ARAMASI + grounding — ÜRETİM YAPMAZ. (context, used_q, sonuç_sayısı).

    answer_from_web'den ayrıştırıldı (2026-07-29): eşzamanlı fallback yolu
    (answer_semantic) bu bağlamı LLM üretimi olmadan, DİĞER kaynaklarla (canlı
    birim sayfası) PARALEL çekebilsin ve en iyi bağlam seçilip TEK üretim
    yapılabilsin. ANTİ-KİLİTLENME reformülasyon-retry burada korunur. Sonuç
    yoksa (context=None) döner."""
    results = web_search(question, gaun_scope=gaun_scope)
    used_q = question
    if not results:
        # RETRY: eşanlamlı reformülasyon ile ikinci arama.
        reformed = _reformulate_query(cfg, question)
        if reformed and normalize_turkish(reformed) != normalize_turkish(question):
            log.info("Web boş → reformülasyon retry: %r → %r", question, reformed)
            results = web_search(reformed, gaun_scope=gaun_scope)
            if results:
                used_q = reformed
    if not results:
        log.info("Web araması (2 deneme) sonuç döndürmedi: %r", question)
        return None, used_q, 0
    context = build_grounding(used_q, results, gaun_scope=gaun_scope)
    return context, used_q, len(results)


# DİNAMİK FALLBACK: iki aramada da cevap yoksa, statik 'Bilmiyorum' TEKRARI
# yerine soruya ÖZEL, dürüst ama yönlendirici bir mesaj üretilir (temp=0.4).
DYNAMIC_FALLBACK_PROMPT = (
    "Kullanıcının Gaziantep Üniversitesi ile ilgili sorusuna güncel kaynaklarda "
    "cevap bulunamadı. KISA (2-3 cümle), sıcak, DÜRÜST ama YÖNLENDİRİCİ bir mesaj "
    "yaz: bu spesifik bilgiye şu an ulaşamadığını belirt ve kullanıcıyı somut bir "
    "sonraki adıma yönlendir (ilgili birim ya da Öğrenci İşleri Daire Başkanlığı'na "
    "danışma; soruyu farklı/daha açık kelimelerle sorma). Bilgi UYDURMA; URL/link, "
    "telefon, sayı, tarih VERME. Markdown kullanma."
)


def _dynamic_not_found(cfg: Config, question: str,
                       history: "list[dict] | None" = None) -> str:
    """Statik 'Bilmiyorum'/'resmi sayfa' TEKRARI yerine soruya ÖZEL, dürüst ama
    yönlendirici mesaj (temperature=0.4). Uydurma link/sayı temizlenir; LLM
    başarısız/bozuksa statik graceful_not_found'a güvenli döner."""
    try:
        resp = ollama.chat(
            model=cfg.llm_model,
            messages=[{"role": "system", "content": DYNAMIC_FALLBACK_PROMPT},
                      {"role": "user", "content": question}],
            options={"temperature": 0.4})
        msg = resp["message"]["content"].strip()
        # temp>0'da uydurma link/kaynak sızabilir — kesin temizle.
        msg = re.sub(r"https?://\S+", "", msg)
        msg = re.sub(r"🔗\s*Kaynak:.*", "", msg).strip()
        if len(msg) >= 15 and not is_garbled(msg) and not is_bilmiyorum(msg):
            log.info("Dinamik yönlendirici fallback üretildi")
            return msg
    except Exception as exc:
        log.warning("Dinamik fallback başarısız: %s", exc)
    return graceful_not_found(question)


# Canlı-birim sayfasının "gerçek içerik" sayılması için asgari uzunluk — çok kısa
# (menü/hata/boş) sayfalar bağlam adayı olmasın.
_MIN_UNIT_CONTEXT_CHARS = 200


def _context_relevance(context: str, question: str) -> int:
    """Bağlamın soruya ALAKA skoru: sorunun içerik token'larından kaçı bağlamda
    (normalize edilmiş) geçiyor. LLM YOK — deterministik kelime örtüşmesi.

    Amaç: RAG başarısız olduktan sonra PARALEL çekilen adaylardan (canlı birim
    sayfası vs web) soruya en çok değen bağlamı ÜRETMEDEN seçmek. Örn. "kütüphane
    bilgisayarlarına Docker" sorusunda kütüphane sayfası 'docker'ı içermez (skor
    düşük), web sonucu içerir (skor yüksek) → web seçilir."""
    ctx_n = normalize_turkish(context)
    tokens = set(extract_content_tokens(question))
    if not tokens:
        return 0
    return sum(1 for t in tokens if t in ctx_n)


def _parallel_fallback_contexts(
        cfg: Config, question: str, unit_url: str,
        gaun_scope: bool = True) -> "tuple[str, tuple[str | None, str, int]]":
    """Canlı-birim sayfası + web grounding'i EŞ ZAMANLI (ThreadPoolExecutor) çeker.

    YALNIZ RAG üretimi başarısız olduğunda çağrılır — spekülatif DEĞİL (her
    semantik soruda web'i tetikleyip keyless DDG'yi rate-limit'e sokmayı önler,
    kullanıcı kararı 2026-07-29). İki I/O aynı anda döner (sıralı 2 network+LLM
    zinciri yerine). Döner: (unit_text, (web_context, web_used_q, web_n)).

    ollama/requests SENKRON istemciler olduğu için asyncio yerine thread havuzu
    kullanılır (api.py ile aynı desen). Her iki görev de kendi içinde hatayı
    yutar (fetch_unit_page/​_web_grounding_context) — biri patlarsa diğeri sürer."""
    fetch_unit = unit_url and unit_url != DEFAULT_UNIT_URL
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="fallback") as ex:
        unit_future = ex.submit(fetch_unit_page, unit_url) if fetch_unit else None
        web_future = ex.submit(_web_grounding_context, cfg, question, gaun_scope)
        unit_text = ""
        if unit_future is not None:
            try:
                unit_text = unit_future.result() or ""
            except Exception as exc:  # canlı sayfa patlarsa web yine denenir
                log.warning("Canlı birim sayfası çekimi başarısız: %s", exc)
        try:
            web = web_future.result()
        except Exception as exc:
            log.warning("Web grounding çekimi başarısız: %s", exc)
            web = (None, question, 0)
    return unit_text, web


def answer_semantic(cfg: Config, client: QdrantClient, question: str,
                    search_query: str, history: "list[dict] | None" = None,
                    route_label: str = "SEMANTİK (RAG)", conn=None,
                    raw_question: "str | None" = None,
                    leader_query: bool = False) -> str:
    """Semantik RAG cevabını üretir; normal semantik yol ve SQL fallback ortaktır."""
    payloads = retrieve_semantic(cfg, client, search_query)
    log.info("Yönlendirme: %s — %d isabet", route_label, len(payloads))
    direct = _extract_direct_qa_answer(payloads, question)
    if direct:
        source_match = re.search(r"🔗\s*Kaynak:\s*(\S+)", direct)
        source_log = source_match.group(1) if source_match else payloads[0].get("source_url")
        log.info("Yerel Q&A doğrudan cevaplandı — kaynak=%s", source_log)
        return direct
    if leader_query:
        # Yönetici (dekan/rektör/müdür) sorusu — İSİM/İLETİŞİM bilgisi yalnız
        # KÜRATÖRLÜ (doğrulanmış) veriden gelir; canlı sayfadan LLM'e isim
        # ürettirmek uydurma riski taşır (2026-07-22: "eğitim dekanı" için
        # canlı sayfadan var olmayan "Prof. Dr. Ayşe KAYA" uyduruldu).
        q_for_leader = raw_question or question
        # "Her departmanın dekanı / tüm dekanlar" gibi ÇOKLU istek: tek birim
        # eşleştirip "bilmiyorum" deme — küratörlü veride DOĞRULANMIŞ ne kadar
        # yönetici varsa hepsini listele, gerisi için dürüstçe yönlendir
        # (2026-07-22 canlı bug: elde 3 dekan varken "bilmiyorum" dedi).
        if is_list_all_leaders(q_for_leader):
            role = next((w for w in LEADER_ROLE_WORDS if w in match_norm(q_for_leader)), "dekan")
            leaders = _all_curated_leaders(cfg, client, role)
            if leaders:
                log.info("Yönetici LİSTE sorusu → %d küratörlü kayıt döndürüldü", len(leaders))
                body = "\n".join(f"- {c}" for c in leaders)
                return _normalize_source_citations(
                    "Doğrulanmış kayıtlarımdaki bilgiler şunlar:\n" + body +
                    "\n\nListede olmayan birimlerin güncel yönetici bilgisi için "
                    "ilgili fakültenin resmi sayfasını ya da https://rehber.gaziantep.edu.tr/ "
                    "adresini ziyaret edebilirsiniz.\n\n🔗 Kaynak: local://kampus_master_sss.md")
            # Küratörlü hiç kayıt yoksa aşağıdaki dürüst yönlendirmeye düş.
        leader_ans = _leader_curated_answer(payloads, q_for_leader)
        if not leader_ans:
            # İlk retrieval gürültülü sorguyla küratörlü dekan chunk'ını
            # getirmemiş olabilir — temiz, odaklı bir sorguyla TEKRAR ara.
            focused = focused_leader_query(q_for_leader)
            log.info("Yönetici sorusu → odaklı retrieval denemesi: %r", focused)
            leader_ans = _leader_curated_answer(
                retrieve_semantic(cfg, client, focused), q_for_leader)
        if leader_ans:
            log.info("Yönetici sorusu → küratörlü cevap döndürüldü")
            return leader_ans
        # Küratörlü veri yok: İSİM UYDURMAMAK için canlı üretim YAPMA — dürüst
        # yönlendir. Gerçek birim ise resmi sayfaya, uydurma birim ise "yok" de.
        log.info("Yönetici sorusu → küratörlü veri yok, dürüst yönlendirme (canlı üretim yok)")
        if conn is not None and match_department(q_for_leader, fetch_departments(conn)) is None \
                and resolve_unit_link(q_for_leader)[1] == DEFAULT_UNIT_URL \
                and looks_like_unit_query(q_for_leader):
            return unit_not_found_message()
        # SON ÇARE: küratörlü veride yok ama GERÇEK bir birimin yöneticisi
        # sorulmuş olabilir (ör. "bilgi işlem daire başkanı kim"). Canlı web —
        # GAÜN domainine İZOLE (site:gaziantep.edu.tr; başka kurum verisi
        # karışmasın) + reformülasyon-retry. Bulunamazsa DİNAMİK yönlendirici mesaj.
        web = answer_from_web(cfg, raw_question or question, history, gaun_scope=True)
        if web:
            return web
        return _dynamic_not_found(cfg, q_for_leader, history)
    else:
        context = format_context(payloads)
        # Üretimde HAM soru gösterilir (jargon-çevrilmiş 'question' değil):
        # condense adımı öğrencinin gerçek üslubunu ("ya çok parasız kaldım"
        # gibi absürt/duygusal çerçeveyi) resmi bir politika sorusuna
        # ("...iade politikası") dönüştürüp modelin absürt/gerçek ayrımını
        # (Kural 3) yapamamasına yol açıyordu — model condensed metni görünce
        # "gerçek ama bilinmeyen bir süreç" sanıp düz "Bilmiyorum" diyordu
        # (2026-07-22 canlı bug). Retrieval/direct-QA eşleştirmesi hâlâ condensed
        # 'question' ile yapılır, sadece son üretim adımı ham metne döner.
        result = generate(cfg, context, raw_question or question, SYSTEM_PROMPT, history)
    # _semantic_no_answer: temiz "Bilmiyorum" + Çince çöp + qwen'in grounding-yok
    # meta/talimat sızıntısı ("verilen metinlerle ilgili değil" vb.) hepsi "cevap
    # yok" sayılır → aşağıdaki zincir (kadro → canlı sayfa → CANLI WEB → dürüst
    # yönlendirme) devreye girer; garbage kullanıcıya gösterilmez.
    if _semantic_no_answer(result):
        # Son çare: SSS'de cevap yok ama soru açıkça bir birimden bahsediyorsa
        # ("bilgisayar mühendislerini bul", "hocalarını göster" gibi kadro
        # sorularının HEPSİNİ anahtar kelimeyle yakalamak mümkün değil — bkz.
        # ROSTER_KEYWORDS), kaba bir "Bilmiyorum" yerine birimin gerçek
        # personel dökümünü ver. match_department deterministiktir (LLM yok),
        # o yüzden bu adım uydurma riski taşımaz (2026-07-22 canlı bug).
        # Yönetici (tek kişi) sorularında kadro dökümü YAPILMAZ (leader_query
        # parametresiyle taşınır) — dekan/rektör/müdür sorusuna 25-40 kişilik
        # liste dönmek yanlış; canlı sayfa/dürüst yönlendirme zincirine devam.
        dept = None
        if conn is not None and not leader_query:
            dept = match_department(search_query, fetch_departments(conn))
            if dept:
                rows = fetch_department_staff(conn, dept["id"])
                if rows:
                    log.info("Bilgi bulunamadı → birim kadrosuna düşüldü: %s (%d personel)",
                             dept["name"], len(rows))
                    return format_staff_answer(
                        [staff_row_to_payload(r) for r in rows],
                        f"{dept['name']} personeli ve dahilileri:")

        # SON ÇARE ZİNCİRİ — EŞ ZAMANLI (PARALEL) + TEK ÜRETİM (2026-07-29,
        # kullanıcı kararı: doğruluk > gecikme). Eskiden bu adımlar SIRAYLA
        # çalışıp her başarısız kaynakta ayrı bir LLM üretimi yapıyordu (canlı
        # birim sayfası ÜRET→"Bilmiyorum", sonra web ÜRET→cevap): 3 sıralı üretim,
        # ~76 sn. Artık çekirdek RAG üretimi (yukarıda) başarısız olunca canlı
        # birim sayfası + web grounding'i PARALEL çekip (I/O eş zamanlı), en
        # ALAKALI bağlamı seçip TEK üretim yapıyoruz. RAG üretimi eşik ile
        # ATLANMAZ (bge-m3 skorları temiz ayrışmıyor: alakasız 0.577 ~ gerçek
        # 0.633 → sessiz regresyon riski); yalnız FALLBACK zinciri hızlandırılır.
        raw_q = raw_question or question
        unit_name, unit_url = resolve_unit_link(raw_q)
        has_unit = unit_url != DEFAULT_UNIT_URL

        # G1 — KİŞİ sorgusu: WEB'E ASLA GİTME (benzer-isimli kişi halüsinasyonu
        # riski). Yalnız SPESİFİK birim sayfası denenir (tek üretim); o da yoksa
        # deterministik personel-bulunamadı. (is_person_query: unvan/kim/çıplak
        # isim — token/isim tabanlı, 'bilgi'⊂'bilgisayar' substring bug'ı YOK;
        # bkz. Kırmızı-Takım BULGU-2.)
        if is_person_query(raw_q):
            if has_unit:
                unit_text = fetch_unit_page(unit_url)
                if unit_text:
                    r = generate(cfg, f"[Kaynak: {unit_url}] Metin: {unit_text}",
                                 raw_q, SYSTEM_PROMPT, history)
                    if not _generated_answer_empty(r):
                        log.info("Kişi sorgusu → birim sayfasından yanıt (%s)", unit_url)
                        return r
            log.info("Bilgi bulunamadı + kişi sorgusu → personel rehberi yönlendirmesi")
            return staff_not_found_message()

        # G2 — UYDURMA BİRİM: soru "X Fakültesi/Bölümü" diyor ama ne SSS'de, ne
        # MariaDB'de, ne de küratörlü URL'de var → web'e GİTME, NET yokluk beyanı
        # (halüsinasyon testleri: "Sultan Fatih Fakültesi").
        if dept is None and looks_like_unit_query(question):
            log.info("Bilgi bulunamadı + birim adı gibi ama kayıtlarda yok → yokluk beyanı")
            return unit_not_found_message()

        # G3 — CEVAPLANABİLİR genel/teknik/gerçek-birim sorusu: canlı birim sayfası
        # + web grounding EŞ ZAMANLI çekilir (spekülatif DEĞİL — RAG zaten
        # başarısız). En alaka-skorlu bağlam seçilip TEK üretim yapılır. Web,
        # eşitlikte tercih edilir (gaziantep.edu.tr'ye izole GENİŞ ağ, birim
        # domainini de kapsar); birim sayfası yalnız KESİN daha alakalıysa kazanır
        # ("kütüphane bilgisayarına Docker" → birim skoru≈web ama web 'docker'ı
        # içerdiği için kazanır; "kütüphane çalışma saatleri" → birim kazanır).
        unit_text, (web_ctx, web_q, web_n) = _parallel_fallback_contexts(
            cfg, raw_q, unit_url, gaun_scope=True)
        candidates = []
        if has_unit and len(unit_text) >= _MIN_UNIT_CONTEXT_CHARS:
            candidates.append((_context_relevance(unit_text, raw_q), False, "birim sayfası",
                               f"[Kaynak: {unit_url}] Metin: {unit_text}", raw_q, SYSTEM_PROMPT))
        if web_ctx:
            candidates.append((_context_relevance(web_ctx, raw_q), True, "web",
                               web_ctx, web_q, WEB_SEARCH_SYSTEM_PROMPT))
        if candidates:
            # (skor, web?) azalan: yüksek skor önce, eşitlikte web (True>False).
            candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
            _score, _is_web, kind, ctx, gen_q, prompt = candidates[0]
            log.info("PARALEL fallback → '%s' bağlamı seçildi (skor=%d, tek üretim)",
                     kind, _score)
            r = generate(cfg, ctx, gen_q, prompt, history)
            if not _generated_answer_empty(r):
                return r
            log.info("Seçilen bağlam (%s) da cevap üretmedi", kind)

        # G4 — hiçbir kaynakta yok → statik 'Bilmiyorum' TEKRARI yerine DİNAMİK
        # yönlendirici mesaj (anti-kilitlenme).
        log.info("Bilgi bulunamadı → dinamik yönlendirici fallback")
        return _dynamic_not_found(cfg, raw_q, history)
    return result


# ---------------------------------------------------------------------------
# Ortak: bağlam -> LLM
# ---------------------------------------------------------------------------

def generate(cfg: Config, context: str, question: str, system_prompt: str,
             history: "list[dict] | None" = None) -> str:
    try:
        resp = ollama.chat(
            model=cfg.llm_model,
            messages=build_chat_messages(context, question, system_prompt, history),
            # num_predict: cevap uzunluğu üst sınırı. ARM CPU'da (~13 tok/sn)
            # sınırsız üretim ara sıra 150sn+ kaçak yapıyordu; 640 token (~1-2
            # paragraf) normal cevaplara yetiyor, en kötü durumu ~50sn'ye çekiyor.
            options={"temperature": 0, "num_predict": 640},
        )
        return _normalize_source_citations(resp["message"]["content"].strip())
    except Exception as exc:
        # Küçük sunucularda büyük LLM yüklenemeyebilir. Bağlamda açık Q&A varsa
        # model üretimine ihtiyaç duymadan kaynaklı cevabı döndür; yoksa çökme.
        log.error("LLM üretimi başarısız (%s); extractive fallback deneniyor.", exc)
        source_match = re.search(r"\[Kaynak:\s*([^\]\s]+)\]", context)
        source = source_match.group(1) if source_match else "Bilinmiyor"
        direct = _extract_direct_qa_answer(
            [{"document": context, "source_url": source, "_score": 1.0}], question)
        if direct:
            return direct
        return graceful_not_found(question)


def _strip_markdown_formatting(answer: str) -> str:
    """Model markdown süslemesini (#, **, ---) prompt talimatına rağmen KULLANMAYA
    devam edebiliyor — liste isteklerinde LLM'lerin markdown'a eğilimi güçlü
    (2026-07-22, kullanıcı ekran görüntüsü: widget markdown yorumlamadığı için
    "### Özellikler" / "**Belgeler:**" çıplak sembollerle görünüyordu). Harita
    kuralıyla aynı ilke: kritik/istenen davranış LLM'e değil koda yazılır.
    """
    # Başlık işaretleri: satır başındaki #, ##, ### ... -> düz metin
    answer = re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]*", "", answer)
    # Kalın/italik vurgular: **metin**/__metin__ -> metin (yalnız işaret kalkar)
    answer = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", answer)
    answer = re.sub(r"__([^_\n]+)__", r"\1", answer)
    # Tek başına bir satırdaki yatay çizgi (---, ***, ___)
    answer = re.sub(r"(?m)^[ \t]*([-*_])\1{2,}[ \t]*$", "", answer)
    # Yukarıdaki temizlik sonrası oluşabilecek fazla boş satırları sadeleştir
    return re.sub(r"\n{3,}", "\n\n", answer)


def _normalize_source_citations(answer: str) -> str:
    """LLM'in local:// kaynaklarını toparlar; 'Bilmiyorum' cevabında kaynak ekleme.

    Model bağlamda cevabı bulamayıp 'Bilmiyorum' dediğinde, fallback'te getirilen
    alakasız bir kaydın kaynağını iliştirmesi çelişkili olur — bu durumda '🔗
    Kaynak: ...' satırı tamamen kırpılır, yalnız cevap döner.
    """
    answer = _strip_markdown_formatting(answer)
    answer = answer.replace("https://local://", "local://")
    answer = answer.replace("http://local://", "local://")
    answer = re.sub(r"(🔗 Kaynak:\s*)\[([^\]\n]+)\]", r"\1\2", answer)
    # 'Bilmiyorum' cevabında kaynak linki KESİNLİKLE eklenmez. Türkçe katlama
    # kullanılır: .lower() büyük 'İ'yi ASCII 'i'ye çevirmez (BİLMİYORUM kaçar).
    if "bilmiyorum" in normalize_turkish(answer):
        return re.split(r"\n*🔗\s*Kaynak\s*:", answer, maxsplit=1)[0].strip()
    answer = re.sub(r"(?<!\n)(🔗 Kaynak:)", r"\n\n\1", answer)
    return answer.strip()


def answer_question(cfg: "Config | str", conn=None,
                    client: "QdrantClient | None" = None,
                    question: "str | None" = None,
                    history: "list[dict] | None" = None,
                    search_query: "str | None" = None,
                    raw_question: "str | None" = None) -> str:
    # String kısayolu: answer_question("soru") veya answer_question("soru", history)
    if isinstance(cfg, str):
        hist = conn if isinstance(conn, list) else None
        return answer(cfg, hist)
    if question is None or conn is None or client is None:
        raise TypeError("answer_question(question[, history]) veya "
                        "answer_question(cfg, conn, client, question) kullan.")

    # search_query: sınıflandırma + retrieval için (takipte konu-zengin olabilir).
    # question: direct-QA eşleştirmesi için temiz (jargon-çevrilmiş) metin.
    # raw_question: nihai LLM üretiminde gösterilecek öğrencinin GERÇEK sözü
    # (persona'nın absürt/gerçek ayrımı + ton için buna ihtiyacı var).
    sq = search_query or question
    leader_query = asks_for_unit_leader(raw_question or sq)
    intent = classify_intent(sq)
    # REGRESYON KORUMASI: fast-track kararı HAM soruya da bakar. condense adımı
    # GAÜN anahtar kelimelerini ("yatay geçiş" vb.) düşürüp condensed sorguyu
    # yanlışlıkla genel-bilgi'ye çevirebiliyor; ham soru GAÜN ise ASLA web'e
    # kaçırma (yalnız HAM + condensed'in İKİSİ de genel ise fast-track).
    if intent == "general_knowledge" and classify_intent(raw_question or question) == "general_knowledge":
        # Üniversiteyle İLGİSİZ genel/teknik soru. GAÜN KAPSAM KİLİDİ açıksa
        # (varsayılan): genel web'e ÇIKMA — alakasız/saçma cevap yerine kibarca
        # "yalnız GAÜN" reddi ver (cevaplar üniversite bağlamında kalsın).
        if GAUN_ONLY_MODE:
            log.info("GAÜN KAPSAM: üniversite-dışı soru → kibar kapsam reddi (web YOK)")
            return GAUN_SCOPE_MESSAGE
        # GAUN_ONLY_MODE=false: genel-asistan modu. GÜNLÜK HAYAT sorusu (hava/
        # tarif/genel kültür) → SAF internet araması (gaun_scope=False, dork YOK).
        log.info("HIZLI YOL: general_knowledge → RAG atlandı, SAF web (izolasyon yok)")
        web = answer_from_web(cfg, raw_question or sq, history, gaun_scope=False)
        if web:
            return web
        return answer_semantic(cfg, client, question, sq, history,
                               "GENEL→SEMANTİK GERİ DÖNÜŞ", conn, raw_question, leader_query)
    if intent == "structural":
        # LLM-first HİBRİT kaynak sırası (kullanıcı kararı 2026-07-27): kesin
        # personel verisi ASLA uydurulmaz, hepsi DETERMİNİSTİK biçimlenir
        # (format_staff_answer — LLM personel adı/numarası üretMEZ), fakat
        # BİRİNCİL kaynak artık canlı web:
        #   1) BİRİM KADROSU → DB   (canlı rehber birim listeleyemez; DB'nin
        #      korunan tek asli görevi — bkz. resolve_roster)
        #   2) İSİMLE KİŞİ  → CANLI rehber (rehber.gaziantep.edu.tr, anlık POST;
        #      güncel/gerçek — "LLM-first, veri canlı web'den" ilkesi)
        #   3) canlı erişilemez/boş → DB isim araması (YALNIZ çevrimdışı emniyet)
        roster_rows, header, aciklama = resolve_roster(conn, sq)
        if roster_rows:
            log.info("Yönlendirme: BİRİM KADROSU (DB) — %s", aciklama)
            return format_staff_answer(
                [staff_row_to_payload(r) for r in roster_rows], header)

        # İsimle kişi: yönetici (dekan/rektör) sorusu değilse canlı rehberi
        # dene. Yönetici sorusunda kadro/kişi dökme riski var → semantik curated.
        name_tokens = person_name_tokens(sq)
        if not leader_query and name_tokens:
            live_rows = fetch_staff_from_directory(name_tokens)  # BİRİNCİL: canlı
            if live_rows:
                log.info("Yönlendirme: İSİM → CANLI rehber %d kayıt (%s)",
                         len(live_rows), name_tokens)
                return format_staff_answer(live_rows, "Güncel personel rehberinden:")
            db_rows = search_staff_by_tokens(conn, name_tokens)  # EMNİYET: DB
            if db_rows:
                log.info("Canlı rehber boş/erişilemez → DB isim emniyeti %d kayıt",
                         len(db_rows))
                return format_staff_answer(
                    [staff_row_to_payload(r) for r in db_rows], "")
            log.info("İsim: canlı VE DB boş: %s", name_tokens)

        # Kişi sorgusu ama hiçbir kaynakta (canlı rehber + DB) yok → DETERMİNİSTİK
        # dürüst 'bulunamadı'. Semantik LLM'e DÜŞÜRMEyiz: qwen tanımadığı isme
        # gevezelik/karşı-soru üretiyor (2026-07-27: "Zzxqw kimdir" → uzun sohbet)
        # ya da benzer isimli birini uyduruyor. Yönetici (dekan/rektör) sorusu
        # leader_query ile zaten yukarıda ayrıldı → buraya düşen kişi sorgusu
        # curated değil, düz kişi aramasıdır. Birim/iletişim sorguları
        # is_person_query'de DEĞİL → semantik/birim-link zincirine düşer.
        if not leader_query and is_person_query(sq):
            # Kişi hiçbir GAÜN kaynağında (canlı rehber + DB) yok. GAÜN KAPSAM
            # KİLİDİ açıksa çıplak "X kimdir"i genel web'e SORMAYIZ (ör. "Einstein
            # kimdir" alakasız kalır) — dürüst personel-bulunamadı. Kilit kapalıysa
            # (genel-asistan modu) staff-bağlamı yoksa web'de aranır.
            staff_context = any(kw in normalize_turkish(sq) for kw in PERSON_TITLE_KEYWORDS)
            if not GAUN_ONLY_MODE and not staff_context:
                # Genel kişi (ör. "Einstein kimdir") → SAF internet (izolasyon yok).
                web = answer_from_web(cfg, raw_question or sq, history, gaun_scope=False)
                if web:
                    log.info("Çıplak kişi sorgusu GAÜN'de yok → genel web cevabı")
                    return web
            log.info("Kişi hiçbir kaynakta yok → dürüst personel-bulunamadı")
            return staff_not_found_message()
        log.info("Yapısal sonuç boş; SEMANTİK (RAG) fallback başlatılıyor.")
        return answer_semantic(cfg, client, question, sq, history, "SEMANTİK FALLBACK (RAG)",
                                conn, raw_question, leader_query)

    return answer_semantic(cfg, client, question, sq, history, conn=conn,
                            raw_question=raw_question, leader_query=leader_query)


def _ensure_logging() -> None:
    # `python -c "import bot; bot.answer_question(...)"` çağrısında da akış
    # logları (Cache Hit/Miss, Live Fetch) görünsün.
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")


def _sanitize_history(history: "list[dict] | None") -> "list[dict]":
    """API'den gelen geçmişi güvenli hale getirir: yalnız user/assistant + str
    içerik, son 4 mesaj (2 soru + 2 cevap sliding window)."""
    out = []
    for m in history or []:
        if (isinstance(m, dict) and m.get("role") in ("user", "assistant")
                and isinstance(m.get("content"), str) and m["content"].strip()):
            out.append({"role": m["role"], "content": m["content"].strip()})
    return out[-4:]


def _translate_query(question: str, hist: "list[dict]") -> str:
    """LLM çevirmen çağrısı: jargon -> resmi arama terimi (+ geçmiş bağlamı)."""
    if hist:
        # Uzun asistan cevapları modeli dağıtır; kısaltılır, kullanıcı soruları tam.
        def _satir(m):
            icerik = m["content"] if m["role"] == "user" else m["content"][:150]
            return f"{'Kullanıcı' if m['role'] == 'user' else 'Asistan'}: {icerik}"
        convo = "\n".join(_satir(m) for m in hist)
        user = (f"Sohbet geçmişi (YALNIZ bağlam için):\n{convo}\n\n"
                f"YENİ SORU: {question}\n\n"
                "Yeni sorunun ANA KONUSUNU koru (örn. 'yaz okulu'); geçmişi "
                "sadece eksik bağlamı ('peki', zamirler) tamamlamak için kullan. "
                "Arama için resmi, bağımsız tek bir soru yaz:")
    else:
        user = f"Kullanıcının sorusu: {question}\n\nArama için resmi, bağımsız soru:"
    model = os.getenv("OLLAMA_LLM_MODEL", "qwen2.5:7b-instruct")
    resp = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": CONDENSE_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        # Condense çıktısı kısa bir "yeniden yazılmış soru"dur; 96 token yeter.
        # Takip sorularındaki 2. LLM çağrısını (gecikme ikiye katlıyordu) kısaltır.
        options={"temperature": 0, "num_predict": 96},
    )
    rewritten = resp["message"]["content"].strip()
    # Model tek satır yazmalı; yine de çok satır dönerse son anlamlı satırı al.
    if "\n" in rewritten:
        satirlar = [s.strip() for s in rewritten.splitlines() if s.strip()]
        rewritten = satirlar[-1] if satirlar else rewritten
    rewritten = re.sub(
        r'(?i)^resmi(?:\s+ve\s+eksiksiz)?\s+arama\s+terimi\s*:\s*["“”]*',
        "",
        rewritten,
    ).strip(' "\'“”')
    return rewritten or question


def _deterministic_rewrite(question: str) -> "str | None":
    """LLM çevirmeninin sık bozduğu kısa kampüs jargonlarını sabitler."""
    n = normalize_turkish(question)
    raw = question.lower()
    if "obs" in n and any(k in n for k in ("girem", "sifre", "parola", "calism")):
        return "OBS erişim ve şifre sorunu nasıl çözülür?"
    # "nasıl alırım/oluştururum" (hesap kaydı niyeti) ile "bağlanamıyorum/koptu"
    # (arıza niyeti) AYRIŞTIRILIR — aksi halde kayıt sorusu yanlışlıkla arıza
    # sorusuna çevrilip retrieval yanlış (eski jargon) chunk'a kayar.
    net_baglam = "eduroam" in n or "wifi" in n or "kablosuz" in n or "netten koptum" in n or "internet" in n
    if net_baglam and any(k in n for k in ("baglanam", "kop", "calismiyor", "calismaz")):
        return "Eduroam Wi-Fi bağlantı sorunu nasıl çözülür?"
    if net_baglam and any(k in n for k in ("sifre", "hesap", "kayit", "nasil alir", "olustur")):
        return "Eduroam kablosuz internetine nasıl bağlanılır, hesap nasıl alınır?"
    if ("çan" in raw or "can egrisi" in n
            or ("hoca" in n and "not" in n and any(k in n for k in ("yukselt", "artir")))):
        return "Bağıl değerlendirme çan eğrisi ve not yükseltme nasıl uygulanır?"
    if "okuldan atil" in n or "ilisik kes" in n:
        return "İlişik kesme şartları nelerdir?"
    if "ortalamam" in n and any(k in n for k in ("yetm", "dusuk", "kotu")):
        return "GANO yetersizliği durumunda ne olur?"
    # Sosyal/Mezuniyet (Learning fazı): 7B çevirmen bu ikisini bozuyordu
    # ("Çalışma Sınavı Polikiliği", "GANO başvurusı") — sabitle.
    if ("part time" in n or "part-time" in raw or "kismi zamanli" in n) and "calis" in n:
        return "Kısmi zamanlı öğrenci çalışma başvurusu nasıl yapılır?"
    if "diploma" in n and any(k in n for k in ("kaybet", "kayip", "ikinci nusha", "yenisi")):
        return "İkinci nüsha diploma işlemleri nasıl yapılır?"
    # "üniversiteyi kazandım" / "kesin kayıt" — 7B çevirmen bunu anlamsız bir
    # şeye çeviriyordu (örn. "hangi bakanlıktan yola çıkmalı?"), retrieval'ı
    # zehirleyip alakasız cevaba yol açıyordu (2026-07-21 canlı bug).
    if ("kazandim" in n and "universite" in n) or "kesin kayit" in n:
        return "Kesin kayıt işlemleri nasıl ve nereden yapılır?"
    # Rektör kimliği: kısa/argo ifadeler ("rektör kim", "ismini ver") SSS'deki
    # resmi soru cümlesiyle zayıf embedding benzerliği kuruyordu (skor ~0.51,
    # eşik altı) — canonical soruya sabitlenince retrieval güvenilir hale gelir
    # (2026-07-22 canlı bug, aynı desen: OBS/eduroam/kesin-kayıt'ta önceden çözüldü).
    # NOT: "isim" DEĞİL "ism" aranıyor — Türkçe ünlü düşmesi ("isim" -> "ismi",
    # "ismini") yüzünden iyelik eki alınca sözlük hali alt-dize olarak kaybolur.
    if "rektor" in n and any(k in n for k in ("kim", "ism", "kimdir")):
        return "Gaziantep Üniversitesi Rektörü kimdir? Rektörlük yönetimi hakkında bilgi verir misin?"
    # e-Devlet mezun belgesi: LLM çevirisi tutarsızdı (bazen iyi bazen "e-Devlet
    # mezuniyet belgesi tarihinde ne olacak?" gibi zayıf retrieval eşleşen bir
    # parafraz üretip cache'e sıkışıyordu, 2026-07-22 canlı stress test bug'ı).
    if "devlet" in n and any(k in n for k in ("mezun", "belge")) and any(k in n for k in ("ne zaman", "cikar", "cikacak")):
        return "Mezuniyet belgemi e-Devlet'ten alabilir miyim?"
    # "Ders seçimi yapmazsam atılır mıyım": LLM çevirisi konuyu "kaç ders
    # kaldım" a kaydırıp retrieval'ı yanlış yöne itti, model de bağlamsız
    # spekülatif metin üretti (2026-07-22, 102 soruluk stress test bug'ı).
    if "ders sec" in n and ("atil" in n or "okuldan" in n):
        return "Ders seçimi yapmazsam üniversiteden anında atılır mıyım?"
    return None


def condense_question(question: str, history: "list[dict] | None") -> str:
    """Öğrenci jargonunu resmi arama terimine çevirir (+ takipte bağlam çözer).

    * Geçmişsiz (tek mesaj): jargon çevirisi yapılır ve sonuç CACHE'lenir
      (tekrarlarda LLM'i yormaz — Faz 9 hız felsefesi korunur). Örn.
      'çan var mı' -> 'Bağıl değerlendirme sistemi nasıl uygulanır?'.
    * Geçmişli: bağlam + jargon çevirisi her seferinde taze yapılır
      (konuşmaya özel, cache'lenmez).
    """
    hist = _sanitize_history(history)
    deterministic = _deterministic_rewrite(question)
    if deterministic:
        return deterministic
    if not hist:
        # cond:v3 — v2 cache'inde 'e-Devlet mezuniyet belgesi tarihinde ne
        # olacak?' gibi zayıf-retrieval üreten bir çeviri sıkışıp kalmıştı
        # (2026-07-22); versiyon atlaması tüm eski çevirileri geçersiz kılar.
        ckey = "cond:v3:" + normalize_turkish(question)
        cached = CACHE.get(ckey)
        if cached is not None:
            return cached
        translated = _guard_translation(question, _translate_query(question, []))
        CACHE.set(ckey, translated, expire=STATIC_TTL)
        return translated
    return _guard_translation(question, _translate_query(question, hist))


def _guard_translation(question: str, translated: str) -> str:
    """LLM çevirisi orijinal soruyla HİÇ kök paylaşmıyorsa raydan çıkmıştır
    (halüsinasyon çeviri) — orijinal soruya geri dön. Meşru sıfır-örtüşme
    çeviriler (çan, netten koptum...) zaten _deterministic_rewrite'ta."""
    if translated == question:
        return translated
    q_tokens = _qa_match_tokens(question)
    t_tokens = _qa_match_tokens(translated)
    if q_tokens and t_tokens and not any(_has_stem(t, q_tokens) for t in t_tokens):
        log.info("Çeviri raydan çıktı (%r) — orijinal soru kullanılacak", translated)
        return question
    return translated


# ---------------------------------------------------------------------------
# Öğrenme deposu — 👍/👎 ile kendini geliştirme (Faz 16)
# ---------------------------------------------------------------------------

# Bu kalıpları içeren cevaplar ÖĞRENİLMEZ (promote edilmez): olumsuz/yönlendirme
# cevaplarını pekiştirmek anlamsız ve zararlı olur.
_NON_PROMOTABLE = (
    "bilmiyorum", "bulamad", "bulunmamakta", "bir hata olu",
    "resmi sayfa", "ziyaret edebilir",
)

# DATA-POISONING eşikleri (.env ile ayarlanır): bir öğrenilmiş bilgi ancak EN AZ
# bu kadar FARKLI IP'den 👍 alınca aktif olur; approval_score bu değere düşünce
# otomatik silinir. Tek 👍 ile aktive etme KALDIRILDI.
LEARN_APPROVAL_THRESHOLD = int(os.getenv("LEARN_APPROVAL_THRESHOLD", "3"))
LEARN_REMOVE_SCORE = int(os.getenv("LEARN_REMOVE_SCORE", "-2"))


def _learning_signature(question: str) -> "tuple[str, set[str]]":
    """(normalize edilmiş tam soru, içerik token seti) — eşleştirme imzası."""
    return normalize_turkish(question or "").strip(), _qa_match_tokens(question or "")


def _is_promotable_answer(answer: str) -> bool:
    """Cevap öğrenilmeye değer mi (olumlu, bilgi içeren)? Kısa/olumsuz/
    yönlendirme cevapları pekiştirilmez."""
    if not answer or len(answer.strip()) < 15:
        return False
    low = normalize_turkish(answer)
    return not any(p in low for p in _NON_PROMOTABLE)


def find_learned_answer(question: str) -> "str | None":
    """Soru daha önce ONAYLANMIŞ (≥ eşik farklı-IP 👍) bir öğrenilmiş cevapla
    GÜÇLÜ örtüşüyorsa onu döndürür. YALNIZ AKTİF kayıtlar taranır (onaylanmamış
    aday cevaplar SUNULMAZ — data-poisoning koruması). Örtüşme eşiği sıkıdır:
    tam normalize eşleşme YA DA Jaccard ≥ 0.6 ve ≥2 ortak içerik token'ı."""
    q_norm, q_tokens = _learning_signature(question)
    if not q_norm:
        return None
    try:
        rows = analytics.active_learnings()   # yalnız onaylanmış (active=1)
    except Exception as exc:
        log.warning("Öğrenme deposu okunamadı: %s", exc)
        return None
    # Sorgunun AYIRT EDİCİ (en uzun, ≥4 harf) token'ı — konuyu belirler. Bulanık
    # eşleşmede bu token adayda da OLMALI; yoksa "mongodb ne işe yarar" gibi
    # şablon sorular "redis ne işe yarar" öğrenmesine yanlış eşleşiyordu (4 ortak
    # şablon kelime → yüksek Jaccard). Konu token'ı farklıysa reddet.
    distinctive = max((t for t in q_tokens if len(t) >= 4), key=len, default=None)
    best, best_score = None, 0.0
    for r in rows:
        if r["question_norm"] == q_norm:  # birebir → kesin eşleşme
            best, best_score = r, 1.0
            break
        toks = set((r["tokens"] or "").split())
        ortak = q_tokens & toks
        if len(ortak) < 2:
            continue
        # Ayırt edici konu token'ı adayda yoksa (farklı konu) atla.
        if distinctive is not None and distinctive not in toks:
            continue
        union = q_tokens | toks
        jac = len(ortak) / len(union) if union else 0.0
        if jac > best_score:
            best, best_score = r, jac
    if best and best_score >= 0.6:
        try:
            analytics.bump_learning_hit(best["question_norm"])
        except Exception:
            pass
        log.info("Öğrenilmiş cevap kullanıldı (skor %.2f): %r", best_score, question)
        return best["answer"]
    return None


def record_feedback(log_id: int, score: int, ip: str = "?") -> bool:
    """👍/👎 skorunu kaydeder VE data-poisoning korumalı öğrenme oyunu işler.

    Bir bilgi ancak EN AZ LEARN_APPROVAL_THRESHOLD (3) FARKLI IP'den 👍 alınca
    aktifleşir (sunulur); approval_score LEARN_REMOVE_SCORE'a (-2) düşünce otomatik
    silinir. IP başına tek oy (DB düzeyinde) — tek IP eşiği aşamaz. `ip` sunucudan
    (gerçek istemci IP'si) gelir. Sunucular feedback için bunu çağırır."""
    ok = analytics.update_feedback(log_id, score)
    try:
        entry = analytics.get_log(log_id)
        if not entry:
            return ok
        q_norm, q_tokens = _learning_signature(entry.get("user_question") or "")
        answer = entry.get("bot_answer") or ""
        # Yalnız YETERİNCE SPESİFİK sorular (≥2 içerik token'ı) öğrenilebilir;
        # olumsuz/yönlendirme cevapları aday OLMAZ. 👎 var olan adaya olumsuz oy
        # ekler (yeni aday yaratmaz — vote_learning bunu garanti eder).
        if not q_norm or len(q_tokens) < 2:
            return ok
        if score == 1 and not _is_promotable_answer(answer):
            return ok
        res = analytics.vote_learning(
            q_norm, ip, score,
            tokens=" ".join(sorted(q_tokens)),
            user_question=entry.get("user_question") or "", answer=answer,
            threshold=LEARN_APPROVAL_THRESHOLD, remove_at=LEARN_REMOVE_SCORE)
        log.info("Öğrenme oyu: %r ip=%s oy=%+d → up=%d down=%d aktif=%s gizli=%s",
                 entry.get("user_question"), ip, score, res["upvotes"],
                 res["downvotes"], res["active"], res.get("hidden"))
    except Exception as exc:
        log.warning("Öğrenme oyu başarısız: %s", exc)
    return ok


def _run(question: str, history: "list[dict] | None" = None) -> dict:
    """Orkestrasyonun tek çıkışlı çekirdeği + telemetri kaydı (Faz 15).

    {answer, log_id, intent, rewritten_question, response_time_ms} döndürür.
    Yönlendirme mantığı (Faz 9/12/14) korunmuştur; yalnızca tek çıkış + log eklendi.
    """
    _ensure_logging()
    load_dotenv(".env")
    _configure_ollama_host()  # OLLAMA_URL'i etkin kıl (env yüklendikten sonra)
    t0 = time.perf_counter()
    cfg = Config()
    hist = _sanitize_history(history)
    rewritten = question

    # Aşama A0 — STATİK GÜVENLİK KALKANI (Prompt Injection / Jailbreak).
    # HER ŞEYDEN önce, tek regex taramasıyla (O(1)-benzeri) çalışır. Enjeksiyon
    # kalıbı varsa istek EMBEDDING'e ve LLM'e HİÇ ulaşmaz — deterministik ret,
    # ~0.1 sn. Kırmızı-Takım BULGU-1 (2026-07-29): bu tip "önceki talimatları
    # unut/korsansın/şifreleri ver" injection'ı eskiden ~71 sn'lik tüm
    # RAG→canlı→web→fallback zincirini tetikliyordu (DoS) ve reddi tamamen
    # qwen'in hizalamasına bağlıydı. Telemetriye 'injection_attempt' etiketiyle
    # yazılır (analytics.knowledge_gaps'i kirletmez — general_knowledge değil).
    if contains_injection_attempt(question):
        intent = "injection_attempt"
        log.warning("GÜVENLİK KALKANI — enjeksiyon/jailbreak kalıbı reddedildi: %r",
                    question[:80])
        result = INJECTION_REFUSAL
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log_id = None
        try:
            log_id = analytics.log_chat(
                user_question=question, rewritten_question=rewritten,
                intent_route=intent, response_time_ms=elapsed_ms, bot_answer=result)
        except Exception as exc:
            log.warning("Telemetri kaydı başarısız: %s", exc)
        return {"answer": result, "log_id": log_id, "intent": intent,
                "rewritten_question": rewritten, "response_time_ms": elapsed_ms}

    # Aşama A — GÜVENLİK REDDİ: sisteme sızma/not değiştirme gibi taleplerde
    # her şeyin ÖNÜNE geçer (LLM/RAG/condense hiç devreye girmez). Bu kontrol
    # koda yazılıdır çünkü retrieval'ın doğru chunk'ı bulmasına güvenilemez
    # (2026-07-22, 102 soruluk stress testte bu soruyu retrieval kaçırmıştı).
    if is_unsafe_request(question):
        intent = "refused"
        log.info("Güvenlik reddi — sisteme sızma/not değiştirme talebi tespit edildi")
        result = UNSAFE_REQUEST_REFUSAL
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log_id = None
        try:
            log_id = analytics.log_chat(
                user_question=question, rewritten_question=rewritten,
                intent_route=intent, response_time_ms=elapsed_ms, bot_answer=result)
        except Exception as exc:
            log.warning("Telemetri kaydı başarısız: %s", exc)
        return {"answer": result, "log_id": log_id, "intent": intent,
                "rewritten_question": rewritten, "response_time_ms": elapsed_ms}

    # Aşama B — ÖĞRENME DEPOSU: daha önce 👍 alınmış, güçlü-eşleşen bir soru
    # varsa o güvenilir cevabı doğrudan ver (kendini geliştirme). Her 👍/👎
    # depoyu günceller (bkz. record_feedback). Eşik sıkı olduğu için alakasız
    # sorulara yanlış cevap sunma riski düşüktür.
    learned = find_learned_answer(question)
    if learned is not None:
        intent = "learned"
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log_id = None
        try:
            log_id = analytics.log_chat(
                user_question=question, rewritten_question=rewritten,
                intent_route=intent, response_time_ms=elapsed_ms, bot_answer=learned)
        except Exception as exc:
            log.warning("Telemetri kaydı başarısız: %s", exc)
        return {"answer": learned, "log_id": log_id, "intent": intent,
                "rewritten_question": rewritten, "response_time_ms": elapsed_ms}

    # Aşama D — SOHBET: ORİJİNAL soru üzerinde, condense'DEN ÖNCE yakalanır
    # (yoksa "teşekkürler" gibi kapanışlar condense ile bozulur). Arama yok.
    original_intent = classify_intent(question)
    if original_intent == "chitchat":
        intent = "chitchat"
        log.info("Sohbet (Chitchat) — DB/RAG/canlı/cache atlandı")
        resp = ollama.chat(
            model=cfg.llm_model,
            messages=[
                {"role": "system", "content": CHITCHAT_SYSTEM_PROMPT},
                *hist,
                {"role": "user", "content": question},
            ],
            options={"temperature": 0.3},
        )
        result = resp["message"]["content"].strip()
    else:
        if original_intent == "structural":
            # Kişi/telefon sorularında LLM condense adımı "kimdir/numara" gibi
            # router sinyallerini bozabilir. Önce orijinal soruyla SQL'e git;
            # SQL boşsa answer_question içindeki zincirleme RAG fallback çalışır.
            condensed = question
            search_query = question
            intent = original_intent
        elif original_intent == "general_knowledge":
            # HIZLI YOL: üniversiteyle ilgisiz genel/teknik soru. Condense de
            # (jargon→GAÜN çevirisi) gereksiz kaynak israfıdır → ham soru
            # doğrudan answer_question üzerinden web'e gider (RAG atlanır).
            condensed = question
            search_query = question
            intent = original_intent
        elif is_location_question(question):
            # Konum soruları: LLM'in "jargon çevirisi" özel isimleri (KYK
            # Şahinbey, SPORIUM, Merkezi Derslikler...) genel bir ifadeye
            # eritip retrieval'ın asıl sinyalini siliyordu — ham soru
            # doğrudan embed edildiğinde 0.79 skorla eşleşirken, condense
            # sonrası "kampüs yolu ve alanları hakkında..." gibi anlamsız bir
            # metne dönüşüp retrieval'ı tamamen kaçırıyordu (2026-07-22 canlı
            # bug). Konum soruları ham haliyle daha güvenilir eşleşir.
            condensed = question
            search_query = question
            intent = original_intent
        else:
            # Gerçek semantik/canlı soru: jargon çevirisi + takipte bağlam çözme.
            condensed = condense_question(question, hist)
            if condensed != question:
                log.info("Sorgu yeniden yazıldı (jargon→resmi): %r -> %r", question, condensed)
            # Retrieval/sınıflandırma sorgusu: takipte önceki kullanıcı sorusuyla
            # DETERMİNİSTİK zenginleştirilir; üretim yine temiz condensed'i kullanır.
            search_query = condensed
            if hist:
                onceki = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
                # Önceki soru (bağlam) + ORİJİNAL yeni soru (yeni özne garantisi) +
                # condensed (jargon çevirisi): condense konu düşürse bile retrieval sağlam.
                search_query = f"{onceki} {question} {condensed}".strip()

            intent = classify_intent(search_query)
            # Condense GAÜN anahtar kelimelerini düşürüp general_knowledge'a
            # çevirmesin: ham soru GAÜN ise (original_intent semantic) fast-track'e
            # DÜŞÜRME — yerel RAG yolunda kalsın (regresyon koruması).
            if intent == "general_knowledge" and original_intent != "general_knowledge":
                intent = "semantic"
        rewritten = condensed

        if intent == "live":
            # Aşama C — CANLI VERİ: Qdrant/cache'e HİÇ bakma, siteden anlık çek.
            topic = live_topic(search_query)
            now = now_turkey()  # Türkiye saati (sunucu TZ'sinden bağımsız)
            nl = normalize_turkish(f"{search_query} {condensed}")
            # BUGÜNKÜ yemek: tarih ve menü LLM'e bırakılmaz — KODDA deterministik
            # çıkarılır (LLM günü menü tablosundan yanlış seçiyordu: "24 Temmuz
            # Cumartesi" oysa Cuma; 2026-07-24). "yarın" geçmiyorsa bugün kabul.
            result = None
            if topic == "yemek" and "yarin" not in nl:
                raw = fetch_todays_menu_raw()
                items = parse_todays_menu(raw, now) if raw else []
                if items:
                    log.info("Bugünkü yemek DETERMİNİSTİK çıkarıldı — %d kalem", len(items))
                    liste = "\n".join(f"- {it}" for it in items)
                    result = (f"{turkish_date(now)} yemek menüsü:\n{liste}\n\n"
                              f"Her günün tam menüsü için {LIVE_SOURCES['yemek']} "
                              f"adresini ziyaret edebilirsiniz.")
            if result is None:
                # Diğer canlı sorgular (yarın/haftalık yemek, duyuru): LLM yolu.
                data = fetch_live_data(topic, condensed)
                log.info("Canlı Web'den çekildi (Live Fetch) — konu=%s, kaynak=%s",
                         topic, data.get("source_url"))
                payloads = [data] if data.get("document") else []
                bugun_str = turkish_date(now)
                yarin_str = turkish_date(now + timedelta(days=1))
                onbilgi = (
                    f"Bugünün tarihi KESİN olarak: {bugun_str}. "
                    f"Yarının tarihi KESİN olarak: {yarin_str}. "
                    f"Cevabında günü/tarihi belirtirken SADECE bu değerleri kullan — "
                    f"menü tablosundaki gün sütunlarından KENDİN gün seçme. "
                    f"Kullanıcı 'yarın' dediyse {yarin_str} gününün yemeklerini ver; "
                    f"başlıkta da aynen bu tarihi/günü yaz.")
                context = f"{onbilgi}\n{format_context(payloads)}"
                result = generate(cfg, context, condensed, SYSTEM_PROMPT, hist)
        else:
            # STATİK (yapısal/semantik). Cache YALNIZ geçmişsiz sorularda.
            use_cache = not hist
            key = ANSWER_CACHE_PREFIX + normalize_turkish(condensed)
            cached = CACHE.get(key) if use_cache else None
            if cached is not None:
                log.info("Önbellekten getirildi (Cache Hit)")
                result = cached
            else:
                if use_cache:
                    log.info("Yeni üretildi (Cache Miss)")
                try:
                    conn = connect_db(cfg)
                    # Tembel + zaman aşımlı istemci (S4): DB kapalıysa ~3 sn'de
                    # patlar, 90 sn thread bloklamaz.
                    client = get_qdrant_client(cfg)
                    try:
                        result = answer_question(cfg, conn, client, condensed, hist, search_query,
                                                  raw_question=question)
                    finally:
                        conn.close()
                    if use_cache:
                        CACHE.set(key, result, expire=STATIC_TTL)
                except Exception as exc:  # DB/altyapı kesintisi → zarif fallback
                    log.warning("Bilgi tabanı erişilemedi (%s): %s",
                                type(exc).__name__, exc)
                    result = DB_UNAVAILABLE_FALLBACK

    # Harita kuralı: konum sorusuysa KESİNLİKLE ve İSTİSNASIZ eklenir — LLM'e
    # bırakılmaz (7B unutabilir), kod her zaman ekler. Chitchat hariç tüm
    # yollarda (yapısal/semantik/canlı, cache'ten gelse bile) uygulanır.
    if intent != "chitchat":
        result = append_map_link_if_needed(question, result)

    # --- Telemetri (yan-kanal; hata olsa bile cevabı düşürmez) ---
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    log_id = None
    try:
        log_id = analytics.log_chat(
            user_question=question, rewritten_question=rewritten,
            intent_route=intent, response_time_ms=elapsed_ms, bot_answer=result)
    except Exception as exc:  # telemetri asla ana akışı bozmasın
        log.warning("Telemetri kaydı başarısız: %s", exc)

    return {"answer": result, "log_id": log_id, "intent": intent,
            "rewritten_question": rewritten, "response_time_ms": elapsed_ms}


def answer(question: str, history: "list[dict] | None" = None) -> str:
    """Hibrit orkestratör — cevabı düz metin döndürür (CLI/testler).

    Kullanım: python -c "import bot; print(bot.answer('...'))"
    """
    return _run(question, history)["answer"]


def answer_with_telemetry(question: str, history: "list[dict] | None" = None) -> dict:
    """API için: cevap + log_id (feedback ilişkilendirmesi) + meta döndürür."""
    return _run(question, history)


def chat_with_gaunai(question: str, history: "list[dict] | None" = None) -> str:
    """Geriye dönük kolay giriş: eski test komutları için alias."""
    return answer(question, history)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_ask(cfg: Config, question: str) -> int:
    conn = connect_db(cfg)
    client = get_qdrant_client(cfg)
    try:
        print(answer_question(cfg, conn, client, question))
    finally:
        conn.close()
    return 0


def cmd_repl(cfg: Config) -> int:
    conn = connect_db(cfg)
    client = get_qdrant_client(cfg)
    print("GAÜN asistanı (çıkış: Ctrl-D / 'exit'). Model:", cfg.llm_model)
    try:
        while True:
            try:
                question = input("\n> ").strip()
            except EOFError:
                print()
                return 0
            if question.lower() in {"exit", "quit", "çık"}:
                return 0
            if not question:
                continue
            try:
                print(answer_question(cfg, conn, client, question))
            except Exception as exc:  # REPL'i tek soru hatası düşürmesin
                log.error("Hata: %s", exc)
    finally:
        conn.close()


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    load_dotenv()
    _configure_ollama_host()  # OLLAMA_URL'i etkin kıl (env yüklendikten sonra)

    ap = argparse.ArgumentParser(description="GAÜN Hibrit RAG asistanı")
    sub = ap.add_subparsers(dest="command")
    askonu = sub.add_parser("ask", help="Tek soru sor")
    askonu.add_argument("question", help="Soru metni")
    args = ap.parse_args(argv)

    cfg = Config()
    if args.command == "ask":
        return cmd_ask(cfg, args.question)
    return cmd_repl(cfg)


if __name__ == "__main__":
    sys.exit(main())
