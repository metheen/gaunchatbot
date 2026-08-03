# GaunAI — BİDB Devir ve Entegrasyon Paketi

**Amaç:** GaunAI (GAÜN yapay zekâ asistanı) widget'ını gaziantep.edu.tr anasayfasına
entegre etmek. Bu doküman BİDB'nin (veya siteyi yöneten ekibin) yapması gerekenleri
ve bizim sağladıklarımızı özetler.

## 1. Neden BİDB'nin sunucusu gerekiyor (önemli)

Uygulama şu ana kadar geliştirme/test amaçlı **özel bir ağdaki** (yerel bir VM)
sunucuda çalıştı — bu sunucu internetten veya kampüs ağından erişilebilir
**değildir**. gaziantep.edu.tr gibi herkese açık bir sayfaya widget eklemek için
uygulamanın gerçek, ağ erişimi olan bir sunucuda (kampüs veri merkezi VM'i veya
BİDB'nin onayladığı bir bulut sunucusu) çalışması **şart**.

Bu aynı zamanda doğru mimari karardır: öğrenci verisi (kişisel iletişim bilgileri,
sohbet telemetrisi) kurumsal olmayan bir makinede değil, BİDB'nin denetimindeki
bir sunucuda barınmalıdır.

## 2. Bizden BİDB'ye — ne istiyoruz

| # | İstek | Neden |
|---|---|---|
| 1 | **Bir sunucu/VM** (Ubuntu 22.04+ önerilir), min. **8 GB RAM**, 4 çekirdek, 30 GB disk, Docker destekli | Yerel LLM (qwen2.5:7B) + Qdrant + MariaDB için gerekli minimum; 3.3 GB RAM'de test ederken OOM çökmesi yaşadık, 8 GB'de sorunsuz |
| 2 | **Bir alt alan adı** — örn. `gaunai.gaziantep.edu.tr` → bu sunucunun IP'sine yönlendirilmiş A kaydı | Widget ve API bu adresten servis edilecek |
| 3 | **TLS sertifikası** (Let's Encrypt/certbot ile biz kurarız, yalnız 80/443 portlarının dışa açık olması yeterli) | Tarayıcı güvenlik uyarısı olmasın, gaziantep.edu.tr (HTTPS) üzerinden karışık-içerik (mixed content) engeli olmasın |
| 4 | **SSH erişimi** (bize veya BİDB'nin kendi personeline — dağıtımı biz yönetebiliriz ya da devredebiliriz) | Kurulum ve güncelleme için |

## 3. Bizden BİDB'ye — ne sağlıyoruz

- **Uygulama kodu**: git deposu (hazır, `git clone` ile tek komutta alınır)
- **Tek komutluk sıfırdan kurulum**: `bidb_bootstrap.sh` — Docker, Ollama+modeller,
  Python ortamı, veritabanı şeması, **doğrulanmış 1215 kayıtlık personel verisi**
  (`db/staff_seed.sql` — canlı siteyi yeniden taramaya gerek yok), Qdrant gömme,
  systemd servisleri, Nginx, TLS (certbot) ve güvenlik duvarını TEK komutta kurar
- **Otomatik güncelleme betiği**: `redeploy.sh` — sonraki her güncellemede
  `git pull` → test → yeniden başlatma → otomatik doğrulama (13 senaryolu
  uçtan uca stres testi) tek komutta
- **Gömülebilir widget**: `static/embed.js` — anasayfaya eklenecek TEK satır script
- **Güvenlik**: MariaDB/Qdrant/Ollama yalnız `127.0.0.1`'de dinler, dışarıya asla
  açılmaz; yalnız Nginx (80/443) dışa açık olur
- **Test kapsamı**: 39 birim testi + 13 senaryolu E2E stres testi (hepsi otomatik
  çalışır, `redeploy.sh` her güncellemede tekrar doğrular)

## 4. Kurulum (BİDB sunucusu hazır olduğunda) — TEK KOMUT

```bash
git clone <bize-verilecek-repo-adresi> ~/gaunchatbot && cd ~/gaunchatbot
sudo ./bidb_bootstrap.sh gaunai.gaziantep.edu.tr admin@example.com
```

Bu tek komut şunları otomatik yapar: sistem paketleri (Docker/Nginx/certbot) →
`.env` (güçlü rastgele sırlarla) → MariaDB+Qdrant (Docker, yalnız 127.0.0.1) →
Ollama + bge-m3 + qwen2.5:7b-instruct (~6 GB indirme) → Python ortamı →
veritabanı şeması + **hazır personel verisi** → Qdrant gömme → systemd
(`gaunai-api.service`) → Nginx + TLS (Let's Encrypt) → UFW → **13 senaryolu
otomatik doğrulama**. Sonunda anasayfaya ekleyeceğiniz script etiketini ekrana
yazdırır.

**Ön koşul:** `gaunai.gaziantep.edu.tr` DNS kaydı bu sunucunun IP'sine
işaret etmeli (certbot doğrulaması için gerekli) — komuttan ÖNCE ayarlanmalı.

**Dürüst not:** Bu betik uçtan uca, gerçek bir Ubuntu sunucusunda TEST
EDİLMEDİ (elimizde taze bir sunucu yoktu) — her adımı tek tek inceledik ve
alt parçalarını (şablon dönüşümleri, veri dökümü, syntax) doğruladık, ama
tam koşum ilk kez BİDB'nin sunucusunda olacak. İlk çalıştırmada bir sorun
çıkarsa (özellikle adım numarası + hata mesajı) bize iletilirse hızla
düzeltiriz.

## 5. Anasayfaya eklenecek TEK satır

`</body>` etiketinden hemen önce:

```html
<script src="https://gaunai.gaziantep.edu.tr/embed.js" defer
        data-api-url="https://gaunai.gaziantep.edu.tr/api/chat"></script>
```

Bu satır sağ-alt köşede bir sohbet balonu ekler; **Shadow DOM** ile tam izole
çalışır — gaziantep.edu.tr'nin mevcut CSS/JS'ini etkilemez, ondan etkilenmez.
Test edildi (kasıtlı çakışan stiller içeren simülasyon sayfasında).

## 6. Açık kalan kararlar

- **Alt alan adı ismi**: `gaunai.gaziantep.edu.tr` bir öneri; BİDB farklı isim
  tercih ederse `embed.js`'teki `DEFAULT_API_URL` ve Nginx `server_name` buna
  göre güncellenir (tek satırlık değişiklik).
- **Sunucu barındırma**: kampüs veri merkezinde fiziksel/sanal makine mi, yoksa
  bulut (örn. üniversitenin anlaşmalı bulut sağlayıcısı) mı — BİDB'nin standart
  politikasına göre belirlenmeli.
