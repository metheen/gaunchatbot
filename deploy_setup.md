# Faz 17 — Canlı Sunucu (Production) Dağıtımı

GaunAI FastAPI uygulamasını Ubuntu'da **systemd** ile kalıcı servis yapıp
**Nginx** reverse proxy ile dışa açma rehberi. Konfig dosyaları: `deploy/gaunai.service`
ve `deploy/gaunai.conf`.

> Mimari: `internet → Nginx :80 → 127.0.0.1:8000 (uvicorn) → bot.py → MariaDB/Qdrant/Ollama`.
> Uygulama **yalnız 127.0.0.1**'e bind edilir; dışa tek giriş Nginx'tir.

---

## 0. Ön koşullar (dağıtımdan önce)

Sunucuda şunlar hazır olmalı:

```bash
# Proje /opt/gaunai altında (örnek konum — service/conf'ta bu yol kullanılıyor)
sudo mkdir -p /opt/gaunai && sudo chown "$USER" /opt/gaunai
git clone <repo> /opt/gaunai     # veya rsync ile kopyala
cd /opt/gaunai

# Python sanal ortam + bağımlılıklar
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

# .env oluştur ve doldur (sırlar), izinleri kilitle
cp .env.example .env && nano .env && chmod 600 .env

# Altyapı: MariaDB + Qdrant (docker) ve Ollama çalışıyor olmalı
docker compose up -d                       # mariadb + qdrant
ollama pull bge-m3                          # embedding
ollama pull qwen2.5:7b-instruct            # üretim modeli
# (Ollama, Ubuntu'da genelde 'ollama.service' olarak otomatik çalışır.)

# Veriyi göm (bir defalık): personel + yönetmelik + SSS
./.venv/bin/python embed_data.py            # MariaDB staff -> Qdrant
./.venv/bin/python setup_regulations_db.py  # regulations koleksiyonu
./.venv/bin/python offline_ingest.py        # offline_data/*.md -> Qdrant

# Elle bir kez doğrula (uvicorn'u geçici çalıştırıp curl)
./.venv/bin/uvicorn api:app --host 127.0.0.1 --port 8000 &
sleep 3 && curl -s http://127.0.0.1:8000/api/health   # {"status":"ok"}
kill %1
```

> **Not:** MariaDB/Qdrant'ı production'da da **sadece 127.0.0.1**'e bind bırak
> (docker-compose'daki `127.0.0.1:3306` / `127.0.0.1:6333`). LAN'a/dışa açma.

---

## 1. systemd servis dosyası — `deploy/gaunai.service`

Uygulamayı sürekli çalıştırır, çökerse `Restart=always` ile ayağa kaldırır.
Tam dosya `deploy/gaunai.service` içinde. Özet:

```ini
[Unit]
Description=GaunAI - FastAPI RAG Asistani (uvicorn)
After=network-online.target docker.service ollama.service
Wants=network-online.target

[Service]
Type=simple
User=gaunai
Group=gaunai
WorkingDirectory=/opt/gaunai
ExecStart=/opt/gaunai/.venv/bin/uvicorn api:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
NoNewPrivileges=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`User`, `WorkingDirectory` ve `ExecStart` yollarını kendi kurulumuna göre uyarla.
`.env`, `WorkingDirectory`'de olduğu için `bot.py`'nin `load_dotenv(".env")` çağrısı
onu bulur (ayrı `EnvironmentFile` gerekmez).

---

## 2. Nginx reverse proxy — `deploy/gaunai.conf`

80 → 127.0.0.1:8000 yönlendirmesi + **uzatılmış timeout** (LLM yanıtı 15-20 sn
sürebildiği için 504 önlemek şart). Tam dosya `deploy/gaunai.conf`. Kritik satırlar:

```nginx
server {
    listen 80;
    server_name gaunai.gaziantep.edu.tr;   # veya sunucu IP'si

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # KRİTİK: standart 60 sn yerine uzat (aksi halde 504 Gateway Timeout)
        proxy_connect_timeout 120s;
        proxy_send_timeout    300s;
        proxy_read_timeout    300s;
        send_timeout          300s;
        proxy_buffering off;
    }
}
```

---

## 3. Sunucu içi kurulum komutları (bash)

```bash
# --- 3.1 Deploy kullanıcısı (yoksa) ve izinler ---
sudo useradd --system --home /opt/gaunai --shell /usr/sbin/nologin gaunai || true
sudo chown -R gaunai:gaunai /opt/gaunai
sudo chmod 600 /opt/gaunai/.env

# --- 3.2 Konfig dosyalarını yerleştir ---
sudo cp /opt/gaunai/deploy/gaunai.service /etc/systemd/system/gaunai.service
sudo cp /opt/gaunai/deploy/gaunai.conf    /etc/nginx/sites-available/gaunai.conf

# --- 3.3 Nginx: symlink ile etkinleştir (+ varsayılanı kaldır) ---
sudo ln -s /etc/nginx/sites-available/gaunai.conf /etc/nginx/sites-enabled/gaunai.conf
sudo rm -f /etc/nginx/sites-enabled/default          # opsiyonel
sudo nginx -t                                        # config sözdizimi testi

# --- 3.4 systemd: servisi etkinleştir + başlat ---
sudo systemctl daemon-reload
sudo systemctl enable --now gaunai
sudo systemctl status gaunai --no-pager

# --- 3.5 Nginx'i yeniden yükle ---
sudo systemctl reload nginx        # veya: sudo systemctl restart nginx

# --- 3.6 UFW (firewall) — ÖNCE SSH! ---
sudo ufw allow OpenSSH             # SSH'ı kilitleme riskine karşı ÖNCE bu
sudo ufw allow 80/tcp              # HTTP
sudo ufw allow 443/tcp             # HTTPS (TLS kurulacaksa)
sudo ufw enable
sudo ufw status verbose
```

> ⚠️ **UFW uyarısı:** `ufw enable` demeden ÖNCE mutlaka `ufw allow OpenSSH`
> çalıştır; yoksa aktif SSH oturumun dışında kalırsın.

---

## 4. Doğrulama

```bash
# Servis ayakta mı, loglar?
sudo systemctl status gaunai --no-pager
sudo journalctl -u gaunai -f              # canlı log (Ctrl-C ile çık)

# API doğrudan (loopback)
curl -s http://127.0.0.1:8000/api/health          # {"status":"ok"}

# Nginx üzerinden (dış)
curl -s http://<SUNUCU-IP-veya-ALANADI>/api/health

# Uçtan uca sohbet (LLM ~15-20 sn — timeout uzatması burada işe yarar)
curl -s -X POST http://<SUNUCU-IP>/api/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"Sınav sonucuma nasıl itiraz ederim?"}'
```

Tarayıcıdan `http://<SUNUCU-IP>/` → sağ-alttaki GaunAI widget'ı açılır.

---

## 5. HTTPS (443) — önerilir

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d gaunai.gaziantep.edu.tr
# certbot 80->443 yönlendirmesini ve sertifika yenilemeyi otomatik kurar.
# 443 için UFW'de izin zaten verildi (3.6).
```

---

## 6. İşletme (operations)

```bash
# Kod güncelleme akışı
cd /opt/gaunai && git pull
./.venv/bin/pip install -r requirements.txt      # bağımlılık değiştiyse
sudo systemctl restart gaunai

# Servisi durdur/başlat
sudo systemctl stop gaunai
sudo systemctl start gaunai

# Telemetri özeti (Faz 15)
./.venv/bin/python analytics.py
```

**Güvenlik özeti:**
- Uygulama, MariaDB (127.0.0.1:3306), Qdrant (127.0.0.1:6333) ve Ollama
  (127.0.0.1:11434) hepsi **loopback**'te; dışa yalnız Nginx :80/:443 açık.
- `.env` izni `600`, `gaunai` kullanıcısına ait; sırlar koda gömülü değil.
- Docker stack `restart: unless-stopped` ile yeniden başlatmalara dayanıklı;
  Ollama `ollama.service` olarak çalışır.

**Bağımlılık sırası:** API cevabı için MariaDB+Qdrant+Ollama ayakta olmalı.
`gaunai.service` bunların ardından başlar (`After=docker.service ollama.service`);
ilk isteklerde altyapı henüz hazır değilse API çökmeden hata döner, hazır olunca
düzelir.

> Bu rehber Ubuntu sunucu içindir; bu makinede (macOS) systemd/nginx test
> edilemez. Konfig sözdizimi standarttır; sunucuda `sudo nginx -t` ile doğrula.
