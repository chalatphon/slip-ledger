"""Baseline F: answer ledger questions WITHOUT an LLM — keyword rules + SQL templates.

This is what you'd build if you didn't want a model in the loop: spot a month, a
category keyword or a known payee, pick an intent (sum / count / max / avg / net /
"which category" / "which month") and fill in a template. It is precise when a
question matches a rule and silently wrong (or gives up) when it doesn't — exactly
the trade-off the agent is meant to beat.

Honesty note: the rules were written once from general knowledge of how people ask
about spending, then run as-is — not tuned against eval/data/agent_questions.jsonl.
"""
import sqlite3
from datetime import date

from app.agent.tools import run_sql

MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5, "มิถุนายน": 6,
    "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10, "พฤศจิกายน": 11, "ธันวาคม": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
# Longer keywords first so e.g. "หนังสือ" (book) wins over "หนัง" (movie).
CATEGORY_WORDS = sorted([
    ("หนังสือ", "education"), ("เรียน", "education"), ("education", "education"),
    ("กิน", "food"), ("อาหาร", "food"), ("ข้าว", "food"), ("food", "food"), ("eat", "food"), ("meal", "food"),
    ("เดินทาง", "transport"), ("รถ", "transport"), ("transport", "transport"), ("taxi", "transport"),
    ("ซื้อของ", "shopping"), ("shopping", "shopping"),
    ("ค่าไฟ", "bills"), ("ค่าน้ำ", "bills"), ("ค่าโทร", "bills"), ("บิล", "bills"), ("bill", "bills"),
    ("ยา", "health"), ("หมอ", "health"), ("health", "health"), ("medicine", "health"),
    ("หนัง", "entertainment"), ("บันเทิง", "entertainment"), ("movie", "entertainment"),
], key=lambda kv: -len(kv[0]))
INCOME_WORDS = ("รายรับ", "ได้เงิน", "income", "received", "earn")


def _has(q: str, words) -> bool:
    return any(w in q for w in words)


def _month(q: str, today: date) -> tuple[str | None, bool]:
    """-> ('YYYY-MM' or None, is_since). 'ตั้งแต่ / since <month>' means from that month on."""
    if _has(q, ("เดือนนี้", "this month")):
        return today.strftime("%Y-%m"), False
    if _has(q, ("เดือนที่แล้ว", "เดือนก่อน", "last month")):
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return f"{y}-{m:02d}", False
    for word, m in MONTHS.items():
        if word in q:
            return f"{today.year}-{m:02d}", _has(q, ("ตั้งแต่", "since"))
    return None, False


def _payee(q: str, payees: list[str]) -> str | None:
    hits = [p for p in payees if p.lower() in q]
    return max(hits, key=len) if hits else None


def build_sql(question: str, payees: list[str], today: date) -> str | None:
    q = question.lower()
    where = []
    month, since = _month(q, today)
    if month:
        where.append(f"date >= '{month}-01'" if since else f"strftime('%Y-%m', date) = '{month}'")
    payee = _payee(q, payees)
    income = _has(q, INCOME_WORDS)
    if payee:
        where.append("counterparty = '" + payee.replace("'", "''") + "'")
    else:
        category = next((c for w, c in CATEGORY_WORDS if w in q), None)
        if category:
            where.append(f"category = '{category}'")

    def sql(select: str, extra: list[str], tail: str = "") -> str:
        cond = " AND ".join(where + extra)
        return f"SELECT {select} FROM transactions" + (f" WHERE {cond}" if cond else "") + tail

    direction = ["direction = 'income'"] if income else ([] if payee else ["direction = 'expense'"])
    if _has(q, ("กี่ครั้ง", "กี่รายการ", "how many")):
        return sql("COUNT(*)", [])
    if _has(q, ("สุทธิ", "net", "เหลือเงิน")):
        return sql("SUM(CASE WHEN direction = 'income' THEN amount ELSE -amount END)", [])
    if _has(q, ("หมวดไหน", "which category")):
        return sql("category", ["direction = 'expense'"], " GROUP BY category ORDER BY SUM(amount) DESC LIMIT 1")
    if _has(q, ("เดือนไหน", "which month")):
        return sql("strftime('%Y-%m', date) AS m", direction, " GROUP BY m ORDER BY SUM(amount) DESC LIMIT 1")
    if _has(q, ("เฉลี่ย", "average")):
        return sql("ROUND(AVG(amount), 2)", direction)
    if _has(q, ("มากที่สุด", "biggest", "largest", "highest")):
        return sql("MAX(amount)", direction)
    if _has(q, ("เท่าไหร่", "เท่าไร", "how much", "total", "รวม")):
        return sql("SUM(amount)", direction)
    return None  # no rule matched


def answer(question: str, db_path: str, today: date) -> tuple[str, str | None]:
    """-> (answer text, the SQL used or None)."""
    db = sqlite3.connect(db_path)
    try:
        payees = [r[0] for r in db.execute("SELECT DISTINCT counterparty FROM transactions")]
    finally:
        db.close()
    query = build_sql(question, payees, today)
    if query is None:
        return "ขอโทษ ไม่เข้าใจคำถาม (no rule matched)", None
    result = run_sql(db_path, query).splitlines()
    return (result[1] if len(result) > 1 else result[0]), query
