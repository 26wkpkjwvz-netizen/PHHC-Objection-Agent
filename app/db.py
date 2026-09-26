"""SQLite storage: checklists, house notes, filings, reviews, findings and chat."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS checklist_items (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    code TEXT NOT NULL,
    cis_code TEXT NOT NULL DEFAULT '',
    grp TEXT NOT NULL,
    text TEXT NOT NULL,
    applies_when TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL,
    UNIQUE (category, code)
);

CREATE TABLE IF NOT EXISTS house_notes (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,          -- civil / criminal / writ / all
    code TEXT NOT NULL DEFAULT '',   -- objection code, or '' for a general note
    note TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filings (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,          -- civil / criminal / writ / auto
    case_type_hint TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY,
    filing_id INTEGER NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    status TEXT NOT NULL,            -- queued / reading / reasoning / done / error
    progress TEXT NOT NULL DEFAULT '',
    reader_model TEXT NOT NULL,
    reasoner_model TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    case_type TEXT NOT NULL DEFAULT '',
    preflight_json TEXT NOT NULL DEFAULT '{}',
    digest_json TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '',
    readiness INTEGER,
    strengths_json TEXT NOT NULL DEFAULT '[]',
    usage_json TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    grp TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    severity TEXT NOT NULL,          -- high / medium / low
    confidence TEXT NOT NULL,        -- likely / possible / check
    evidence TEXT NOT NULL,
    pages TEXT NOT NULL DEFAULT '',
    fix TEXT NOT NULL,
    draft_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',   -- open / fixed / dismissed
    user_note TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    role TEXT NOT NULL,              -- user / assistant
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_filing ON reviews(filing_id);
CREATE INDEX IF NOT EXISTS idx_findings_review ON findings(review_id);
CREATE INDEX IF NOT EXISTS idx_chat_review ON chat_messages(review_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
    seed_checklists()


def seed_checklists() -> None:
    """Load (or refresh) the Registry checklists from app/checklists/*.json."""
    with session() as conn:
        for category in config.CATEGORIES:
            data = json.loads((config.CHECKLIST_DIR / f"{category}.json").read_text())
            for order, item in enumerate(data["items"]):
                conn.execute(
                    """INSERT INTO checklist_items
                           (category, code, cis_code, grp, text, applies_when, sort_order)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (category, code) DO UPDATE SET
                           cis_code = excluded.cis_code, grp = excluded.grp,
                           text = excluded.text, applies_when = excluded.applies_when,
                           sort_order = excluded.sort_order""",
                    (category, item["code"], item.get("cis", ""), item["group"],
                     item["text"], item.get("when", ""), order),
                )


def rows(conn, sql, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(conn, sql, params=()) -> dict | None:
    r = conn.execute(sql, params).fetchone()
    return dict(r) if r else None


def checklist(conn, category: str) -> list[dict]:
    return rows(conn, "SELECT * FROM checklist_items WHERE category = ? ORDER BY sort_order",
                (category,))


def house_notes_for(conn, category: str) -> list[dict]:
    return rows(conn, "SELECT * FROM house_notes WHERE category IN (?, 'all') ORDER BY id",
                (category,))
