import contextlib
import pathlib
import sqlite3

from . import config

_SCHEMA = (pathlib.Path(__file__).parent / "schema.sql").read_text()


@contextlib.contextmanager
def connect():
    # sqlite3.Connection's own context manager only commits/rolls back the
    # transaction on exit, it does NOT close the connection - `with
    # sqlite3.connect(...)` leaks a connection per call. Wrap it so callers
    # get one that actually closes.
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


# Kolom yang ditambahkan ke `slots` setelah skema awal (Fase 2: negara/ASN
# buat halaman pantau; fail_streak menyusul buat toleransi probe per jam).
# `CREATE TABLE IF NOT EXISTS` tidak menyentuh tabel yang sudah ada, jadi
# pool.db lama tidak otomatis dapat kolom baru - ketahuan lewat
# OperationalError "no such column" saat rotate. Guard ini yang seharusnya
# ada sejak awal, bukan instruksi "hapus pool.db manual".
_NEW_SLOT_COLUMNS = (
    ("negara", "TEXT"),
    ("org", "TEXT"),
    # NOT NULL boleh di ALTER TABLE ADD COLUMN selama DEFAULT-nya bukan NULL;
    # baris lama langsung terisi 0, jadi tidak perlu UPDATE susulan.
    ("fail_streak", "INTEGER NOT NULL DEFAULT 0"),
)


def _migrate(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(slots)")}
    for col, decl in _NEW_SLOT_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE slots ADD COLUMN {col} {decl}")


def init():
    with connect() as conn:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        for slot_id, provider, port in config.SLOT_DEFS:
            conn.execute(
                "INSERT INTO slots (id, port, provider, status) VALUES (?, ?, ?, 'dead') "
                "ON CONFLICT(id) DO UPDATE SET port=excluded.port, provider=excluded.provider",
                (slot_id, port, provider),
            )
        conn.commit()
