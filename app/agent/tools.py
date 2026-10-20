"""The agent's one tool: run a read-only SQL query over the ledger.

The model writes the SQL, so the SQL is UNTRUSTED. Three independent guards:
  1. a cheap text check (one statement, must start with SELECT/WITH);
  2. the database is opened read-only (`mode=ro`), so writes fail at the file level;
  3. an sqlite authorizer allow-list: only SELECT, reading the `transactions` table,
     and calling functions — anything else (PRAGMA, ATTACH, other tables) is denied.
Plus a step limit so a runaway query can't hang the server, and a row cap so a
huge result can't flood the model's context.
"""
import re
import sqlite3
from pathlib import Path

ALLOWED_TABLES = {"transactions"}
MAX_ROWS = 30
MAX_VM_STEPS = 2_000_000


class UnsafeQuery(Exception):
    pass


def _authorizer(action, arg1, arg2, db_name, trigger):
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_OK if arg1 in ALLOWED_TABLES else sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def check_sql(query: str) -> str:
    q = query.strip().rstrip(";").strip()
    if not q:
        raise UnsafeQuery("empty query")
    if ";" in q:
        raise UnsafeQuery("only one statement is allowed")
    if not re.match(r"(?is)^(select|with)\b", q):
        raise UnsafeQuery("only SELECT queries are allowed")
    return q


def run_sql(db_path: str, query: str) -> str:
    """Run the query and return the rows as compact text the model can read.
    Errors are returned as text too (the model sees them and can retry)."""
    try:
        q = check_sql(query)
    except UnsafeQuery as exc:
        return f"ERROR: {exc}"
    if not Path(db_path).exists():
        return "ERROR: the ledger is empty"
    db = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        db.set_authorizer(_authorizer)
        steps = 0

        def _budget():
            nonlocal steps
            steps += 1
            return 1 if steps * 1000 > MAX_VM_STEPS else 0  # non-zero aborts the query

        db.set_progress_handler(_budget, 1000)
        cur = db.execute(q)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(MAX_ROWS + 1)
    except sqlite3.Error as exc:
        return f"ERROR: {exc}"
    finally:
        db.close()
    if not rows:
        return "(no rows)"
    lines = [" | ".join(cols)] + [" | ".join("NULL" if v is None else str(v) for v in r) for r in rows[:MAX_ROWS]]
    if len(rows) > MAX_ROWS:
        lines.append(f"... (more than {MAX_ROWS} rows; aggregate with SUM/COUNT/GROUP BY instead)")
    return "\n".join(lines)
