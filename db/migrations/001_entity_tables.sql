-- =====================================================================
-- 001_entity_tables.sql — GAUN Chatbot, Faz 0 yapısal veri katmanı
-- Idempotent: tüm ifadeler tekrar tekrar çalıştırılabilir
-- (IF NOT EXISTS / INSERT ... ON DUPLICATE KEY UPDATE).
--
-- Mimari kararlar (Faz 0):
--   * Personel oda/kat bilgisi TUTULMAZ — konum sorusu harita uygulamasına
--     yönlendirilir: kampus.gaun.edu.tr/harita?hedef=<map_targets.slug>
--   * staff ve departments crawler tarafından beslenir (manuel giriş yok);
--     first_seen_at / last_seen_at yaşam döngüsünü yönetir.
--   * map_targets: harita uygulamasının tanıdığı hedeflerin whitelist'i.
--     Chatbot harita URL'sini SADECE bu slug'larla üretir (uydurma slug
--     ve URL injection engeli). Slug listesi harita ekibinden alınır.
--
-- Collation utf8mb4_turkish_ci: Türkçe eşleştirme (İ/i, I/ı) doğru çalışır.
-- =====================================================================

CREATE DATABASE IF NOT EXISTS gaun_assistant
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_turkish_ci;

USE gaun_assistant;

-- ---------------------------------------------------------------------
-- 1) map_targets — harita uygulamasının geçerli hedef sözlüğü (whitelist)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS map_targets (
  id           SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
  slug         VARCHAR(100)      NOT NULL,  -- harita app 'hedef' parametresinin birebir değeri
  display_name VARCHAR(255)      NOT NULL,
  category     ENUM('akademik_birim','idari_birim','yemekhane','kafeterya',
                    'kutuphane','saglik','spor','yurt','atm_banka',
                    'ulasim','diger') NOT NULL DEFAULT 'diger',
  is_active    TINYINT(1)        NOT NULL DEFAULT 1,
  created_at   TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP         NOT NULL DEFAULT CURRENT_TIMESTAMP
                                 ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_map_targets_slug (slug),
  KEY idx_map_targets_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_turkish_ci;

-- ---------------------------------------------------------------------
-- 2) departments — birimler (crawler beslemeli, hiyerarşik)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS departments (
  id             INT UNSIGNED NOT NULL AUTO_INCREMENT,
  parent_id      INT UNSIGNED NULL,          -- Fakülte > Bölüm, Rektörlük > Daire Bşk.
  slug           VARCHAR(255) NOT NULL,      -- deterministik upsert anahtarı
  name           VARCHAR(255) NOT NULL,
  dept_type      ENUM('rektorluk','genel_sekreterlik','fakulte','enstitu',
                      'yuksekokul','meslek_yuksekokulu','bolum','anabilim_dali',
                      'daire_baskanligi','sube_mudurlugu','koordinatorluk',
                      'arastirma_merkezi','diger') NOT NULL DEFAULT 'diger',
  map_target_id  SMALLINT UNSIGNED NULL,     -- NULL ise cevapta üst birimin hedefi kullanılır
  phone          VARCHAR(30)  NULL,
  phone_internal VARCHAR(30)  NULL,
  email          VARCHAR(150) NULL,
  website_url    VARCHAR(500) NULL,
  source_url     VARCHAR(500) NULL,          -- verinin kazındığı sayfa (izlenebilirlik)
  is_active      TINYINT(1)   NOT NULL DEFAULT 1,
  first_seen_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                              ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_departments_slug (slug),
  KEY idx_departments_parent (parent_id),
  KEY idx_departments_type (dept_type),
  CONSTRAINT fk_departments_parent
    FOREIGN KEY (parent_id) REFERENCES departments (id)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT fk_departments_map_target
    FOREIGN KEY (map_target_id) REFERENCES map_targets (id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_turkish_ci;

-- ---------------------------------------------------------------------
-- 3) staff — personel iletişim ağı (tamamen crawler beslemeli)
--    KVKK gereği yalnız kurumsal/kamuya açık alanlar; şemada bilinçli
--    olarak ev adresi / şahsi telefon / fotoğraf alanı YOKTUR.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staff (
  id             INT UNSIGNED NOT NULL AUTO_INCREMENT,
  external_key   VARCHAR(255) NOT NULL,      -- deterministik upsert anahtarı: profil URL'i, yoksa sha1(source_url + search_name)
  full_name      VARCHAR(200) NOT NULL,
  search_name    VARCHAR(200) NOT NULL,      -- küçük harf + TR karakter katlama: 'Çelik Yıldız' -> 'celik yildiz' (karaktersiz aramayı çözer)
  academic_title VARCHAR(100) NULL,          -- 'Prof. Dr.', 'Öğr. Gör.'
  role_title     VARCHAR(150) NULL,          -- 'Bilgi İşlem Daire Başkanı', 'Dekan'
  department_id  INT UNSIGNED NULL,
  phone          VARCHAR(30)  NULL,
  phone_internal VARCHAR(30)  NULL,
  email          VARCHAR(150) NULL,
  profile_url    VARCHAR(500) NULL,          -- AVESİS / kişisel kurumsal sayfa
  source_url     VARCHAR(500) NULL,
  is_active      TINYINT(1)   NOT NULL DEFAULT 1,  -- crawl'da görünmeyen kayıt silinmez, pasife çekilir
  first_seen_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
                              ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_staff_external_key (external_key),
  KEY idx_staff_search_name (search_name),
  KEY idx_staff_department (department_id),
  KEY idx_staff_role (role_title),
  CONSTRAINT fk_staff_department
    FOREIGN KEY (department_id) REFERENCES departments (id)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_turkish_ci;

-- ---------------------------------------------------------------------
-- 4) entity_aliases — intent router'ın ad eşleştirme sözlüğü
--    ('mediko', 'bidb', 'oidb', 'yemekhane', 'karnım aç' HARİÇ — o intent,
--     alias değil). Polimorfik olduğu için gerçek FK kurulamaz; bütünlük
--    servis katmanında + aşağıdaki öksüz-kayıt sorgusuyla korunur.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entity_aliases (
  id               INT UNSIGNED NOT NULL AUTO_INCREMENT,
  entity_type      ENUM('staff','department','map_target') NOT NULL,
  entity_id        INT UNSIGNED NOT NULL,
  alias            VARCHAR(255) NOT NULL,
  alias_normalized VARCHAR(255) NOT NULL,    -- search_name ile aynı katlama kuralı
  source           ENUM('manual','crawler') NOT NULL DEFAULT 'manual',
  created_at       TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_alias (entity_type, alias_normalized),
  KEY idx_alias_lookup (alias_normalized)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_turkish_ci;

-- Haftalık bütünlük kontrolü (öksüz alias tespiti) — işletme sorgusu:
--   SELECT a.* FROM entity_aliases a
--   LEFT JOIN departments d ON a.entity_type='department' AND a.entity_id=d.id
--   LEFT JOIN staff s        ON a.entity_type='staff'      AND a.entity_id=s.id
--   LEFT JOIN map_targets m  ON a.entity_type='map_target' AND a.entity_id=m.id
--   WHERE COALESCE(d.id, s.id, m.id) IS NULL;

-- =====================================================================
-- ÖRNEK TOHUM VERİLERİ — şema kullanımını belgelemek içindir.
-- Gerçek veri: map_targets -> harita ekibinin slug listesinden,
-- departments/staff -> Faz 1+ crawler'dan gelecek. '0000' değerleri dummy.
-- =====================================================================

INSERT INTO map_targets (slug, display_name, category) VALUES
  ('merkezi-yemekhane', 'Merkezi Yemekhane',            'yemekhane'),
  ('kutuphane',         'Merkez Kütüphane',             'kutuphane'),
  ('bidb',              'Bilgi İşlem Daire Başkanlığı', 'idari_birim')
ON DUPLICATE KEY UPDATE
  display_name = VALUES(display_name),
  category     = VALUES(category);

-- NOT: Sahte 'Canan Deneme' / dummy 'Bilgi İşlem' departments+staff tohumları
-- KALDIRILDI — gerçek crawler verisi (staff/departments) ile çakışıp RAG
-- cevaplarını kirletiyordu (dahili '0000'). staff ve departments YALNIZCA
-- crawler_rehber.py --write ile doldurulur. map_targets ve alias tohumları
-- illüstratiftir ve RAG'e girmez; harita ekibinin gerçek slug listesiyle
-- güncellenene kadar örnek olarak kalır.

INSERT INTO entity_aliases (entity_type, entity_id, alias, alias_normalized)
SELECT 'map_target', m.id, 'Yemekhane', 'yemekhane'
FROM map_targets m WHERE m.slug = 'merkezi-yemekhane'
ON DUPLICATE KEY UPDATE alias = VALUES(alias);
