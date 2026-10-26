"""Typed entry: a line like "ข้าวมันไก่ 50 บาท เมื่อวาน" -> a validated Transaction draft.

Same split as the slip extractor: the model only pulls the parts out of the sentence
(what, how much, when-as-written); code resolves relative dates against today, picks
the default direction and validates. The draft goes through the same confirm step
as a slip, so nothing is saved until the user presses save.
"""
from datetime import date
from decimal import Decimal

from pydantic import ValidationError

from app import llm, normalize
from app.schemas import CATEGORIES, ExtractResult, Transaction

MAX_TEXT = 500

PROMPT = (
    "The user typed one money transaction in Thai or English. Pull its parts into this "
    "JSON object and return ONLY the JSON (no prose, no code fences):\n"
    "{\n"
    '  "amount": "the amount in baht, digits only",\n'
    '  "counterparty": "who was paid or who paid, or what was bought (short)",\n'
    '  "date": "the date words exactly as typed (e.g. เมื่อวาน, 18 ต.ค., yesterday) or null",\n'
    '  "time": "HH:MM if typed, else null",\n'
    '  "direction": "income only if the user RECEIVED money (ได้เงิน, รับ, เงินเดือน), else expense",\n'
    f'  "category": "one of: {", ".join(CATEGORIES)}",\n'
    '  "memo": "anything else worth keeping, or null"\n'
    "}\n"
    "Do not convert dates and do not invent values."
)


def text_to_draft(raw: dict, today: date) -> ExtractResult:
    """Normalize the model's JSON for a typed entry. Never raises: problems come back
    as `errors` so the user can fix them in the form."""
    if "_error" in raw:
        return ExtractResult(draft=None, raw=raw, errors=[raw["_error"]])
    errors: list[str] = []

    when = normalize.parse_relative_date(raw.get("date"), today)
    if when is None:
        when = today
        if raw.get("date"):
            errors.append(f"could not read date {raw.get('date')!r} -> today")

    direction = str(raw.get("direction") or "expense").strip().lower()
    if direction not in ("expense", "income"):
        direction = "expense"

    category = str(raw.get("category") or "other").strip().lower()
    if category not in CATEGORIES:
        errors.append(f"unknown category {category!r} -> other")
        category = "other"

    fields = {
        "date": when,
        "time": normalize.parse_time(raw.get("time")),
        "amount": normalize.parse_amount(raw.get("amount")),
        "fee": Decimal("0.00"),
        "direction": direction,
        "counterparty": normalize.clean_text(raw.get("counterparty")),
        "category": category,
        "memo": normalize.clean_text(raw.get("memo")),
        "source": "manual",
    }
    try:
        return ExtractResult(draft=Transaction(**fields), raw=raw, errors=errors)
    except ValidationError as exc:
        errors += [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return ExtractResult(draft=None, raw=raw, errors=errors)


async def parse(text: str, today: date | None = None) -> ExtractResult:
    today = today or date.today()
    raw = await llm.complete_json([
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": text},
    ])
    return text_to_draft(raw, today)
