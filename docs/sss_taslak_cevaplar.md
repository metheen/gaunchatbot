# SSS Taslak Cevaplar — Sosyal + Mezuniyet (DOĞRULAMA BEKLİYOR)

> ⛔ **BU DOSYA INGEST EDİLMEZ.** (`docs/` altında — `offline_ingest` yalnız
> `offline_data/`'yı okur.) Her cevap 21 taze `scraped_*.md` hasadındaki
> kanıta dayalı TASLAKtır; kullanıcı (Adım 3) doğrulayana kadar
> `kampus_master_sss.md`'ye TAŞINMAZ. Kanıt düzeyi "hizmet/sayfa mevcut"
> seviyesindedir; süreç ayrıntıları (tarih, ücret, form adımı) sitede
> depth=2'de olduğundan her taslakta [DOĞRULA] etiketiyle işaretlidir.

---

## SOSYAL (5 taslak)

### S1. Kısmi zamanlı çalışma
**Soru:** Okulda part-time (kısmi zamanlı) çalışmak istiyorum, nasıl başvururum?
**Taslak cevap:** Gaziantep Üniversitesi'nde kısmi zamanlı öğrenci çalıştırma
işlemleri Sağlık Kültür ve Spor Daire Başkanlığı (SKS) bünyesindeki **Kısmi
Zamanlı Öğrenci Birimi** tarafından yürütülür. Başvuru dönemleri ve kontenjanlar
SKS duyurularıyla ilan edilir: https://sksdb.gaziantep.edu.tr
**Kanıt:** scraped_sks_daire.md → "HİZMETLERİMİZ … KISMİ ZAMANLI ÖĞRENCİ BİRİMİ"
**[DOĞRULA]:** başvuru dönemi (genelde güz başı?), saat ücreti, kimler başvurabilir.

### S2. Psikolojik destek
**Soru:** Psikolojik desteğe ihtiyacım var, üniversitede danışmanlık var mı?
**Taslak cevap:** Evet. SKS Daire Başkanlığı bünyesinde **Psikolojik Danışma ve
Rehberlik Merkezi** öğrencilere hizmet vermektedir. Randevu ve başvuru bilgisi
için: https://sksdb.gaziantep.edu.tr (sitede "Randevu Al" bölümü mevcuttur).
**Kanıt:** scraped_sks_daire.md → "PSİKOLOJİK DANIŞMA VE REHBERLİK MERKEZİ" + "RANDEVU AL"
**[DOĞRULA]:** ücretsiz mi (muhtemelen evet), yeri/telefonu, randevu kanalı.

### S3. Beslenme/diyet danışmanlığı
**Soru:** Diyetisyene gitmek istiyorum, kampüste beslenme danışmanlığı var mı?
**Taslak cevap:** SKS bünyesinde **Beslenme ve Diyet Danışma Merkezi** vardır;
öğrenciler randevuyla yararlanabilir: https://sksdb.gaziantep.edu.tr
**Kanıt:** scraped_sks_daire.md → "BESLENME VE DİYET DANIŞMA MERKEZİ"
**[DOĞRULA]:** randevu prosedürü, hizmet günleri.

### S4. Spor tesisleri
**Soru:** Kampüste spor salonu/halı saha var mı, öğrenciler nasıl kullanır?
**Taslak cevap:** Kampüste SKS Spor Şube Müdürlüğü'ne bağlı **kapalı ve açık
spor tesisleri, çim saha ve tenis kortları** bulunur. Tesis kullanımı ve
rezervasyon bilgisi: https://sksdb.gaziantep.edu.tr
**Kanıt:** scraped_sks_daire.md → "SPOR TESİSLERİMİZ KAPALI/AÇIK … ÇİM SAHA TENİS KORTU"
**[DOĞRULA]:** öğrenciye ücret/rezervasyon usulü, saatler.

### S5. Öğrenci kulüpleri
**Soru:** Öğrenci kulüplerine/topluluklarına nasıl üye olurum, yeni kulüp nasıl kurulur?
**Taslak cevap:** Öğrenci kulüpleri ve kültürel etkinlikler SKS **Kültür Şube
Müdürlüğü** koordinasyonunda yürütülür. Kulüp listesi, üyelik ve yeni kulüp
kurma başvurusu için: https://sksdb.gaziantep.edu.tr
**Kanıt:** scraped_sks_daire.md → "MÜDÜRLÜKLER KÜLTÜR ŞUBE MÜDÜRLÜĞÜ"
**[DOĞRULA]:** kulüp kurma prosedürü (danışman + asgari üye?), aktif kulüp listesi.

---

## MEZUNİYET (5 taslak)

### M1. Diploma teslimi
**Soru:** Mezun oldum, diplomamı ne zaman ve nereden alabilirim?
**Taslak cevap:** Diploma işlemleri **Öğrenci İşleri Daire Başkanlığı (OİDB)**
tarafından yürütülür. Diploma hazır olana kadar e-Devlet üzerinden mezuniyet
belgesi alınabilir. Ayrıntı: https://oidb.gaziantep.edu.tr
**Kanıt:** scraped_ogrenci_isleri.md → diploma işlemleri menüsü; mevcut SSS'nin
e-Devlet/OIDB cevabıyla tutarlı.
**[DOĞRULA]:** hazırlanma süresi, şahsen/vekâletle teslim şartı.

### M2. İkinci nüsha (kayıp) diploma
**Soru:** Diplomamı kaybettim, yenisini nasıl çıkartırım?
**Taslak cevap:** Kayıp diploma için OİDB'nin **"İkinci Nüsha Diploma
İşlemleri"** sayfasındaki prosedür izlenir: https://oidb.gaziantep.edu.tr
**Kanıt:** scraped_ogrenci_isleri.md → "İKİNCİ NÜSHA DİPLOMA İŞLEMLERİ" (sayfa adı birebir)
**[DOĞRULA]:** gazete ilanı gerekli mi, ücret, dilekçe ekleri.

### M3. Mezuniyette ilişik kesme
**Soru:** Mezun olurken ilişik kesme işlemini nasıl yaparım?
**Taslak cevap:** Mezuniyet aşamasında **İlişik Kesme Formu** doldurulur
(kütüphane borcu vb. birim onayları). Form ve süreç OİDB sayfasında:
https://oidb.gaziantep.edu.tr
**Kanıt:** scraped_ogrenci_isleri.md → "İLİŞİK KESME FORMU" (Başvuru ve Belgeler altında)
**[DOĞRULA]:** hangi birim onayları isteniyor, OBS üzerinden mi manuel mi.

### M4. Mezun portalı
**Soru:** Mezunlar için portal/iletişim ağı var mı?
**Taslak cevap:** Evet, üniversitenin **Mezun Portalı** vardır; erişim OİDB
sayfası üzerinden sağlanır: https://oidb.gaziantep.edu.tr
**Kanıt:** scraped_ogrenci_isleri.md → "MEZUN PORTALI" menü öğesi
**[DOĞRULA]:** portal URL'si (muhtemelen ayrı subdomain), ne işe yarıyor (kariyer? belge?).

### M5. e-Devlet mezuniyet belgesi
**Soru:** Mezuniyet belgemi e-Devlet'ten alabilir miyim?
**Taslak cevap:** Evet; mezuniyet/öğrenim belgeleri e-Devlet üzerinden
alınabilir. Islak imzalı/onaylı nüsha gerekiyorsa OİDB'ye başvurulur:
https://oidb.gaziantep.edu.tr
**Kanıt:** Mevcut master SSS'nin belge cevabı (e-Devlet) + OIDB hasadı; tutarlı genelleme.
**[DOĞRULA]:** e-Devlet'te "mezun belgesi" ayrı kalem mi (YÖK belge sorgulama).

---

## İDARİ (1 bonus taslak)

### İ1. Harç ödemesi
**Soru:** Harç/katkı payı ödemesini nasıl yaparım?
**Taslak cevap:** Harç (katkı payı/öğrenim ücreti) ödemeleri OİDB'nin **"Harç
Ödemesi"** sayfasındaki yöntemlerle yapılır: https://oidb.gaziantep.edu.tr
**Kanıt:** scraped_ogrenci_isleri.md → "ÖDEMELER HARÇ ÖDEMESİ"
**[DOĞRULA]:** banka/sanal pos kanalı, kimler harç öder (ikinci öğretim/uzatan).

---

## Doğrulama yönergesi (Adım 3 — kullanıcı)

Her taslak için üç seçenek:
1. **ONAYLA** → olduğu gibi master SSS'ye taşınır (birim + link seviyesi cevap).
2. **ZENGİNLEŞTİR** → [DOĞRULA] boşluklarını gerçek bilgiyle doldur, öyle taşınır.
3. **REDDET** → taslak silinir; soru kontrol listesinde 🔴'ye döner.

Onay sonrası (ben): master SSS güncelle → `setup_regulations_db.py --recreate` +
re-ingest (orphan'lar da temizlenir) → `ANSWER_CACHE_PREFIX` v7→v8 → her yeni
kategori için stress senaryosu.
