"""GAUN Chatbot — Hibrit Yönlendirici saf mantığı (Faz 4).

Yan etkisiz fonksiyonlar (DB/ağ yok, testlerden doğrudan import edilir):
soruyu 'structural' (SQL) veya 'semantic' (RAG) diye sınıflandırır ve
yapısal sorgular için isim/birim adayı token'larını çıkarır.

bot.py bu kararlara göre MariaDB'ye (yapısal) veya Qdrant multi-collection
RAG'e (semantik) gider.
"""

import re

from rag_pipeline import normalize_turkish  # aynı Türkçe katlama kuralı


def _norm_query(text: str) -> str:
    """Soru metnini eşleştirme için hazırlar: Türkçe katlama + noktalama -> boşluk.

    normalize_turkish isim (search_name) için noktalamayı KORUR; sorularda ise
    'kaç?'/'kimdir?'/'e-postası' gibi noktalamalar sinyalleri bozar, o yüzden
    burada ayrıca temizlenir (search_name kuralına dokunmadan).

    S3 çözümü (Kiril/Arap dayanıklılığı, 2026-07-29): normalize_turkish girdiyi
    ASCII'ye zorlar (encode('ascii','ignore')) — bu, Latin/Türkçe anahtar
    kelime eşleşmesi için ŞART ama Latin-dışı alfabeleri (Kiril, Arap, vb.)
    TAMAMEN siler. Eskiden buradaki [^a-z0-9] regex'i de aynı yönde çalışıp
    saf gayri-Latin soruyu BOŞ string'e indiriyordu ('Где находится...' → '')
    ve soru sessizce yutuluyordu. Artık iki yollu:
      1) ASCII katlama bir şey üretebiliyorsa (Latin/Türkçe) → mevcut davranış
         BİREBİR korunur (anahtar kelime eşleşmesi hiç değişmez).
      2) ASCII katlama tümüyle boşsa (saf Kiril/Arap) → normalize_turkish'i
         ATLAYIP Unicode harfleri KORUYAN daha esnek bir temizlik yapılır
         (yalnız noktalama/özel karakter -> boşluk). Böylece soru boş string'e
         dönüşmez; kelimesiz de olsa metin akış içinde (kapsam kilidi/web) yaşar.
    """
    ascii_norm = re.sub(r"\s+", " ",
                        re.sub(r"[^a-z0-9\s]", " ", normalize_turkish(text))).strip()
    if ascii_norm:
        return ascii_norm
    # ASCII katlama BOŞ kaldı → girdi saf Latin-dışı (Kiril/Arap) ya da yalnız
    # noktalama. normalize_turkish'i atla, Unicode harf/rakamı KORU, sadece
    # noktalamayı temizle. \w (Unicode varsayılan) Kiril/Arap harflerini içerir.
    # NOT: ASCII '?' (Kiril soruda) normalize'da hayatta kalıp ascii_norm'u
    # yanıltıcı 'dolu' göstermesin diye kontrol TEMİZLENMİŞ ascii_norm üzerinde.
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)).strip().lower()

# İletişim/yapısal niyet sinyalleri (normalize edilmiş metinde alt-dize aranır).
CONTACT_KEYWORDS = (
    "numara", "telefon", "dahili", "eposta", "e posta", "email", "mail",
    "iletisim", "adresi", "odasi", "ofis",
)
# Kişi/rol arama sinyalleri (token olarak aranır).
PERSON_KEYWORDS = ("kim", "kimdir", "kimin", "unvani", "gorevi", "kimler")

# Birim personel listesi (kadro) sinyalleri (alt-dize aranır). "X akademik
# kadro", "X öğretim üyeleri", "X personel listesi" gibi sorular CONTACT/PERSON
# kelimelerini içermediği için yakalanmıyordu ve yanlışlıkla semantic'e düşüp
# "Bilmiyorum"a çevriliyordu (2026-07-22, "bilgisayar mühendisliği akademik
# kadro" canlı bug) — oysa resolve_roster zaten match_department ile birim
# personel dökümü çıkarabiliyor, eksik olan sadece bu yönlendirme sinyaliydi.
ROSTER_KEYWORDS = (
    "kadro", "ogretim uyesi", "ogretim uyeleri", "ogretim elemani",
    "ogretim elemanlari", "akademisyenleri", "calisanlari", "personel listesi",
)

# Kişi (personel) bilgisi arama sinyalleri. "Nihat hocanın bilgilerini ver",
# "Ali Veli hakkında bilgi", "X hocam kimdir" gibi sorular öğrenciler tarafından
# çok sık soruluyor ama CONTACT/PERSON kelimesi içermediği için semantic'e düşüp
# "Bilmiyorum"a çevriliyordu (2026-07-24 canlı bug). Bunlar YAPISAL (SQL isim
# araması) olmalı. 'hoca' + akademik unvanlar tek başına yeterli sinyaldir;
# 'bilgi/hakkında' ise ancak soruda gerçek içerik (isim) token'ı da varsa
# yapısal sayılır — çünkü isim değilse SQL boş döner ve zaten semantic'e düşer.
PERSON_TITLE_KEYWORDS = (
    "hoca", "akademisyen", "arastirma gorevlisi", "ogretim gorevlisi",
    "ogretim uyesi", "ogretim elemani", "doktor ogretim",
)
INFO_LOOKUP_KEYWORDS = ("bilgi", "hakkinda", "tanit", "ozgecmis", "cv", "iletisim bilg")

# Semantik (RAG) yol hangi Qdrant koleksiyonlarına baksın.
# YALNIZ 'regulations' (yönetmelik/SSS). 'staff' BİLİNÇLİ olarak ÇIKARILDI
# (2026-07-24): personel kimliği/iletişimi vektör-benzerliğiyle getirilince LLM
# UYDURUYORDU — "mehmet ersin akay" sorusu DB'de olmayan bu isimle, benzer
# isimli gerçek kişinin (Ahmet Ersin Erdayı, dahili 1190) bilgilerini
# birleştirip yanlış kimlik üretti. Artık personel bilgisi SADECE deterministik
# SQL'den (structural yol) gelir; SQL'de yoksa dürüstçe "bulunamadı" denir.
SEMANTIC_COLLECTIONS = ("regulations",)

# Canlı veri (LIVE_WEB) sinyalleri: cevabı anlık değişen konular. Bu kelimeler
# geçiyorsa Qdrant/cache ES GEÇİLİR, siteden anlık çekilir. (normalize edilmiş)
# NOT: çıplak "yemek" YOK — "yemekhane" (birim adı) alt-dize olarak eşleşip
# yemekhaneyle ilgili HER soruyu (turnike, misafir, kedi vb.) yanlışlıkla
# canlı-veri rotasına düşürüyordu (2026-07-22, 102 soruluk stress testte
# bulundu). Gerçek "bugün ne yeniyor" niyeti zaten bugun/yarin/menu ile yakalanır.
LIVE_KEYWORDS = ("bugun", "yarin", "menu", "guncel", "duyuru", "haber")

# Sohbet (CHITCHAT) sinyalleri: selamlaşma / hal-hatır. Girdi YALNIZ bunlardan
# (+ stopword) oluşuyorsa sohbettir; gerçek içerik varsa (örn. "merhaba yatay
# geçiş şartları") sohbet DEĞİLDİR. 'nasil' bilinçli olarak yok — "nasıl yapılır"
# gerçek sorudur; yalnız tam form 'nasilsin' sohbettir.
CHITCHAT_KEYWORDS = (
    "selam", "merhaba", "nasilsin", "nasilsiniz", "gunaydin", "tesekkur",
    "sagol", "saol", "naber", "kimsin", "hosgeldin", "iyi aksamlar",
    "iyi geceler", "gorusuruz", "hosca kal", "iyiyim",
)

# HIZLI YOL (Fast-Track) sinyalleri: GAÜN/kampüs/mevzuat/öğrenci-işleri sözlüğü.
# Soruda bunlardan HİÇBİRİ yoksa (ve yapısal/live/sohbet de değilse) soru
# üniversiteyle ilgisizdir → 'general_knowledge' (RAG ATLANIR, doğrudan web).
# TASARIM: liste KASITLI OLARAK GENİŞ tutulur — over-catch GÜVENLİ yöndedir
# (bir GAÜN sorusu yanlışlıkla eşleşirse yalnız hızlanmaz, doğru RAG yolunda
# kalır); asıl KAÇINILAN, bir GAÜN sorusunun web'e sızmasıdır. Alt-dize aranır.
_GAUN_SIGNAL_WORDS = frozenset({
    # Kurum kimliği / birim
    "gaziantep", "universite", "gaun", "gaun'", "kampus", "kampus", "rektor",
    "dekan", "mudur", "daire", "baskanlik", "koordinator", "fakulte", "bolum",
    "enstitu", "yuksekokul", "myo", "meslek", "konservatuar", "senato", "birim",
    # Öğrenci işleri / idari
    "ogrenci", "oidb", "kayit", "harc", "katki", "mezun", "diploma", "transkript",
    "belge", "disiplin", "danisman", "otomasyon", "obs", "akbis", "avesis",
    "uzem", "guzem", "bidb", "dilekce", "basvuru", "kontenjan", "yerlestirme",
    # Akademik
    "ders", "sinav", "vize", "final", "butunleme", "mazeret", "kredi", "akts",
    "ects", "mufredat", "staj", "tez", "yatay", "dikey", "gecis", "intibak",
    "akademik", "takvim", "ogretim", "egitim", "yonetmelik", "yonerge", "mevzuat",
    "not ortalamasi", "gano", "agno", "devamsizlik", "muafiyet", "onlisans",
    "lisans", "yuksek lisans", "doktora", "burslu", "okul", "derslik", "amfi",
    # Hizmetler / kampüs yaşamı (öğrencinin günlük hayatı — İMPLİCİT sorular)
    "yemek", "yemekhane", "kutuphane", "yurt", "kyk", "burs", "spor", "salon",
    "mediko", "saglik", "psikolojik", "rehberlik", "engelli", "turnike",
    "wifi", "eduroam", "ulasim", "servis", "ring", "kafeterya", "kantin",
    "araba", "arac", "park", "otopark", "otobus", "durak", "bisiklet",
    "kart", "yemekhane karti", "kimlik", "kahvalti", "tuvalet", "atm",
    "not", "but", "vize", "final",
    # Uluslararası / değişim
    "erasmus", "degisim", "mevlana", "farabi", "uluslararasi", "yabanci dil",
    # İletişim / konum / kişi (çoğu structural'da yakalanır — güvenlik ağı).
    # NOT: "nerede" BİLEREK yok — çok genel bir kelime ("dünya kupası nerede"
    # gibi genel soruları yanlışlıkla GAÜN sayardı. Kampüs konum soruları zaten
    # bir birim/yer adı (kütüphane/rektörlük/blok/kampüs) taşır.
    "rehber", "dahili", "personel", "konum", "harita", "kroki", "blok",
    "hoca", "akademisyen", "rektorluk", "dekanlik",
    # Etkinlik
    "etkinlik", "kongre", "seminer", "konferans", "mezuniyet",
})

# Yön/mekan sözcükleri: KISA bir soruda geçiyorsa (öğrenci muhtemelen KENDİ
# kampüsünü kastediyor: "arabamı nereye park edebilirim") → web'e değil kampüs
# RAG'ına (semantic) zorlanır. Token bazında aranır.
_DIRECTION_WORDS = ("nereye", "nerede", "nereden", "nasil")

# İsim/birim adayından düşülecek anlam taşımayan kelimeler.
STOPWORDS = frozenset({
    "numara", "numarasi", "numaralari", "telefon", "telefonu", "dahili",
    "dahilisi", "eposta", "email", "mail", "adresi", "iletisim",
    "kac", "kacdir", "ne", "nedir", "kim", "kimdir", "kimin", "hangi",
    "nerede", "nereye", "nereden",
    "birim", "birimi", "biriminde", "calisir", "calisiyor", "gorev", "gorevi",
    "unvan", "unvani", "ver", "soyle", "lutfen", "misin", "bana", "bir",
    "acaba", "peki", "ile", "icin", "kime", "kime", "nasil", "ulasirim",
    "in", "un", "nin", "nun", "kimler",
    # Kadro/personel listesi sinyalleri — birim adının kendisi değil, kalıp:
    # bunlar match_department'a token olarak girerse (örn. "kadro") hiçbir
    # birim adında geçmediği için eşleşme hep boş dönerdi.
    "akademik", "kadro", "kadrosu", "personel", "personeli", "listesi",
    "ogretim", "uyesi", "uyeleri", "elemani", "elemanlari", "akademisyenleri",
    "calisanlari", "hocalari",
    # Konuşma dolgu kelimeleri (sohbet tespitinde 'anlamlı içerik' sayılmasın):
    "cok", "sag", "eyvallah", "tamam", "evet", "hayir", "harika", "super",
})

# Personel İSİM araması için ekstra gürültü kelimeleri. STOPWORDS'e KONMAZ
# (örn. 'bilgi' "Bilgi İşlem" biriminin parçası — global atılırsa o birim
# eşleşmez). Bunlar YALNIZ search_staff_by_tokens çağrısından önce temizlenir:
# "Nihat hocanın bilgilerini ver" -> arama token'ı ['nihat'] (hoca/bilgi çıkar).
PERSON_QUERY_NOISE = frozenset({
    "hoca", "hocam", "hocanin", "hocasi", "hocaya", "hocayi", "hocanin",
    "akademisyen", "bilgi", "bilgisi", "bilgileri", "bilgilerini", "bilgilerin",
    "hakkinda", "hakkindaki", "tanit", "tanitir", "ozgecmis", "cv",
    "profesor", "docent", "arastirma", "gorevlisi",
    # Ofis/oda sinyalleri: "Canan Deneme odası nerede" gibi sorularda routing'i
    # (CONTACT_KEYWORDS) yapısala çeker ama bu kelimeler isim aramasına token
    # olarak sızarsa AND-eşleşmeyi kırar ("canan deneme odasi" hiçbir search_name'de
    # yok) — isim aramasından önce temizlenir ('numara'/'dahili'nin STOPWORDS'te
    # temizlenmesiyle aynı ilke; bunlar birim adlarında geçebildiği için
    # STOPWORDS yerine YALNIZ isim-araması gürültüsüne konur).
    "oda", "odasi", "odada", "odasini", "ofis", "ofisi", "ofisinde",
})

# Basit ilgi/iyelik eki kırpma: "denemenin" -> "deneme", "velinin" -> "veli".
# Türkçe karakterler _norm_query aşamasında katlandığı için suffix'ler ascii'dir.
_LONG_POSSESSIVE_SUFFIXES = ("nizin", "nuzun", "mizin", "muzun", "sinin", "sunun")
_GENITIVE_SUFFIXES = ("nin", "nun")
_SHORT_GENITIVE_SUFFIXES = ("in", "un")


def _strip_possessive_suffix(token: str) -> str:
    for suffix in _LONG_POSSESSIVE_SUFFIXES:
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[:-len(suffix)]
    for suffix in _GENITIVE_SUFFIXES:
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[:-len(suffix)]
    for suffix in _SHORT_GENITIVE_SUFFIXES:
        if len(token) > len(suffix) + 4 and token.endswith(suffix):
            return token[:-len(suffix)]
    return token


def _is_chitchat(n: str, tokens: "list[str]") -> bool:
    """Girdi YALNIZ selamlaşma/hal-hatır (+stopword) ise True.

    Selamla başlayan gerçek soruları (anlamlı içerik kalıyorsa) sohbet saymaz.
    """
    if not tokens:
        return False
    if not any(kw in n for kw in CHITCHAT_KEYWORDS):
        return False
    meaningful = [
        t for t in tokens
        if t not in STOPWORDS and not any(kw in t for kw in CHITCHAT_KEYWORDS)
    ]
    return not meaningful


# Kişi ADI formatı tespiti (anahtar kelimesiz sorular için). Öğrenci bazen
# hiçbir sinyal kelimesi olmadan yalnızca bir isim yazar ("Canan Deneme") ya da
# "Canan Deneme nerede" gibi sorar; bunlar semantic'e düşüp "Bilmiyorum"a
# çevriliyordu. Böyle sorular YAPISAL (SQL/rehber) aramaya gitmeli.
# GÜVENLİK: yalnız soru ESASEN sadece isimden ibaretse (isim dışı anlamlı içerik
# YOKKEN) tetiklenir — böylece kullanıcının rastgele büyük harflediği gerçek
# sorular ("Yatay Geçiş şartları") semantic'te kalır. Birim/yer sözcükleri isim
# sayılmaz (_NAME_EXCLUDE) — "Mühendislik Fakültesi" kadro dökümüne kaymaz.
_NAME_WORD_RE = re.compile(r"^[A-ZİŞĞÜÖÇ][a-zışğüöçı]+$")
_NAME_EXCLUDE = frozenset({
    "gaziantep", "universite", "universitesi", "gaun", "fakulte", "fakultesi",
    "muhendislik", "muhendisligi", "bolum", "bolumu", "enstitu", "enstitusu",
    "yuksekokul", "yuksekokulu", "meslek", "rektor", "rektorluk", "rektorlugu",
    "dekan", "dekanlik", "dekanligi", "mudur", "mudurluk", "mudurlugu",
    "daire", "baskanlik", "baskanligi", "merkez", "merkezi", "koordinatorluk",
    "kampus", "kutuphane", "yemekhane", "hastane", "bilgi", "islem",
    "ogrenci", "isleri", "saglik", "kultur",
})


def looks_like_person_name(question: str) -> bool:
    """Soru esasen sadece bir kişi adı mı (ör. 'Canan Deneme')?

    En uzun ardışık Title-Case (ve birim/yer sözcüğü olmayan) sözcük dizisini
    bulur; dizi >=2 sözcük VE soruda isim dışı anlamlı içerik yoksa True döner.
    Anahtar kelimeli sorular ('...numarası', '...kimdir') zaten classify_intent'te
    daha önce yakalanır; bu yalnız sinyalsiz çıplak isimler için son ağdır.
    """
    run: "list[str]" = []
    best: "list[str]" = []
    for w in question.split():
        if _NAME_WORD_RE.match(w) and normalize_turkish(w) not in _NAME_EXCLUDE:
            run.append(w)
            if len(run) > len(best):
                best = run[:]
        else:
            run = []
    if len(best) < 2:
        return False
    name_norm = {normalize_turkish(w) for w in best}
    extra = [t for t in extract_content_tokens(question) if t not in name_norm]
    return not extra


def classify_intent(question: str) -> str:
    """Soruyu dört kategoriden birine ayırır:

    * 'live'       — anlık değişen konu (yemek/duyuru/bugün...) → siteden çekilir.
    * 'structural' — iletişim/kişi araması (numara/telefon/kim...) → MariaDB SQL.
    * 'chitchat'   — yalnız selamlaşma/hal-hatır → LLM ile doğal sohbet, arama yok.
    * 'general_knowledge' — üniversiteyle İLGİSİZ genel/teknik/dış-dünya sorusu →
      RAG ATLANIR, doğrudan canlı web (Fast-Track; kaynak israfı önlenir).
    * 'semantic'   — GAÜN serbest metni (yönetmelik/SSS...) → Qdrant RAG.

    Öncelik: live > structural > chitchat > (GAÜN sinyali? semantic : general_knowledge).
    """
    n = _norm_query(question)
    if any(kw in n for kw in LIVE_KEYWORDS):
        return "live"
    if any(kw in n for kw in CONTACT_KEYWORDS):
        return "structural"
    if any(kw in n for kw in ROSTER_KEYWORDS):
        return "structural"
    # "Rektör kim?" / "rektör hakkında bilgi" gibi ROL kimliği soruları:
    # 'rektor' extract_content_tokens'ta 'REKTÖRLÜK' departman adının alt-dizesi
    # olduğu için match_department yanlışlıkla departman personel listesini
    # döndürüyordu (2026-07-22 canlı bug). Bu bir kişi-adı araması değil —
    # küratörlü RAG cevabına gitmeli. 'rektörlük telefonu' (CONTACT) yukarıda
    # zaten yakalandığı için buraya düşmez.
    if "rektor" in n and (any(t in PERSON_KEYWORDS for t in n.split())
                          or any(k in n for k in INFO_LOOKUP_KEYWORDS)):
        return "semantic"
    if any(t in PERSON_KEYWORDS for t in n.split()):
        return "structural"
    # Kişi (personel) bilgisi araması: "Nihat hocanın bilgilerini ver" gibi.
    # 'hoca'/unvan tek başına yeterli sinyaldir. 'bilgi/hakkında' ise ancak
    # soruda gerçek bir isim adayı token'ı da varsa yapısal sayılır (isim
    # değilse SQL boş döner, answer_question zaten semantic'e düşürür — güvenli).
    if any(kw in n for kw in PERSON_TITLE_KEYWORDS):
        return "structural"
    if any(kw in n for kw in INFO_LOOKUP_KEYWORDS) and extract_content_tokens(question):
        return "structural"
    if _is_chitchat(n, n.split()):
        return "chitchat"
    # Son ağ: anahtar kelimesiz çıplak kişi adı ("Canan Deneme") → yapısal.
    if looks_like_person_name(question):
        return "structural"
    # KISA/KİŞİSEL YAKALAYICI: kısa (<5 kelime) + yön/mekan sorusu (nereye/nerede/
    # nasıl) muhtemelen KAMPÜS hayatıyla ilgilidir ("arabamı nereye park
    # edebilirim") — anahtar kelime içermese bile web'e DEĞİL, semantic (kampüs
    # RAG) yönüne zorlanır. Aksi halde ABD park bilgisi gibi alakasız web sonucu
    # dönüyordu (2026-07-28 canlı bug).
    tokens = n.split()
    if len(tokens) < 5 and any(d in tokens for d in _DIRECTION_WORDS):
        return "semantic"
    # FAST-TRACK: buraya kadar hiçbir yapısal/live/sohbet/isim/kampüs-mekan
    # sinyali yok. Soruda GAÜN/kampüs sözlüğünden EN UFAK iz bile yoksa
    # üniversiteyle ilgisizdir → general_knowledge (RAG atlanır). En küçük GAÜN
    # izinde güvenli tarafta kalıp semantic (yerel RAG önce) döneriz.
    if not any(w in n for w in _GAUN_SIGNAL_WORDS):
        return "general_knowledge"
    return "semantic"


def needs_gaun_scope(question: str) -> bool:
    """Soru üniversite/kampüs/akademik/idari mi (→ web araması gaziantep.edu.tr'ye
    İZOLE edilir, başka üniversite verisi karışmasın) yoksa günlük hayat/genel
    kültür mü (→ saf internet araması)? general_knowledge = günlük hayat."""
    return classify_intent(question) != "general_knowledge"


def live_topic(question: str) -> str:
    """Canlı sorgunun alt konusunu döndürür: 'yemek' veya 'duyuru'.

    live_fetcher.fetch_live_data bu konuya göre doğru seed URL'yi seçer.
    Belirsiz (bugün/güncel) durumlarda varsayılan 'duyuru'dur.
    """
    n = _norm_query(question)
    if any(k in n for k in ("yemek", "menu")):
        return "yemek"
    return "duyuru"


def extract_content_tokens(question: str) -> "list[str]":
    """Sorudan isim/birim adayı token'ları (stopword'süz, >=3 harf)."""
    tokens = []
    for raw in _norm_query(question).split():
        token = _strip_possessive_suffix(raw)
        if len(token) >= 3 and token not in STOPWORDS:
            tokens.append(token)
    return tokens


# Birim adlarında sık geçen jenerik ekler — kullanıcılar birimi genelde bunlar
# OLMADAN söyler ("Bilgi İşlem" ~ "Bilgi İşlem Daire Bşk."), o yüzden eşleşme
# çekirdeğinden çıkarılır (match_department).
_GENERIC_UNIT_WORDS = frozenset({
    "daire", "baskanligi", "baskanlik", "bsk", "mudurlugu", "mudurluk",
    "fakultesi", "bolumu", "enstitusu", "yuksekokulu", "merkezi",
    "rektorlugu", "dekanligi", "koordinatorlugu", "birimi",
})


def _dept_word_matches(core: str, q_words: "list[str]") -> bool:
    """Birim adı kelimesi soruda (kelime bazında) var mı?

    Birebir eşleşme her zaman yeterli. Uzun (≥7 harf) kelimeler için ayrıca
    ilk 7 harfi ortak olan bir soru kelimesi de kabul edilir — bu, "mühendisliği"
    (birim adı) ile "mühendislerini" (kullanıcının doğal dilde kullandığı çekim)
    gibi aynı kökten farklı ek almış biçimleri birbirine bağlar. Eşik bilerek
    7 harf tutuldu: "bilgi" (5 harf) gibi kısa kelimelerin "bilgisayar" (10
    harf, apayrı bir birim) ile yanlışlıkla eşleşmesini engeller — kısa
    kelimeler yalnız BİREBİR eşleşirse kabul edilir.
    """
    if core in q_words:
        return True
    if len(core) >= 7:
        return any(len(w) >= 7 and core[:7] == w[:7] for w in q_words)
    return False


# Birim TÜRÜ (fakülte/enstitü/…) → o türü gösteren ad varyantları. Ad çekirdeği
# jenerik ekleri (fakültesi vb.) attığı için "eğitim fakültesi" ile "eğitim
# koordinatörlüğü" çekirdek düzeyinde aynı ('egitim') görünür; tür sinyali burada
# ayrı tutulur ki fakülte sorusu KOORDİNATÖRLÜĞE düşmesin (2026-07-27 canlı bug).
# Yalnız adında türünü GÜVENİLİR biçimde taşıyan üst-düzey birimler; 'bölüm'
# BİLEREK dışarıda — bölüm adları çoğu zaman "…Bölümü" sözcüğünü taşımaz
# ("EĞİTİM BİLİMLERİ") ve tür süzgeci onları yanlışlıkla eler.
_UNIT_TYPE_VARIANTS = {
    "fakulte": ("fakulte", "fakultesi", "fak"),
    "enstitu": ("enstitu", "enstitusu"),
    "yuksekokul": ("yuksekokul", "yuksekokulu", "myo"),
    "koordinatorluk": ("koordinatorluk", "koordinatorlugu"),
    "daire": ("daire",),
    "merkez": ("merkez", "merkezi"),
    "konservatuar": ("konservatuar", "konservatuari"),
    "rektorluk": ("rektorluk", "rektorlugu"),
}
# Ana kampüs örtük varsayılan: kullanıcı "eğitim fakültesi" derken (kampüs
# belirtmeden) Gaziantep merkez kampüsünü kasteder. Bu kelimeler birim adının
# çekirdeğinde olsa da soruda geçmesi ZORUNLU değildir; diğer kampüsler
# (Nizip/Afrin/İslahiye…) ise ancak açıkça anılırsa eşleşir.
_DEFAULT_CAMPUS_WORDS = frozenset({"gaziantep", "gaun"})


def _unit_types(words: "list[str]") -> "set[str]":
    """Kelime listesindeki birim-türü sinyallerini (kanonik) döndürür.

    _dept_word_matches ile aynı 7-harf ön ek toleransını kullanır ki sorudaki
    çekimli biçim ('fakültesindeki') de tür olarak yakalansın."""
    found: "set[str]" = set()
    for w in words:
        for canon, variants in _UNIT_TYPE_VARIANTS.items():
            if w in variants or (
                len(w) >= 7
                and any(len(v) >= 7 and w[:7] == v[:7] for v in variants)
            ):
                found.add(canon)
    return found


def match_department(question: str, departments: "list[dict]") -> "dict | None":
    """Bir birimin adının ÇEKİRDEK (jenerik ek hariç) kelimeleri soruda —
    aralarında başka kelime olsa da — geçiyorsa o birimi döndürür. Birden çok
    eşleşmede en çok çekirdek kelime içeren (en spesifik) birim seçilir.

    Yön KASITLI olarak "birim adının kelimeleri soruda var mı" yönündedir
    (eskiden tersiydi: sorunun TÜM kelimelerinin birim adında olması
    aranıyordu). Eski yön, "emin misin bir daha bak araştır ... bilgisayar
    mühendislerini bul" gibi doğal takip sorularındaki fazladan kelimeler
    (bak/araştır/üniversitedeki) yüzünden asıl sinyal (bilgisayar
    mühendisliği) soruda net biçimde olsa bile eşleşmeyi KIRIYORDU
    (2026-07-22 canlı bug — kullanıcı ilk soruda doğru cevabı aldı, takip
    sorusunda "Bilmiyorum"a düştü). Yeni yön, sorudaki fazladan kelimelere
    dokunmadan sadece birim adının gerçekten geçip geçmediğine bakar.

    İki ek disiplin (2026-07-27 canlı bug — "eğitim fakültesindeki akademisyenler"
    tek kişilik EĞİTİM KOORDİNATÖRLÜĞÜNE düşüyordu):
      1) TÜR süzgeci: soru bir birim türü ('fakülte'/'enstitü'/…) içeriyorsa
         aday da AYNI türden olmalı — fakülte sorusu koordinatörlüğe eşleşmez.
      2) Ana kampüs varsayılanı: 'gaziantep' çekirdek kelimesi soruda geçmese de
         eşleşmeyi kırmaz; böylece kampüs belirtmeyen "eğitim fakültesi" merkez
         GAZİANTEP EĞİTİM FAKÜLTESİne düşer, diğer kampüsler (Nizip/Afrin) ise
         yalnız açıkça anıldığında gelir.

    departments: [{'id':..., 'name':...}, ...] (bot.py MariaDB'den yükler).
    """
    n = _norm_query(question)
    if not n:
        return None
    q_words = n.split()
    query_types = _unit_types(q_words)
    best = None
    best_score = None
    for dept in departments:
        name_n = _norm_query(dept["name"])
        words = name_n.split()
        # (1) Tür uyumu: soru bir tür belirtiyorsa aday o türden OLMALI.
        if query_types and not (query_types & _unit_types(words)):
            continue
        core = [t for t in words if len(t) >= 3 and t not in _GENERIC_UNIT_WORDS]
        # (2) Ana kampüs (gaziantep) örtük varsayılan → zorunlu kelimelerden düşer.
        required = [t for t in core if t not in _DEFAULT_CAMPUS_WORDS]
        if not required or not all(_dept_word_matches(t, q_words) for t in required):
            continue
        score = (len(required), len(name_n))
        if best is None or score > best_score:
            best, best_score = dept, score
    return best
