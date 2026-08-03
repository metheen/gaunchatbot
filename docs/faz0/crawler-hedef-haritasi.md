# Crawler Hedef Haritası — GAUN Chatbot (Faz 0)

**VARSAYIM (V5):** Alan adı `gaun.edu.tr` ve alt alan adı desenleri Türk devlet
üniversitelerinin tipik bilgi mimarisine göre çıkarılmıştır. Faz 0 kapanışında
her URL canlı sitede doğrulanmalı, gerçek sitemap/robots.txt incelenmelidir.

## Şablon (parser) tipleri

| Tip | Adı | Hedef depo | Açıklama |
|-----|-----|-----------|----------|
| T1 | Duyuru liste + detay | Qdrant (RAG) | Tarih metadata'sı zorunlu (bayatlık kontrolü) |
| T2 | Kadro/personel tablosu | MariaDB `staff` + `departments` | Ad, unvan, görev, dahili, e-posta, profil linki |
| T3 | Statik bilgi sayfası | Qdrant (RAG) | Hakkında, SSS, hizmet sayfaları |
| T4 | PDF doküman | Qdrant (RAG) | Yönetmelik/yönerge çoğunlukla PDF — pipeline'a PDF metin çıkarımı gerekli |
| T5 | Yemek menüsü tablosu | Qdrant (RAG, tarih metadata'lı) — ileride `menus` tablosu (roadmap) | Günlük değişir |
| T6 | Telefon rehberi | MariaDB `staff.phone_internal` + `departments.phone_internal` | En kritik yapısal kaynak |

## P1 — Faz 1 pilotu (personel iletişim ağı ağırlıklı)

| Kaynak | Beklenen URL deseni | Şablon | Sıklık |
|--------|--------------------|--------|--------|
| Telefon rehberi | `rehber.gaun.edu.tr` veya `www.gaun.edu.tr/rehber` | T6 | Haftalık |
| AVESİS akademik profiller | `avesis.gaun.edu.tr` (varsa — en temiz yapısal kaynak) | T2 | Haftalık |
| Fakülte akademik kadro sayfaları | `<fakulte>.gaun.edu.tr/akademik-kadro` (tüm fakültelerde aynı şablon beklenir) | T2 | Haftalık |
| Bölüm kadro sayfaları | `<fakulte>.gaun.edu.tr/<bolum>/kadro` | T2 | Haftalık |
| Daire başkanlıkları personel/iletişim | `bidb.gaun.edu.tr/personel`, `oidb...`, `sks...`, `pdb...`, `imid...` + `/iletisim` | T2/T3 | Haftalık |
| Birim listesi / organizasyon şeması | `www.gaun.edu.tr` (Fakülteler, İdari Birimler menüleri) | T3 | Aylık — `departments` hiyerarşisinin kaynağı |

## P2 — Faz 2–3 (RAG içeriği)

| Kaynak | Beklenen URL deseni | Şablon | Sıklık |
|--------|--------------------|--------|--------|
| Ana site duyuruları | `www.gaun.edu.tr/duyurular` | T1 | Günde 2× |
| Birim duyuruları | `<birim>.gaun.edu.tr/duyurular` | T1 | Günlük |
| OİDB: yönetmelik/yönerge | `oidb.gaun.edu.tr/mevzuat` (PDF ağırlıklı) | T4 | Aylık |
| OİDB: akademik takvim | `oidb.gaun.edu.tr/akademik-takvim` | T3 | Haftalık |
| OİDB: SSS | `oidb.gaun.edu.tr/sss` | T3 | Aylık |
| SKS: yemek menüsü | `sks.gaun.edu.tr/yemek-menusu` | T5 | Günlük |
| SKS: burs, spor tesisleri | `sks.gaun.edu.tr/...` | T3 | Aylık |
| Kütüphane: saatler, kurallar | `kutuphane.gaun.edu.tr` | T3 | Aylık |
| Erasmus / Dış İlişkiler | `erasmus.gaun.edu.tr` veya `international...` | T1/T3 | Haftalık |
| Sağlık merkezi (mediko) | `sks.gaun.edu.tr/saglik` veya ayrı birim sayfası | T3 | Aylık |

## P3 — Faz 4+ (uzun kuyruk)

Enstitüler, MYO'lar, araştırma/uygulama merkezleri, sürekli eğitim merkezi,
üniversite genel mevzuat arşivi, ulaşım/ring servis sayfaları. Aylık tam tarama.

## Hariç tutulanlar (asla taranmaz)

- `obs.gaun.edu.tr` (öğrenci bilgi sistemi — login arkası, kişisel veri)
- `mail.gaun.edu.tr`, webmail, EBYS/doküman yönetim sistemleri
- LMS/uzaktan eğitim (`uzem`, moodle) — login arkası ders içerikleri
- Login gerektiren her sayfa; form POST edilmez, yalnız GET
- Kişisel veri içerip kurumsal olmayan içerikler (öğrenci listeleri vb.)

## Tarama görgü kuralları

- `robots.txt`'e mutlak uyum; istekler arası ≥1 sn bekleme; gece saatlerinde çalıştırma tercih edilir (kendi üniversitemizin sunucusunu yormamak için).
- Her sayfanın içerik hash'i saklanır (`crawl_pages`, Faz 1 migration'ı); değişmeyen sayfa yeniden işlenmez.
- Parser başına "0 kayıt döndü" alarmı: site şablonu değişince sessiz veri kaybı yerine uyarı üretilir.
- 404 dönen kaynaklar: `staff`/`departments` kayıtları silinmez, `is_active=0` yapılır; Qdrant chunk'ları düşülür.

## Faz 0 kapanış aksiyonları

1. Harita ekibinden geçerli `hedef` slug listesini al → `map_targets`'a seed et (sözleşme: chatbot yalnız bu slug'larla URL üretir).
2. Bu dosyadaki tüm URL desenlerini canlı sitede doğrula (sitemap.xml + robots.txt incele); gerçek fakülte/birim subdomain envanterini çıkar.
3. Kadro sayfalarından 2–3 örnek HTML'i fikstür olarak kaydet (parser birim testlerinin temeli).
4. Rehber ↔ kadro sayfası isim çakıştırma stratejisini netleştir (`search_name` üzerinden merge — aynı kişi iki kaynakta farklı yazımla gelebilir).
