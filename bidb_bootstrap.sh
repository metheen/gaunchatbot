#!/bin/bash
#
# GaunAI — BİDB için SIFIRDAN kurulum (Faz 7 devir paketi).
# Taze bir Ubuntu 22.04+ sunucuda, root ile çalıştırılır. Docker, Ollama, Python
# ortamı, veritabanları, doğrulanmış personel verisi, systemd servisleri, Nginx
# ve TLS dahil HER ŞEYİ tek komutta kurar.
#
# Kullanım:
#   sudo ./bidb_bootstrap.sh <domain> <certbot-email>
#   sudo ./bidb_bootstrap.sh gaunai.gaziantep.edu.tr admin@example.com
#
# Betik, ÇALIŞTIRILDIĞI dizinin proje kökü olduğunu varsayar (yani önce
# `git clone <repo> && cd <repo> && sudo ./bidb_bootstrap.sh ...`).
#
# Adımlar: sistem paketleri -> Docker (MariaDB+Qdrant) -> Ollama+modeller ->
#          Python venv -> DB şema+veri -> Qdrant gömme -> systemd -> Nginx+TLS
#          -> UFW -> doğrulama (13 senaryolu smoke test).

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
step() { echo -e "${BLUE}▶  $1${NC}"; }
ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠  $1${NC}"; }
err()  { echo -e "${RED}❌ $1${NC}"; }

trap 'err "Kurulum satır $LINENO civarında BAŞARISIZ oldu. Yukarıdaki çıktıyı incele."' ERR

if [[ $EUID -ne 0 ]]; then
  err "Bu betik root (sudo) yetkisiyle çalıştırılmalıdır."
  echo -e "${YELLOW}   Doğru kullanım:${NC} sudo ./bidb_bootstrap.sh <domain> <certbot-email>"
  exit 1
fi

DOMAIN="${1:?Kullanım: sudo ./bidb_bootstrap.sh <domain> <certbot-email>}"
CERTBOT_EMAIL="${2:?Kullanım: sudo ./bidb_bootstrap.sh <domain> <certbot-email>}"
APP_DIR="$(pwd)"
APP_USER="gaunai"

echo -e "${BLUE}=== GaunAI — BİDB Sıfırdan Kurulum ===${NC}"
echo "   APP_DIR=${APP_DIR}  DOMAIN=${DOMAIN}  EMAIL=${CERTBOT_EMAIL}"
echo

[[ -f "${APP_DIR}/bot.py" && -f "${APP_DIR}/docker-compose.yml" ]] || {
  err "Bu betik proje kökünde çalıştırılmalı (bot.py/docker-compose.yml bulunamadı)."
  exit 1
}

# ---------------------------------------------------------------------------
# 1) Sistem paketleri
# ---------------------------------------------------------------------------
step "1/10 Sistem paketleri (apt)..."
apt update -qq
apt install -y -qq docker.io docker-compose-plugin git python3-venv python3-pip \
  nginx certbot python3-certbot-nginx curl >/dev/null
systemctl enable --now docker >/dev/null
ok "Sistem paketleri kuruldu (docker, nginx, certbot, python3-venv)."

# ---------------------------------------------------------------------------
# 2) .env (yoksa şablondan üret, güçlü rastgele sırlarla)
# ---------------------------------------------------------------------------
step "2/10 .env hazırlanıyor..."
if [[ ! -f "${APP_DIR}/.env" ]]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  sed -i \
    -e "s|CHANGE_ME_root_sifresi|$(openssl rand -base64 24)|" \
    -e "s|CHANGE_ME_uygulama_sifresi|$(openssl rand -base64 24)|" \
    -e "s|CHANGE_ME_uzun_rastgele_anahtar|$(openssl rand -base64 32)|" \
    -e "s|^MARIADB_HOST=mariadb|MARIADB_HOST=127.0.0.1|" \
    "${APP_DIR}/.env"
  echo "ALLOWED_ORIGINS=https://www.gaziantep.edu.tr,https://gaziantep.edu.tr" >> "${APP_DIR}/.env"
  chmod 600 "${APP_DIR}/.env"
  ok ".env oluşturuldu (güçlü rastgele sırlarla, ALLOWED_ORIGINS ayarlı)."
else
  warn ".env zaten mevcut — dokunulmadı."
fi

# ---------------------------------------------------------------------------
# 3) Docker (MariaDB + Qdrant)
# ---------------------------------------------------------------------------
step "3/10 MariaDB + Qdrant (Docker) başlatılıyor..."
cd "${APP_DIR}"
docker compose up -d
step "   MariaDB hazır olması bekleniyor..."
for i in $(seq 1 30); do
  docker exec gaun_mariadb sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" -e "SELECT 1;"' >/dev/null 2>&1 && break
  sleep 3
done
ok "Docker servisleri ayakta (127.0.0.1'de, dışa kapalı)."

# ---------------------------------------------------------------------------
# 4) Ollama + modeller
# ---------------------------------------------------------------------------
step "4/10 Ollama kuruluyor + modeller indiriliyor (bge-m3 + qwen2.5:7b, ~6GB)..."
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh >/dev/null
fi
cp "${APP_DIR}/deploy/ollama.service" /etc/systemd/system/ollama.service
sed -i "s|^User=.*|User=root|" /etc/systemd/system/ollama.service  # root: BİDB context, dedicated user opsiyonel
systemctl daemon-reload
systemctl enable --now ollama.service
sleep 3
ollama pull bge-m3
ollama pull qwen2.5:7b-instruct
ok "Ollama servis olarak çalışıyor, modeller indirildi."

# ---------------------------------------------------------------------------
# 5) Python ortamı
# ---------------------------------------------------------------------------
step "5/10 Python sanal ortamı + bağımlılıklar..."
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install -q --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt"
ok "Python ortamı hazır."

# ---------------------------------------------------------------------------
# 6) Veritabanı şeması + doğrulanmış personel verisi
# ---------------------------------------------------------------------------
step "6/10 MariaDB şeması + personel/birim verisi (1215 kayıt) yükleniyor..."
docker exec -i gaun_mariadb sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"' \
  < "${APP_DIR}/db/migrations/001_entity_tables.sql"
docker exec -i gaun_mariadb sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"' \
  < "${APP_DIR}/db/staff_seed.sql"
STAFF_COUNT=$(docker exec gaun_mariadb sh -c 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" -N -e "SELECT COUNT(*) FROM gaun_assistant.staff;"')
ok "Veritabanı hazır (${STAFF_COUNT} personel kaydı)."

# ---------------------------------------------------------------------------
# 7) Qdrant koleksiyonları (regulations + staff embedding)
# ---------------------------------------------------------------------------
step "7/10 Qdrant koleksiyonları gömülüyor (birkaç dakika sürebilir)..."
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/setup_regulations_db.py" --recreate
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/offline_ingest.py"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/embed_data.py"
ok "Qdrant 'regulations' ve 'staff' koleksiyonları hazır."

# ---------------------------------------------------------------------------
# 8) systemd — GaunAI API servisi
# ---------------------------------------------------------------------------
step "8/10 GaunAI API systemd servisi kuruluyor..."
id "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"
chmod 600 "${APP_DIR}/.env"
cp "${APP_DIR}/deploy/gaunai.service" /etc/systemd/system/gaunai-api.service
sed -i \
  -e "s|/opt/gaunai|${APP_DIR}|g" \
  -e "s|^User=.*|User=${APP_USER}|" \
  -e "s|^Group=.*|Group=${APP_USER}|" \
  /etc/systemd/system/gaunai-api.service
systemctl daemon-reload
systemctl enable --now gaunai-api.service
ok "gaunai-api.service etkin ve çalışıyor. (redeploy.sh bu isimle eşleşir.)"

# ---------------------------------------------------------------------------
# 9) Nginx + TLS + UFW
# ---------------------------------------------------------------------------
step "9/10 Nginx + TLS (certbot) + firewall..."
cp "${APP_DIR}/deploy/gaunai.conf" /etc/nginx/sites-available/gaunai.conf
sed -i "s|server_name .*;|server_name ${DOMAIN};|" /etc/nginx/sites-available/gaunai.conf
ln -sf /etc/nginx/sites-available/gaunai.conf /etc/nginx/sites-enabled/gaunai.conf
[[ -e /etc/nginx/sites-enabled/default ]] && rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp
  ufw allow 80/tcp; ufw allow 443/tcp
  ufw --force enable >/dev/null
fi
certbot --nginx -d "$DOMAIN" -m "$CERTBOT_EMAIL" --agree-tos --non-interactive --redirect || \
  warn "certbot başarısız oldu — DNS'in bu sunucuya işaret ettiğinden emin ol, sonra elle çalıştır: certbot --nginx -d ${DOMAIN}"
ok "Nginx + firewall hazır."

# ---------------------------------------------------------------------------
# 10) Doğrulama
# ---------------------------------------------------------------------------
step "10/10 Doğrulama (pytest + 13 senaryolu smoke test)..."
"${APP_DIR}/.venv/bin/python" -m pytest -q "${APP_DIR}" || warn "Bazı testler başarısız — çıktıyı incele."
sleep 3
if "${APP_DIR}/.venv/bin/python" "${APP_DIR}/stress_test.py"; then
  ok "SMOKE TEST YEŞİL."
else
  warn "Smoke test kırmızı — servis loglarını incele: journalctl -u gaunai-api -f"
fi

echo
echo -e "${GREEN}🎉 GaunAI BİDB sunucusunda hazır!${NC}"
echo -e "${BLUE}Adres        :${NC} https://${DOMAIN}/"
echo -e "${BLUE}Anasayfaya   :${NC} <script src=\"https://${DOMAIN}/embed.js\" defer data-api-url=\"https://${DOMAIN}/api/chat\"></script>"
echo -e "${BLUE}Canlı log    :${NC} journalctl -u gaunai-api -f"
echo -e "${BLUE}Güncelleme   :${NC} cd ${APP_DIR} && ./redeploy.sh"
