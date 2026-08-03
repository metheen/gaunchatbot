# GaunAI Veri Kontrol Listesi — kampus_master_sss.md Kapsam Denetimi

Fable-5 / **Learning** fazı çıktısı. Tarih: 2026-07-13.
Kaynak taban: `offline_data/kampus_master_sss.md` (18 SSS + 4 kişi kaydı) +
`gaunai_egitim_soru_bankasi.md` (~25 jargon-Q&A) + 21 taze `scraped_*.md` hasadı.

Durum etiketleri:
- ✅ **DOLU** — doğrulanmış cevap master SSS'de mevcut
- ⚠️ **TASLAK VAR** — hasat kanıtına dayalı taslak `sss_taslak_cevaplar.md`'de, **kullanıcı doğrulaması bekliyor**
- 🔴 **EKSİK** — cevap yok, taslak da üretilemedi (kaynak önerisi verildi)

> Değişmez kural: **Doğrulanmamış hiçbir şey SSS'ye girmez.** ⚠️ taslaklar
> yalnız doğrulama sonrası (Adım 3) master SSS'ye taşınır.

> **GÜNCELLEME (2026-07-13, Adım 3 sonucu):** Kullanıcı S1-S5 ve M1-M4'ü
> ONAYLADI → master SSS'ye taşındı (aşağıdaki tablolarda bu 9 madde artık ✅).
> **M5** de kullanıcının gönderdiği zenginleştirilmiş detayla (YÖKSİS onayı,
> Geçici Mezuniyet Belgesi geçerliliği) eklendi → ✅. **İ1** (harç) onay
> kapsamı dışında, beklemede. Sosyal 8✅, Mezuniyet 6✅ oldu.

---

## 1. Akademik — 🟢 güçlü (8 dolu)

| Soru | Durum | Kaynak |
|---|---|---|
| Çan eğrisi / bağıl değerlendirme | ✅ | Yönetmelik (SSS) |
| FF/FD/NA ders tekrarı | ✅ | Yönetmelik (SSS) |
| Sınav itirazı (5 iş günü) | ✅ | Yönetmelik (SSS) |
| Mazeret sınavı | ✅ | Yönetmelik (SSS) |
| GANO yetersizliği / şeref | ✅ | Yönetmelik (SSS) |
| Devam zorunluluğu | ✅ | Yönetmelik (SSS) |
| Ders kaydı yapılmazsa | ✅ | Yönetmelik (SSS) |
| Yatay geçiş şartları | ✅ | Yönetmelik (SSS) |
| Çift anadal / yandal **prosedürü** | 🔴 | OIDB "Çiftanadal/Yandal Başvuru Formu" sayfası (hasatta form adı doğrulandı) |
| Ders saydırma / muafiyet | 🔴 | OIDB + birim yönetim kurulu |

## 2. İdari (Öğrenci İşleri) — 🟡 orta (4 dolu)

| Soru | Durum | Kaynak |
|---|---|---|
| Öğrenci belgesi / transkript | ✅ | SSS (OIDB + e-Devlet) |
| İlişik kesme şartları | ✅ | Yönetmelik (SSS) |
| Kayıt dondurma | ✅ | Yönetmelik (SSS) |
| Harç / katkı payı ödemesi | ⚠️ | OIDB "Harç Ödemesi" sayfası hasatta doğrulandı |
| Askerlik tecil işlemleri | 🔴 | OIDB |
| Yatay geçiş başvuru takvimi | 🔴 | OIDB duyuruları (dönemlik) |

## 3. Teknik (BİDB) — 🟡 orta (3 dolu)

| Soru | Durum | Kaynak |
|---|---|---|
| Eduroam bağlantısı | ✅ | SSS + eduroam_rehber.txt |
| Kurumsal şifre sıfırlama | ✅ | SSS |
| OBS / DUYSİS erişimi | ✅ | SSS |
| VPN (kampüs dışı erişim) | 🔴 | BİDB |
| Ücretsiz lisanslı yazılım (Office vb.) | 🔴 | BİDB |

## 4. Sosyal — 🟠 → hedef 🟢 (EN YÜKSEK ÖNCELİK #1)

| Soru | Durum | Kaynak |
|---|---|---|
| Yemekhane saatleri/menü | ✅ | SSS + canlı yemek rotası |
| Ring servis saatleri | ✅ | SSS (SKS) |
| Yurt/KYK barınma | ✅ | SSS (yönlendirme) |
| **Kısmi zamanlı (part-time) öğrenci çalışma** | ⚠️ | SKS "Kısmi Zamanlı Öğrenci Birimi" hasatta doğrulandı |
| **Psikolojik danışmanlık** | ⚠️ | SKS "Psikolojik Danışma ve Rehberlik Merkezi" hasatta doğrulandı |
| **Beslenme/diyet danışmanlığı** | ⚠️ | SKS "Beslenme ve Diyet Danışma Merkezi" hasatta doğrulandı |
| **Spor tesisleri (kullanım)** | ⚠️ | SKS kapalı/açık tesis, çim saha, tenis kortu hasatta doğrulandı |
| **Öğrenci kulüpleri/toplulukları** | ⚠️ | SKS "Kültür Şube Müdürlüğü" hasatta doğrulandı |
| Burslar (KYK dışı / üniversite bursları) | 🔴 | SKS + Rektörlük duyuruları |
| Etkinlik takvimi | 🔴 | SKS Kültür Şube + ana sayfa duyuruları |

## 5. Mezuniyet — 🔴 → hedef 🟢 (EN YÜKSEK ÖNCELİK #2 — kategori hiç yoktu)

| Soru | Durum | Kaynak |
|---|---|---|
| **Diplomamı ne zaman/nereden alırım?** | ⚠️ | OIDB (hasatta diploma işlemleri menüsü doğrulandı) |
| **Diplomamı kaybettim (ikinci nüsha)** | ⚠️ | OIDB "İkinci Nüsha Diploma İşlemleri" hasatta doğrulandı |
| **Mezuniyet için ilişik kesme prosedürü** | ⚠️ | OIDB "İlişik Kesme Formu" hasatta doğrulandı |
| **Mezun portalı nedir?** | ⚠️ | OIDB "Mezun Portalı" hasatta doğrulandı |
| e-Devlet mezun belgesi | ⚠️ | OIDB + e-Devlet (mevcut transkript cevabıyla tutarlı) |
| Mezuniyet töreni (tarih/kayıt) | 🔴 | Ana sayfa duyuruları / SKS Kültür Şube (yıllık) |
| Diploma eki (Diploma Supplement) | 🔴 | OIDB / Bologna sayfası (hasatta "Bologna Bilgi Sistemi" görüldü) |
| Mezuniyet not ortalaması şartı | ✅ | GANO sorusunda mevcut ("en az 2,00") |

---

## Özet sayaç

| Kategori | ✅ | ⚠️ taslak | 🔴 eksik |
|---|---|---|---|
| Akademik | 8 | 0 | 2 |
| İdari | 3 | 1 | 2 |
| Teknik | 3 | 0 | 2 |
| **Sosyal** | 3 | **5** | 2 |
| **Mezuniyet** | 1 | **5** | 2 |
| **Toplam** | **18** | **11** | **10** |

Sonraki adım: ⚠️ 11 taslak `docs/sss_taslak_cevaplar.md`'de → kullanıcı doğrulaması
(Adım 3) → onaylananlar master SSS'ye → `--recreate` + re-ingest + yeni stress senaryoları.
