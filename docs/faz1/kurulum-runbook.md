# Faz 1 Kurulum Runbook — MariaDB + Qdrant (Ubuntu)

## 1) Hazırlık (bir kez)

```bash
cd /opt/gaunchatbot            # proje dizini (sunucudaki konumuna göre uyarla)
cp .env.example .env
chmod 600 .env                 # sırları yalnız sahibi okuyabilsin

# Her sır için AYRI güçlü değer üret, .env içine yapıştır:
openssl rand -base64 24        # MARIADB_ROOT_PASSWORD
openssl rand -base64 24        # MARIADB_PASSWORD
openssl rand -base64 32        # QDRANT_API_KEY
```

## 2) Doğrulama ve başlatma

```bash
docker compose config --quiet && echo OK   # sözdizimi + env kontrolü
docker compose up -d
docker compose ps                          # her iki servis 'healthy' olana kadar bekle
docker compose logs -f mariadb             # ilk açılışta 001_entity_tables.sql loglarını izle
```

Not: `db/migrations/` yalnız İLK açılışta (boş `data/mariadb`) otomatik uygulanır.
Sonraki migration'ları elle uygula (dosyalar idempotent):

```bash
docker compose exec -T mariadb mariadb -uroot -p"$(grep ^MARIADB_ROOT_PASSWORD .env | cut -d= -f2-)" \
  < db/migrations/002_yeni_migration.sql
```

## 3) Ağ testi — geçici curl konteyneri ile Qdrant

```bash
QKEY=$(grep ^QDRANT_API_KEY .env | cut -d= -f2-)

# Hazır mı?
docker run --rm --network gaun_network curlimages/curl:8.10.1 \
  -s http://qdrant:6333/readyz
# beklenen: all shards are ready

# API anahtarıyla koleksiyon listesi (boş liste normal):
docker run --rm --network gaun_network curlimages/curl:8.10.1 \
  -s -H "api-key: ${QKEY}" http://qdrant:6333/collections
# beklenen: {"result":{"collections":[]},"status":"ok",...}

# GÜVENLİK: anahtarsız istek REDDEDİLMELİ:
docker run --rm --network gaun_network curlimages/curl:8.10.1 \
  -s http://qdrant:6333/collections
# beklenen: "Must provide an API key..." (401/403)
```

## 4) MariaDB testi — şema ve tohum veri

```bash
MPASS=$(grep ^MARIADB_PASSWORD .env | cut -d= -f2-)

docker compose exec mariadb mariadb -ugaun_app -p"${MPASS}" gaun_assistant \
  -e "SHOW TABLES;"
# beklenen: departments, entity_aliases, map_targets, staff

docker compose exec mariadb mariadb -ugaun_app -p"${MPASS}" gaun_assistant \
  -e "SELECT s.full_name, s.role_title, d.name FROM staff s JOIN departments d ON d.id=s.department_id;"
# beklenen: Canan Deneme | Bilgi İşlem Daire Başkanı | Bilgi İşlem Daire Başkanlığı (ÖRNEK tohum)
```

## 5) İzolasyon doğrulaması — host'tan ERİŞİLEMEMELİ

```bash
curl -s --max-time 3 http://localhost:6333/ || echo "OK: Qdrant host'a kapalı"
mariadb -h 127.0.0.1 -P 3306 2>/dev/null    || echo "OK: MariaDB host'a kapalı"
ss -tlnp | grep -E ':3306|:6333' || echo "OK: portlar host'ta dinlemiyor"
```

## 6) Yedekleme (Faz 1'den itibaren alışkanlık)

```bash
# MariaDB dump:
docker compose exec mariadb mariadb-dump -uroot -p"..." gaun_assistant > yedek_$(date +%F).sql
# Qdrant snapshot (koleksiyon oluşunca): POST /collections/<ad>/snapshots
```

## Sorun giderme

- `unhealthy` mariadb → `docker compose logs mariadb`; ilk açılış migration hatası
  genelde SQL sözdiziminden gelir; düzeltip `docker compose down && sudo rm -rf data/mariadb`
  ile SIFIRDAN başlatılabilir (İLK kurulumda veri yokken güvenli — sonrasında asla).
- `data/` dizin izinleri: konteynerler root/mysql olarak yazar; dizinleri elle
  oluşturmana gerek yok, compose ilk açılışta oluşturur.
