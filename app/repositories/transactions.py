"""Repository: the only module that writes SQL against the ledger. Pure data access —
no HTTP, no model calls."""
import sqlite3
from pathlib import Path

from app.schemas import StoredTransaction, Transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id           INTEGER PRIMARY KEY,
    date         TEXT NOT NULL,             -- YYYY-MM-DD
    time         TEXT,                      -- HH:MM
    amount       REAL NOT NULL,             -- THB, always positive
    fee          REAL NOT NULL DEFAULT 0,
    direction    TEXT NOT NULL,             -- expense | income | unknown
    counterparty TEXT NOT NULL,
    bank         TEXT,
    ref_no       TEXT UNIQUE,               -- the same slip can't be saved twice
    category     TEXT NOT NULL,
    memo         TEXT,
    source       TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""
COLUMNS = ("date", "time", "amount", "fee", "direction", "counterparty",
           "bank", "ref_no", "category", "memo", "source")


class DuplicateError(Exception):
    """A transaction with this ref_no is already in the ledger."""


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # FastAPI may open the connection (in the get_db dependency) on one worker thread
    # and run the endpoint on another. Each request gets its own connection and uses
    # it from one request at a time, so turning off sqlite's same-thread check is safe.
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute(SCHEMA)
    return db


def add(db: sqlite3.Connection, tx: Transaction) -> int:
    row = tx.model_dump(mode="json")
    row["amount"], row["fee"] = float(tx.amount), float(tx.fee)
    try:
        cur = db.execute(
            f"INSERT INTO transactions ({', '.join(COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in COLUMNS)})",
            [row[c] for c in COLUMNS],
        )
    except sqlite3.IntegrityError as exc:
        raise DuplicateError(tx.ref_no) from exc
    db.commit()
    return cur.lastrowid


def list_recent(db: sqlite3.Connection, limit: int = 100) -> list[StoredTransaction]:
    rows = db.execute(
        f"SELECT id, {', '.join(COLUMNS)} FROM transactions "
        "ORDER BY date DESC, time DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [StoredTransaction(**dict(r)) for r in rows]


def delete(db: sqlite3.Connection, tx_id: int) -> bool:
    cur = db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    db.commit()
    return cur.rowcount > 0
