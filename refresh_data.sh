#!/bin/bash
#
# GaunAI — Periyodik veri yenileme (güncellik çözümü, 2026-07-22).
#
# Mimariyi DEĞİŞTİRMEZ: sorgu anında hâlâ SQL/küratörlü-RAG kullanılır, hiçbir
# soru sırasında canlı internet çekimi yapılmaz. Bunun yerine, ARKA PLANDA
# periyodik olarak (crontab ile haftada bir) canlı siteden yeniden taranır ve
# yerel veri (MariaDB + Qdrant) tazelenir — "veriler güncel olmayabilir"
# sorununu KÖKTEN çözer, sorgu-anı riskini sıfır tutar.
#
# DOKUNMAZ: offline_data/kampus_master_sss.md, gaunai_egitim_soru_bankasi.md
# (elle doğrulanmış "kutsal kaynak" — yalnız insan güncelleyebilir).
# DOKUNUR: MariaDB staff/departments (crawler_rehber.py), offline_data/scraped_*.md
# (gaun_crawler.py) ve bunların Qdrant gömmeleri.
#
# Kullanım: ./refresh_data.sh   (proje kökünde, .venv hazır olmalı)

set -uo pipefail   # -e YOK: bir adım başarısız olsa da diğer adımlar denensin
cd "$(dirname "$0")"

mkdir -p logs
LOG="logs/refresh_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "=== GaunAI Veri Yenileme — $(date '+%Y-%m-%d %H:%M:%S') ==="

LOCKFILE="/tmp/gaunai_refresh.lock"
if [ -f "$LOCKFILE" ]; then
  echo "UYARI: Önceki yenileme hâlâ sürüyor gibi görünüyor ($LOCKFILE var). Çıkılıyor."
  exit 1
fi
trap 'rm -f "$LOCKFILE"' EXIT
touch "$LOCKFILE"

count_staff() {
  docker exec gaun_mariadb sh -c 'mariadb -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" -N -e "SELECT COUNT(*) FROM gaun_assistant.staff;"' 2>/dev/null
}
count_qdrant() {
  local coll="$1"
  local key; key=$(docker exec gaun_qdrant printenv QDRANT__SERVICE__API_KEY 2>/dev/null)
  curl -s "http://127.0.0.1:6333/collections/${coll}" -H "api-key: ${key}" 2>/dev/null \
    | grep -o '"points_count":[0-9]*' | grep -o '[0-9]*'
}

BEFORE_STAFF=$(count_staff)
BEFORE_REGS=$(count_qdrant regulations)
echo "Önce: staff=${BEFORE_STAFF:-?} regulations=${BEFORE_REGS:-?}"

echo
echo "▶ 1/3 Personel dizini yeniden taranıyor (crawler_rehber.py --write)..."
./.venv/bin/python crawler_rehber.py --write

echo
echo "▶ 2/3 Kampüs siteleri yeniden taranıyor + Qdrant'a gömülüyor (gaun_crawler.py)..."
./.venv/bin/python gaun_crawler.py

echo
echo "▶ 3/3 Personel embedding'i tazeleniyor (embed_data.py)..."
./.venv/bin/python embed_data.py

AFTER_STAFF=$(count_staff)
AFTER_REGS=$(count_qdrant regulations)
echo
echo "Sonra: staff=${AFTER_STAFF:-?} regulations=${AFTER_REGS:-?}"

# Sağlık kontrolü: sayı BEKLENMEDİK şekilde çok düştüyse muhtemelen bir scrape
# hatasıdır (site değişti, JS-render kırıldı), gerçek personel kaybı değil —
# sessizce kabul etmek yerine açıkça uyar.
if [ -n "${BEFORE_STAFF:-}" ] && [ -n "${AFTER_STAFF:-}" ] && [ "$AFTER_STAFF" -lt $((BEFORE_STAFF * 80 / 100)) ]; then
  echo "⚠️  UYARI: staff sayısı %20'den fazla düştü (${BEFORE_STAFF} -> ${AFTER_STAFF}) — muhtemelen tarama hatası, ELLE İNCELE."
fi
if [ -n "${BEFORE_REGS:-}" ] && [ -n "${AFTER_REGS:-}" ] && [ "$AFTER_REGS" -lt $((BEFORE_REGS * 50 / 100)) ]; then
  echo "⚠️  UYARI: regulations sayısı yarıdan fazla düştü (${BEFORE_REGS} -> ${AFTER_REGS}) — muhtemelen tarama hatası, ELLE İNCELE."
fi

echo
echo "=== Tamamlandı — $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "Log: ${LOG}"
