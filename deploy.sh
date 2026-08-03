#!/bin/bash
#
# GaunAI — Tek komutluk otonom production dağıtımı (Faz 17).
# Kullanım (Ubuntu sunucuda, proje $APP_DIR altındayken):
#   sudo ./deploy.sh
#
# deploy/gaunai.service ve deploy/gaunai.conf dosyalarını şablon alır,
# aşağıdaki değişkenlere göre uyarlayıp systemd + nginx + ufw kurulumunu yapar.

set -euo pipefail

# ---------------------------------------------------------------------------
# Renkler ve log yardımcıları
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
step() { echo -e "${BLUE}▶  $1${NC}"; }
ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠  $1${NC}"; }
err()  { echo -e "${RED}❌ $1${NC}"; }

trap 'err "Kurulum satır $LINENO civarında BAŞARISIZ oldu. Yukarıdaki çıktıyı incele."' ERR

# ---------------------------------------------------------------------------
# 1) Root kontrolü
# ---------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
  err "Bu betik root (sudo) yetkisiyle çalıştırılmalıdır."
  echo -e "${YELLOW}   Doğru kullanım:${NC} sudo ./deploy.sh"
  exit 1
fi

# ---------------------------------------------------------------------------
# 2) Düzenlenebilir değişkenler — ortam değişkeniyle geçilebilir, aksi halde
#    varsayılan kullanılır. Örn: DOMAIN=gaunai.gaziantep.edu.tr sudo -E ./deploy.sh
#    Not: $USER kabuk değişkenini gölgelememek için 'APP_USER' kullanıldı.
# ---------------------------------------------------------------------------
APP_DIR="${APP_DIR:-/opt/gaunai}"
APP_USER="${APP_USER:-gaunai}"
DOMAIN="${DOMAIN:-localhost}"
SERVICE_NAME="${SERVICE_NAME:-gaunai}"

DEPLOY_DIR="${APP_DIR}/deploy"
SERVICE_SRC="${DEPLOY_DIR}/gaunai.service"
NGINX_SRC="${DEPLOY_DIR}/gaunai.conf"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_DST="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
NGINX_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"

echo -e "${BLUE}=== GaunAI Otonom Dağıtım (Faz 17) ===${NC}"
echo "   APP_DIR=${APP_DIR}  APP_USER=${APP_USER}  DOMAIN=${DOMAIN}  SERVICE=${SERVICE_NAME}"
echo

# ---------------------------------------------------------------------------
# 3) Ön kontroller
# ---------------------------------------------------------------------------
step "Ön kontroller (kaynak dosyalar ve gerekli komutlar)..."
[[ -f "$SERVICE_SRC" ]] || { err "Bulunamadı: $SERVICE_SRC"; exit 1; }
[[ -f "$NGINX_SRC"   ]] || { err "Bulunamadı: $NGINX_SRC"; exit 1; }
command -v systemctl >/dev/null 2>&1 || { err "systemctl yok (systemd gerekli)."; exit 1; }
command -v nginx     >/dev/null 2>&1 || { err "nginx kurulu değil. Kur: sudo apt install nginx"; exit 1; }
ok "Ön kontroller tamam."

# ---------------------------------------------------------------------------
# 4) Servis kullanıcısı + izinler
# ---------------------------------------------------------------------------
step "Servis kullanıcısı ve dosya izinleri ayarlanıyor..."
if id "$APP_USER" >/dev/null 2>&1; then
  warn "Kullanıcı zaten mevcut: $APP_USER"
else
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
  ok "Servis kullanıcısı oluşturuldu: $APP_USER"
fi
chown -R "${APP_USER}:${APP_USER}" "$APP_DIR"
if [[ -f "${APP_DIR}/.env" ]]; then
  chmod 600 "${APP_DIR}/.env"
else
  warn ".env bulunamadı (${APP_DIR}/.env) — servis DB/LLM'e bağlanamayabilir."
fi
ok "Kullanıcı ve izinler ayarlandı."

# ---------------------------------------------------------------------------
# 5) systemd servis dosyası (şablonu değişkenlere göre uyarla)
# ---------------------------------------------------------------------------
step "systemd servis dosyası kuruluyor -> ${SERVICE_DST}"
cp "$SERVICE_SRC" "$SERVICE_DST"
sed -i \
  -e "s|/opt/gaunai|${APP_DIR}|g" \
  -e "s|^User=.*|User=${APP_USER}|" \
  -e "s|^Group=.*|Group=${APP_USER}|" \
  "$SERVICE_DST"
ok "Servis dosyası yerleştirildi ve uyarlandı."

# ---------------------------------------------------------------------------
# 6) Nginx konfigürasyonu + sites-enabled symlink
# ---------------------------------------------------------------------------
step "Nginx konfigürasyonu kuruluyor -> ${NGINX_DST}"
cp "$NGINX_SRC" "$NGINX_DST"
sed -i "s|server_name .*;|server_name ${DOMAIN};|" "$NGINX_DST"
ln -sf "$NGINX_DST" "$NGINX_LINK"
[[ -e /etc/nginx/sites-enabled/default ]] && rm -f /etc/nginx/sites-enabled/default && warn "Varsayılan nginx sitesi devre dışı bırakıldı."
ok "Nginx sitesi etkinleştirildi (symlink)."

# ---------------------------------------------------------------------------
# 7) systemd: reload + enable --now
# ---------------------------------------------------------------------------
step "systemd yeniden yükleniyor ve servis etkinleştiriliyor..."
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
ok "Servis etkin ve çalışıyor: ${SERVICE_NAME}"

# ---------------------------------------------------------------------------
# 8) Nginx test + restart (test başarısızsa restart YOK)
# ---------------------------------------------------------------------------
step "Nginx konfigürasyonu test ediliyor (nginx -t)..."
if nginx -t; then
  ok "Nginx konfigürasyonu geçerli."
  systemctl restart nginx
  ok "Nginx yeniden başlatıldı."
else
  err "Nginx konfigürasyonu HATALI. Nginx yeniden başlatılmadı; çıktıyı düzelt."
  exit 1
fi

# ---------------------------------------------------------------------------
# 9) UFW firewall (SSH kilidini önleyerek)
# ---------------------------------------------------------------------------
step "Firewall (UFW) kuralları uygulanıyor..."
if command -v ufw >/dev/null 2>&1; then
  warn "SSH bağlantını kaybetmemek için ÖNCE OpenSSH'a izin veriliyor."
  ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp
  ufw allow 80/tcp
  ufw allow 443/tcp
  ufw --force enable
  ok "UFW kuralları uygulandı (OpenSSH, 80/tcp, 443/tcp)."
else
  warn "ufw kurulu değil — firewall adımı atlandı."
fi

# ---------------------------------------------------------------------------
# 10) Sonuç
# ---------------------------------------------------------------------------
echo
echo -e "${GREEN}🎉 GaunAI başarıyla canlıya alındı!${NC}"
echo
echo -e "${BLUE}--- Servis durumu (${SERVICE_NAME}) ---${NC}"
systemctl status "$SERVICE_NAME" --no-pager || true
echo
echo -e "${BLUE}Doğrula :${NC} curl -s http://127.0.0.1:8000/api/health"
echo -e "${BLUE}Canlı log:${NC} journalctl -u ${SERVICE_NAME} -f"
echo -e "${BLUE}Adres    :${NC} http://${DOMAIN}/"
echo -e "${YELLOW}HTTPS için:${NC} sudo certbot --nginx -d ${DOMAIN}"
