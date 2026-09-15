-- Skema pool manager. Lihat PRD-proxy-pool.html §Model slot.

CREATE TABLE IF NOT EXISTS slots (
  id          TEXT PRIMARY KEY,
  port        INTEGER NOT NULL,
  provider    TEXT NOT NULL,          -- pia | proton | pia-custom | nord
  server      TEXT,                   -- pemilih server yang sedang terpasang
  exit_ip     TEXT,                   -- kunci dedupe antar slot
  negara      TEXT,                   -- dari ipinfo.io, buat halaman pantau
  org         TEXT,                   -- ASN, mis. "AS212238 Datacamp Limited"
  status      TEXT NOT NULL DEFAULT 'dead',  -- active | blocked | connecting | dead
  last_ok_at  TEXT,
  -- Berapa probe per jam gagal berturut-turut; direset tiap vonis 'ok'. Slot
  -- baru dicoret dari daftar terbit setelah menembus FAIL_STREAK_LIMIT, bukan
  -- di kegagalan pertama - lihat jobs._apply_hourly_verdict().
  fail_streak INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS probes (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  slot_id  TEXT NOT NULL,
  kind     TEXT NOT NULL,             -- daily | hourly
  verdict  TEXT NOT NULL,             -- ok | blocked | connecting | error
  detail   TEXT,
  at       TEXT NOT NULL
);

-- Server mana yang sudah dicoba, supaya rotasi tidak mengulang kandidat yang baru
-- saja gagal. PRIMARY KEY per (provider, server): satu baris per server, ditimpa
-- tiap kali dicoba lagi.
CREATE TABLE IF NOT EXISTS candidates (
  provider  TEXT NOT NULL,
  server    TEXT NOT NULL,
  slot_id   TEXT,
  result    TEXT,                     -- ok | blocked | dup_ip | connect_fail | probe_error
  tried_at  TEXT,
  PRIMARY KEY (provider, server)
);

-- Hasil job "Cek URL" (jobs.check_urls): satu baris per (slot, URL) per
-- jalannya job. Non-gating - tidak ada yang membaca tabel ini untuk mengubah
-- status slot; murni untuk tabel di halaman pantau + /checks.json.
CREATE TABLE IF NOT EXISTS checks (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at   TEXT NOT NULL,             -- sama untuk semua baris dari satu jalannya job
  slot_id  TEXT NOT NULL,
  exit_ip  TEXT,                      -- exit slot saat dicek; beda dari slots.exit_ip = hasil basi
  name     TEXT NOT NULL,             -- kolom name di CSV
  url      TEXT NOT NULL,
  verdict  TEXT NOT NULL,             -- ok | blocked | empty | error
  detail   TEXT,
  bukti    TEXT                       -- nama file di pool/probe-out/, dipisah koma
);
