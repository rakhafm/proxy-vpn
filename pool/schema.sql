-- Skema pool manager. Lihat PRD-proxy-pool.html §Model slot.

CREATE TABLE IF NOT EXISTS slots (
  id          TEXT PRIMARY KEY,
  port        INTEGER NOT NULL,
  provider    TEXT NOT NULL,          -- pia | proton
  server      TEXT,                   -- pemilih server yang sedang terpasang
  exit_ip     TEXT,                   -- kunci dedupe antar slot
  negara      TEXT,                   -- dari ipinfo.io, buat halaman pantau
  org         TEXT,                   -- ASN, mis. "AS212238 Datacamp Limited"
  status      TEXT NOT NULL DEFAULT 'dead',  -- active | blocked | connecting | dead
  last_ok_at  TEXT
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
