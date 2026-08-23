"""SQLite storage. One file, WAL mode, safe for the scraper and Flask to share."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id              TEXT PRIMARY KEY,
    url             TEXT NOT NULL,
    title           TEXT NOT NULL,
    price           INTEGER,
    image           TEXT,
    location        TEXT,
    city            TEXT,
    band            TEXT,
    state           TEXT,
    date_text       TEXT,
    posted_at       TEXT,
    model_key       TEXT,
    model_name      TEXT,
    brand           TEXT,
    vram            INTEGER,
    perf            REAL,
    kind            TEXT,
    flags           TEXT DEFAULT '[]',
    reference_price INTEGER,
    ref_source      TEXT,
    clearing_price  INTEGER,
    expected_hours  REAL,
    discount        REAL,
    perf_per_1k     REAL,
    deal_score      REAL,
    deal_class      TEXT,
    suspect         INTEGER DEFAULT 0,
    first_seen      TEXT,
    last_seen       TEXT,
    seen_count      INTEGER DEFAULT 1,
    price_drops     INTEGER DEFAULT 0,
    initial_price   INTEGER,
    gone            INTEGER DEFAULT 0,
    missed_sweeps   INTEGER DEFAULT 0,
    gone_at         TEXT,
    tenure_hours    REAL,
    outcome         TEXT,
    alerted         INTEGER DEFAULT 0,
    via_query       TEXT
);
CREATE INDEX IF NOT EXISTS ix_listings_score ON listings(deal_score DESC);
CREATE INDEX IF NOT EXISTS ix_listings_model ON listings(model_key, price);
CREATE INDEX IF NOT EXISTS ix_listings_seen  ON listings(last_seen DESC);
CREATE INDEX IF NOT EXISTS ix_listings_city  ON listings(city);
CREATE INDEX IF NOT EXISTS ix_listings_outcome ON listings(outcome, model_key);

CREATE TABLE IF NOT EXISTS price_history (
    listing_id TEXT NOT NULL,
    price      INTEGER NOT NULL,
    ts         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hist_listing ON price_history(listing_id, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT,
    ts         TEXT,
    deal_score REAL,
    reason     TEXT,
    channels   TEXT,
    title      TEXT,
    price      INTEGER,
    url        TEXT
);
CREATE INDEX IF NOT EXISTS ix_alerts_ts ON alerts(ts DESC);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started     TEXT,
    finished    TEXT,
    requests    INTEGER DEFAULT 0,
    http_errors INTEGER DEFAULT 0,
    cards       INTEGER DEFAULT 0,
    matched     INTEGER DEFAULT 0,
    new         INTEGER DEFAULT 0,
    price_drops INTEGER DEFAULT 0,
    alerted     INTEGER DEFAULT 0,
    note        TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=15000")
    return con


def init() -> None:
    """
    Create tables, backfill any columns a newer version introduced, then build
    indexes.

    The order matters: CREATE TABLE IF NOT EXISTS silently keeps an old table
    shape, so an index over a newly-added column would fail if it ran before
    the ALTER TABLE that adds it.
    """
    import re as _re
    stmts = [x.strip() for x in SCHEMA.split(";") if x.strip()]
    tables = [x for x in stmts if x.upper().startswith("CREATE TABLE")]
    indexes = [x for x in stmts if x.upper().startswith("CREATE INDEX")]

    with connect() as con:
        for stmt in tables:
            con.execute(stmt)

        for table, spec in _column_specs():
            have = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            for name, decl in spec:
                if name not in have:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    print(f"[db] migrated: {table}.{name} added")

        for stmt in indexes:
            con.execute(stmt)


def _column_specs():
    """(table, [(column, type-decl)]) parsed out of SCHEMA itself."""
    import re
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);",
                         SCHEMA, re.S):
        table, body = m.group(1), m.group(2)
        cols = []
        for line in body.splitlines():
            line = line.strip().rstrip(",")
            if not line or line.upper().startswith(("PRIMARY KEY", "UNIQUE", "FOREIGN KEY")):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2 and "PRIMARY KEY" not in parts[1].upper():
                cols.append((parts[0], parts[1]))
        yield table, cols


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "flags" in d:
        try:
            d["flags"] = json.loads(d["flags"] or "[]")
        except (TypeError, json.JSONDecodeError):
            d["flags"] = []
    return d


def prune(con: sqlite3.Connection, stale_days: int) -> int:
    """Drop listings not seen for `stale_days` and their history."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).isoformat()
    ids = [r[0] for r in con.execute(
        "SELECT id FROM listings WHERE last_seen < ?", (cutoff,))]
    if ids:
        qs = ",".join("?" * len(ids))
        con.execute(f"DELETE FROM price_history WHERE listing_id IN ({qs})", ids)
        con.execute(f"DELETE FROM listings WHERE id IN ({qs})", ids)
    return len(ids)
