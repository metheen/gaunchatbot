#!/usr/bin/env python3
"""GAUN Chatbot — Mikro-Pilot Rehber Crawler (Faz 2).

Hedef: https://rehber.gaziantep.edu.tr/ — CANLI SİTEDE DOĞRULANAN kurallar:
  * Site form POST ile çalışır: payload = {"kelime": "<arama>", "arama": ""}
    DİKKAT: tek harfli arama (örn. 'a') sunucu tarafında BOŞ sonuç döndürür;
    pilot tarama en az 3 karakterli seed köklerle yapılır.
  * Sonuçlu cevapta 5 <table> vardır; personel verisi tables[3]'tedir
    (sonuçsuz cevapta 4 tablo olur ve hepsi boilerplate'tir).
  * Başlık satırı güvenilmez; doğrudan <tr>/<td> gezilir. Geçerli personel
    satırı TAM 5 <td> içerir ve alan ayrımı hücre İÇİ etiketlerden yapılır:
      td[0] sıra no | td[1] <i>Unvan</i> AD | td[2] SOYAD
      td[3] dahili  | td[4] <b>ÜST BİRİM</b> <i>ALT BİRİM</i>

Kayıt mantığı: Üst Birim -> departments (parent), Alt Birim -> departments
(child, parent_id bağlı); personel alt birime, alt birim yoksa üst birime
bağlanır. external_key = sha1(kaynak URL + search_name + birim bağlamı) —
birim bağlamı, farklı birimlerdeki aynı isimli kişilerin çakışmasını önler;
arama seed'i anahtara dahil değildir.

Güvenlik modeli (değişmedi):
  * Varsayılan mod DRY-RUN — veritabanına YAZMAZ. Yazma için --write zorunlu.
  * robots.txt'e uyum; POST istekleri arasında rastgele 1.5-3 sn bekleme.
  * Sırlar .env'den (python-dotenv); SQL her zaman prepared statement.

Kullanım:
  python crawler_rehber.py                                          # .env REHBER_URLS + SEED_LIST, dry-run
  python crawler_rehber.py --url https://rehber.gaziantep.edu.tr/    # explicit URL, dry-run
  python crawler_rehber.py --html-file tests/fixtures/rehber_ornek.html
  python crawler_rehber.py --write                                  # gerçek yazma

NOT: MariaDB host'a port açmadığı için --write modu ancak gaun_network
içindeki bir konteynerden çalışır (bkz. docs/faz1/kurulum-runbook.md).
"""

import argparse
import hashlib
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import mysql.connector
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Türkçe metin normalizasyonu tek evde (rag_pipeline); crawler, router ve arama
# aynı katlama kuralını paylaşır. İsimler geriye dönük uyumluluk için mevcut
# testler crawler_rehber'den de import edebilir.
from rag_pipeline import normalize_turkish, slugify

log = logging.getLogger("crawler_rehber")

# Personel tablosunun dönen HTML'deki sırası (tables[3] = 4. tablo).
STAFF_TABLE_INDEX = 3
# Geçerli personel satırındaki hücre sayısı — farklıysa satır atlanır.
STAFF_ROW_CELLS = 5
# GAÜN rehberinde minimum 3 karakterli POST aramaları için yaygın ad/soyad kökleri.
SEED_LIST = (
    "can", "han", "nur", "tan", "er ", "din", "gül", "taş",
    "sen", "ali", "ata", "soy", "kan", "ak ", "öz ", "yil",
    "yilm", "kurt", "kaya", "demi", "çeli", "şahi", "yild",
    "öztü", "aydi", "özde", "arsl", "doğa", "kili", "asla", "çeti",
)
REQUEST_SLEEP_RANGE = (1.5, 3.0)

# ---------------------------------------------------------------------------
# Saf fonksiyonlar (yan etkisiz — testler doğrudan import eder)
# ---------------------------------------------------------------------------

def build_external_key(profile_url: Optional[str], source_url: str,
                       search_name: str, birim_context: str = "") -> str:
    """Upsert anahtarı: profil URL'i varsa o, yoksa deterministik hash.

    birim_context, aynı isimli iki farklı kişinin (farklı birimlerde)
    tek kayda çakışmasını önler. Kişi birim değiştirirse anahtar değişir;
    eski kayıt is_active süpürmesiyle pasife düşer (bilinçli tercih).
    """
    if profile_url:
        return profile_url[:255]
    raw = f"{source_url}|{search_name}|{normalize_turkish(birim_context)}"
    return "rehber:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def clean_phone(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    cleaned = re.sub(r"[^0-9+() -]", "", raw).strip()
    return cleaned[:30] if any(ch.isdigit() for ch in cleaned) else None


# Akademik unvan ipuçları (normalize edilmiş token ÖN EKLERİ):
# 'prof.', 'doc.', 'dr.', 'ogr.', 'ars.', 'yrd.' ile başlayan token akademiktir.
_ACADEMIC_PREFIXES = ("prof", "doc", "dr", "ogr", "ars", "yrd")


def split_title(unvan: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Rehberdeki tek 'Unvan' alanını (academic_title, role_title) çiftine ayırır.

    'Prof. Dr.' -> academic_title; 'Daire Başkanı', 'Müdür' -> role_title.
    Token ön eki kontrolü kullanılır ki 'Müdür'/'Kadrolu' gibi kelimelerin
    içindeki 'dr' yanlış eşleşmesin.
    """
    if not unvan:
        return None, None
    tokens = normalize_turkish(unvan).split()
    if any(tok.startswith(pfx) for tok in tokens for pfx in _ACADEMIC_PREFIXES):
        return unvan, None
    return None, unvan


@dataclass
class StaffRecord:
    full_name: str
    academic_title: Optional[str]
    role_title: Optional[str]
    ust_birim: Optional[str]
    alt_birim: Optional[str]
    phone_internal: Optional[str]
    email: Optional[str]
    profile_url: Optional[str]
    source_url: str


def record_external_key(rec: StaffRecord) -> str:
    """StaffRecord için upsert anahtarını tek kaynaklı üretir."""
    search_name = normalize_turkish(rec.full_name)
    birim_context = f"{rec.ust_birim or ''} {rec.alt_birim or ''}"
    return build_external_key(rec.profile_url, rec.source_url, search_name, birim_context)


def parse_rehber_html(html: str, source_url: str) -> "list[StaffRecord]":
    """rehber.gaziantep.edu.tr POST cevabındaki personel tablosunu ayrıştırır.

    Canlı sitede doğrulanan yapı: personel verisi tables[3]'te, geçerli satır
    TAM 5 <td>. Unvan/ad ayrımı td[1] içindeki <i> etiketinden, üst/alt birim
    ayrımı td[4] içindeki <b>/<i> etiketlerinden yapılır — metin sezgisi yok.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if len(tables) <= STAFF_TABLE_INDEX:
        # Sonuçsuz cevap 4 tablo döndürür; 5.'nin yokluğu "sonuç yok" ya da
        # şablon değişikliği sinyalidir — üst katman alarm üretir.
        log.warning("Sayfada %d tablo var, en az %d bekleniyordu — sonuç yok veya şablon değişti.",
                    len(tables), STAFF_TABLE_INDEX + 1)
        return []

    records = []
    atlanan = 0
    for row in tables[STAFF_TABLE_INDEX].find_all("tr"):
        cells = row.find_all("td")
        if len(cells) != STAFF_ROW_CELLS:
            if cells:  # boş satırlar sessiz, bozuk td satırları sayılır
                atlanan += 1
            continue

        # td[1]: <i>Unvan</i> AD — <i> koparılınca hücrede kalan metin addır.
        unvan = None
        unvan_el = cells[1].find("i")
        if unvan_el is not None:
            unvan = unvan_el.get_text(" ", strip=True) or None
            unvan_el.extract()
        ad = cells[1].get_text(" ", strip=True)
        soyad = cells[2].get_text(" ", strip=True)
        full_name = " ".join(part for part in (ad, soyad) if part).strip()
        if not full_name:
            atlanan += 1
            continue

        # td[4]: <b>ÜST BİRİM</b> <i>ALT BİRİM</i> — etiket yoksa düz metin
        # üst birim sayılır (savunmacı geri düşüş).
        ust_el = cells[4].find("b")
        alt_el = cells[4].find("i")
        alt_birim = (alt_el.get_text(" ", strip=True) or None) if alt_el else None
        if ust_el is not None:
            ust_birim = ust_el.get_text(" ", strip=True) or None
        else:
            ust_birim = cells[4].get_text(" ", strip=True) or None

        academic_title, role_title = split_title(unvan)
        records.append(StaffRecord(
            full_name=full_name,
            academic_title=academic_title,
            role_title=role_title,
            ust_birim=ust_birim,
            alt_birim=alt_birim,
            phone_internal=clean_phone(cells[3].get_text(" ", strip=True)),
            email=None,          # rehber tablosunda e-posta sütunu yok
            profile_url=None,    # rehber tablosunda profil linki yok
            source_url=source_url,
        ))

    if atlanan:
        log.info("%d bozuk/eksik satır atlandı.", atlanan)
    return records


# ---------------------------------------------------------------------------
# Ağ katmanı
# ---------------------------------------------------------------------------

def robots_allows(url: str, user_agent: str) -> bool:
    """robots.txt kuralına mutlak uyum; dosyaya ulaşılamıyorsa uyarıp devam eder."""
    parts = urlparse(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
    except OSError as exc:
        log.warning("robots.txt okunamadı (%s): %s — devam ediliyor", robots_url, exc)
        return True
    return parser.can_fetch(user_agent, url)


def fetch(url: str, user_agent: str, payload: dict) -> str:
    """Rehber arama formunu POST eder, HTML döndürür."""
    resp = requests.post(url, data=payload,
                         headers={"User-Agent": user_agent}, timeout=30)
    resp.raise_for_status()
    # Eski TR siteleri charset'i yanlış bildirebiliyor; içerikten tahmine düş.
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding
    return resp.text


# ---------------------------------------------------------------------------
# Veritabanı katmanı (yalnız --write modunda dokunulur)
# ---------------------------------------------------------------------------

# Rehberdeki ad, ileride kadro sayfasından gelecek küratörlü adı/parent'ı
# ezmesin diye ON DUPLICATE'te yalnız last_seen tazelenir.
_UPSERT_DEPARTMENT_SQL = """
INSERT INTO departments (slug, name, dept_type, parent_id, source_url)
VALUES (%s, %s, 'diger', %s, %s)
ON DUPLICATE KEY UPDATE last_seen_at = CURRENT_TIMESTAMP
"""

_UPSERT_STAFF_SQL = """
INSERT INTO staff
  (external_key, full_name, search_name, academic_title, role_title,
   department_id, phone_internal, email, profile_url, source_url)
VALUES
  (%(external_key)s, %(full_name)s, %(search_name)s, %(academic_title)s,
   %(role_title)s, %(department_id)s, %(phone_internal)s, %(email)s,
   %(profile_url)s, %(source_url)s)
ON DUPLICATE KEY UPDATE
  full_name      = VALUES(full_name),
  search_name    = VALUES(search_name),
  academic_title = VALUES(academic_title),
  role_title     = VALUES(role_title),
  department_id  = VALUES(department_id),
  phone_internal = VALUES(phone_internal),
  email          = VALUES(email),
  profile_url    = VALUES(profile_url),
  source_url     = VALUES(source_url),
  is_active      = 1,
  last_seen_at   = CURRENT_TIMESTAMP
"""


def connect_db() -> "mysql.connector.MySQLConnection":
    password = os.getenv("MARIADB_PASSWORD")
    if not password:
        raise SystemExit("MARIADB_PASSWORD tanımlı değil — .env dosyanı kontrol et.")
    return mysql.connector.connect(
        host=os.getenv("MARIADB_HOST", "mariadb"),
        port=int(os.getenv("MARIADB_PORT", "3306")),
        user=os.getenv("MARIADB_USER", "gaun_app"),
        password=password,
        database=os.getenv("MARIADB_DATABASE", "gaun_assistant"),
        charset="utf8mb4",
    )


def _upsert_department(cur, cache: dict, slug: str, name: str,
                       parent_id: Optional[int], source_url: str) -> int:
    dept_id = cache.get(slug)
    if dept_id is None:
        cur.execute(_UPSERT_DEPARTMENT_SQL, (slug, name, parent_id, source_url))
        cur.execute("SELECT id FROM departments WHERE slug = %s", (slug,))
        dept_id = cur.fetchone()[0]
        cache[slug] = dept_id
    return dept_id


def upsert_records(conn, records: "list[StaffRecord]") -> dict:
    """Kayıtları tek transaction'da upsert eder; istatistik döndürür."""
    stats = {"yeni": 0, "guncellendi": 0, "degismedi": 0}
    dept_cache: dict = {}
    cur = conn.cursor()
    try:
        for rec in records:
            dept_id = None
            if rec.ust_birim:
                dept_id = _upsert_department(
                    cur, dept_cache, slugify(rec.ust_birim), rec.ust_birim,
                    None, rec.source_url)
                if rec.alt_birim:
                    # Alt birim slug'ı üst birimle isimlendirilir: farklı
                    # fakültelerdeki aynı adlı bölümler tek kayda çakışmasın.
                    dept_id = _upsert_department(
                        cur, dept_cache,
                        slugify(f"{rec.ust_birim} {rec.alt_birim}"),
                        rec.alt_birim, dept_id, rec.source_url)

            search_name = normalize_turkish(rec.full_name)
            cur.execute(_UPSERT_STAFF_SQL, {
                "external_key": record_external_key(rec),
                "full_name": rec.full_name,
                "search_name": search_name,
                "academic_title": rec.academic_title,
                "role_title": rec.role_title,
                "department_id": dept_id,
                "phone_internal": rec.phone_internal,
                "email": rec.email,
                "profile_url": rec.profile_url,
                "source_url": rec.source_url,
            })
            # mysql rowcount sözleşmesi: 1 = yeni insert, 2 = güncelleme, 0 = değişiklik yok
            if cur.rowcount == 1:
                stats["yeni"] += 1
            elif cur.rowcount == 2:
                stats["guncellendi"] += 1
            else:
                stats["degismedi"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    load_dotenv()

    ap = argparse.ArgumentParser(
        description="GAUN rehber pilot crawler — POST/tables[3] şablonu (varsayılan: dry-run)")
    kaynak = ap.add_mutually_exclusive_group()
    kaynak.add_argument("--url", action="append",
                        help="Rehber form URL'i (tekrarlanabilir); verilmezse .env REHBER_URLS")
    kaynak.add_argument("--html-file", help="Yerel HTML dosyasından ayrıştır (offline test)")
    ap.add_argument("--write", action="store_true",
                    help="Veritabanına yaz (verilmezse dry-run: yalnız loglar)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Varsayılan davranışı açıkça belirtir; --write ile birlikte kullanılamaz")
    args = ap.parse_args(argv)

    if args.write and args.dry_run:
        ap.error("--write ve --dry-run birlikte kullanılamaz")
    dry_run = not args.write

    records: "list[StaffRecord]" = []
    seen_keys: set[str] = set()
    if args.html_file:
        html = Path(args.html_file).read_text(encoding="utf-8")
        for rec in parse_rehber_html(html, f"file://{args.html_file}"):
            key = record_external_key(rec)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            records.append(rec)
    else:
        urls = args.url or [u.strip() for u in os.getenv("REHBER_URLS", "").split(",") if u.strip()]
        if not urls:
            log.error("Hedef yok: --url ver veya .env'de REHBER_URLS tanımla.")
            return 1
        user_agent = os.getenv("CRAWLER_USER_AGENT", "GaunChatbotCrawler/0.1")

        allowed_urls = []
        for url in urls:
            if not robots_allows(url, user_agent):
                log.error("robots.txt izin vermiyor, atlandı: %s", url)
                continue
            allowed_urls.append(url)
        if not allowed_urls:
            log.error("robots.txt sonrası taranabilir hedef kalmadı.")
            return 1

        seeds = list(dict.fromkeys(SEED_LIST))
        short_seeds = [seed for seed in seeds if len(seed) < 3]
        if short_seeds:
            log.warning("3 karakterden kısa seed'ler atlandı: %s",
                        ", ".join(repr(seed) for seed in short_seeds))
            seeds = [seed for seed in seeds if len(seed) >= 3]

        post_count = 0
        for seed in seeds:
            seed_new = 0
            for url in allowed_urls:
                if post_count:
                    time.sleep(random.uniform(*REQUEST_SLEEP_RANGE))
                post_count += 1

                payload = {"kelime": seed, "arama": ""}
                log.info("POST %s (kelime=%r)", url, seed)
                page_records = parse_rehber_html(fetch(url, user_agent, payload), url)
                if not page_records:
                    log.warning("0 kayıt döndü: %s (kelime=%r)", url, seed)
                for rec in page_records:
                    key = record_external_key(rec)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    records.append(rec)
                    seed_new += 1
            log.info("%r taranıyor... %d yeni kayıt bulundu. Toplam işlenen: %d",
                     seed, seed_new, len(records))

    if not records:
        log.error("Hiç personel kaydı bulunamadı — çıkış kodu 2 (alarm).")
        return 2

    if dry_run:
        for rec in records:
            birim = " / ".join(b for b in (rec.ust_birim, rec.alt_birim) if b) or "-"
            log.info("[DRY-RUN] %-24s | %-14s | %-48s | dahili:%-6s",
                     rec.full_name, rec.academic_title or rec.role_title or "-",
                     birim, rec.phone_internal or "-")
        log.info("Toplam %d kayıt bulundu (dry-run — DB'ye YAZILMADI; yazmak için --write).",
                 len(records))
        return 0

    conn = connect_db()
    try:
        stats = upsert_records(conn, records)
    finally:
        conn.close()
    log.info("Upsert tamam: %d yeni, %d güncellendi, %d değişmedi (toplam %d).",
             stats["yeni"], stats["guncellendi"], stats["degismedi"], len(records))
    return 0


if __name__ == "__main__":
    sys.exit(main())
