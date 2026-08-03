#!/bin/bash
#
# GaunAI — Güncelleme dağıtımı (Evolution / CI-CD).
# Sunucuda (~/gaunchatbot) sudo OLMADAN, mevcut kullanıcıyla çalışır.
# git push sonrası burada elle veya /loop ile tetiklenir.
#
# Adımlar: git pull (ff-only) -> pytest -> [offline_data/ değiştiyse]
#          regulations --recreate + re-ingest -> systemd restart -> otomatik
#          post-deploy smoke test (stress_test.py, canlı API üzerinde).
#
# Kullanım:
#   ./redeploy.sh              # tam akış
#   ./redeploy.sh --no-smoke   # smoke test'i atla (ör. API henüz restart edilmediyse)

set -euo pipefail
cd "$(dirname "$0")"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
step() { echo -e "${BLUE}▶  $1${NC}"; }
ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠  $1${NC}"; }
err()  { echo -e "${RED}❌ $1${NC}"; }

MARKER=".last_ingest_commit"

step "1) git pull (fast-forward only)"
BEFORE=$(git rev-parse HEAD)
git pull --ff-only
AFTER=$(git rev-parse HEAD)
[[ "$BEFORE" == "$AFTER" ]] && ok "Zaten güncel ($AFTER)." || ok "Güncellendi: ${BEFORE:0:7} -> ${AFTER:0:7}"

step "2) pytest (regresyon)"
./.venv/bin/python -m pytest -q

step "3) offline_data/ veya rag_pipeline.py değişti mi? (koşullu recreate+ingest)"
LAST_INGESTED="$(cat "$MARKER" 2>/dev/null || true)"
if [[ -z "$LAST_INGESTED" ]] || ! git diff --quiet "$LAST_INGESTED" "$AFTER" -- offline_data/ rag_pipeline.py 2>/dev/null; then
  warn "Veri/kürasyon kodu değişmiş -> Qdrant 'regulations' yeniden kuruluyor."
  export PATH="$HOME/.local/bin:$PATH"
  curl -s -m4 http://127.0.0.1:11434/api/tags >/dev/null || { err "Ollama'ya ulaşılamıyor (ollama.service çalışıyor mu?)"; exit 1; }
  ./.venv/bin/python setup_regulations_db.py --recreate
  ./.venv/bin/python offline_ingest.py
  echo "$AFTER" > "$MARKER"
  ok "'regulations' yeniden gömüldü."
else
  ok "offline_data/ değişmemiş -> ingest atlandı."
fi

step "4) API'yi yeniden başlat (systemd)"
if sudo -n systemctl restart gaunai-api.service 2>/dev/null; then
  ok "gaunai-api.service yeniden başlatıldı (passwordless sudo)."
else
  warn "Passwordless sudo yok — ELLE çalıştır:"
  echo "     sudo systemctl restart gaunai-api.service"
  if [[ "${1:-}" != "--no-smoke" ]]; then
    warn "Restart sonrası smoke test için: ./redeploy.sh --no-smoke sonra ayrıca ./.venv/bin/python stress_test.py"
    exit 3
  fi
fi

if [[ "${1:-}" == "--no-smoke" ]]; then
  ok "Deploy tamam (smoke test atlandı, --no-smoke)."
  exit 0
fi

step "5) Post-deploy smoke test (stress_test.py, canlı API)"
sleep 3
if ./.venv/bin/python stress_test.py; then
  ok "SMOKE TEST YEŞİL — deploy başarılı."
else
  err "SMOKE TEST KIRMIZI — 'git log' ile önceki iyi commit'e dönmeyi değerlendir."
  exit 1
fi
