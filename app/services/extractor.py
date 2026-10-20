"""Slip / receipt image -> validated Transaction draft.

Two steps, kept apart on purpose so each can be evaluated:
  1. `read_image`  — the model transcribes the image into JSON (parse + one repair).
  2. `to_draft`    — code normalizes and validates that JSON into a `Transaction`.

Two prompt versions exist so the eval can compare them (eval/eval_extract.py):
  - "baseline": the naive default (the reference app's generic "extract key info").
  - "v2":       a fixed key set, Thai-slip hints, and a closed category list.
"""
from decimal import Decimal

from pydantic import ValidationError

from app import config, llm, normalize
from app.schemas import CATEGORIES, ExtractResult, Transaction

PROMPTS = {
    "baseline": (
        "Read this document image and extract its key information as a FLAT JSON object "
        "of string fields (for example date, amount, sender, receiver). Use snake_case "
        "keys. Return ONLY the JSON object, no prose, no code fences."
    ),
    "v2": (
        "This image is a Thai bank transfer slip or a shop receipt. Copy what is printed "
        "into this JSON object and return ONLY the JSON (no prose, no code fences):\n"
        "{\n"
        '  "date": "the date exactly as printed, e.g. 18 ต.ค. 69",\n'
        '  "time": "HH:MM or null",\n'
        '  "amount": "the transferred / total amount as printed, digits only, e.g. 1,250.00",\n'
        '  "fee": "fee (ค่าธรรมเนียม) or 0",\n'
        '  "sender_name": "name under จาก / From (payer), or null for a receipt",\n'
        '  "sender_bank": "payer bank or null",\n'
        '  "receiver_name": "name under ไปที่ / To (payee), or the shop name on a receipt",\n'
        '  "receiver_bank": "payee bank or null",\n'
        '  "ref_no": "the main reference number (เลขที่อ้างอิง / Ref) or null",\n'
        '  "memo": "note / บันทึกช่วยจำ or null",\n'
        f'  "category": "one of: {", ".join(CATEGORIES)}"\n'
        "}\n"
        "Rules: copy Thai text exactly as printed; do NOT convert the year; do not invent "
        "values — use null when a field is not on the image. Choose category from what the "
        "payee is (a restaurant or cafe is food, a person-to-person transfer is transfer)."
    ),
}

# The baseline prompt lets the model pick its own key names, so map the common ones.
# (This is part of what makes the baseline "naive": we have to guess its keys.)
ALIASES = {
    "date": ["date", "transaction_date", "datetime", "date_time", "วันที่"],
    "time": ["time", "transaction_time", "เวลา"],
    "amount": ["amount", "total", "total_amount", "amount_thb", "จำนวนเงิน", "จำนวน"],
    "fee": ["fee", "fees", "transaction_fee", "ค่าธรรมเนียม"],
    "sender_name": ["sender_name", "sender", "from", "from_name", "payer", "จาก"],
    "sender_bank": ["sender_bank", "from_bank"],
    "receiver_name": ["receiver_name", "receiver", "to", "to_name", "payee", "recipient",
                      "merchant", "merchant_name", "shop_name", "ไปที่"],
    "receiver_bank": ["receiver_bank", "to_bank"],
    "ref_no": ["ref_no", "reference_number", "reference_no", "ref", "transaction_id",
               "เลขที่อ้างอิง"],
    "memo": ["memo", "note", "notes"],
    "category": ["category"],
}


def _pick(raw: dict, field: str):
    for key in ALIASES[field]:
        if raw.get(key) not in (None, "", "null"):
            return raw[key]
    return None


def _is_owner(name: str | None, owners: list[str]) -> bool:
    return bool(name) and any(o.lower() in name.lower() for o in owners)


def to_draft(raw: dict, owners: list[str] | None = None) -> ExtractResult:
    """Normalize the model's JSON and validate it as a Transaction. Never raises:
    problems come back as `errors` so the UI can ask the user to fix them."""
    owners = config.OWNER_NAMES if owners is None else owners
    errors: list[str] = []
    if "_error" in raw:
        return ExtractResult(draft=None, raw=raw, errors=[raw["_error"]])

    sender = normalize.clean_text(_pick(raw, "sender_name"))
    receiver = normalize.clean_text(_pick(raw, "receiver_name"))
    # Direction is decided by code, not the model: is the owner the payer or the payee?
    if _is_owner(sender, owners):
        direction, counterparty, bank = "expense", receiver, _pick(raw, "receiver_bank")
    elif _is_owner(receiver, owners):
        direction, counterparty, bank = "income", sender, _pick(raw, "sender_bank")
    elif sender is None and receiver:  # a shop receipt: no payer printed -> spending
        direction, counterparty, bank = "expense", receiver, None
    else:
        direction, counterparty, bank = "unknown", receiver or sender, _pick(raw, "receiver_bank")

    category = str(_pick(raw, "category") or "other").strip().lower()
    if category not in CATEGORIES:
        errors.append(f"unknown category {category!r} -> other")
        category = "other"

    fields = {
        "date": normalize.parse_date(_pick(raw, "date")),
        "time": normalize.parse_time(_pick(raw, "time")),
        "amount": normalize.parse_amount(_pick(raw, "amount")),
        "fee": normalize.parse_amount(_pick(raw, "fee")) or Decimal("0.00"),
        "direction": direction,
        "counterparty": counterparty,
        "bank": normalize.clean_text(bank),
        "ref_no": normalize.clean_text(_pick(raw, "ref_no")),
        "category": category,
        "memo": normalize.clean_text(_pick(raw, "memo")),
    }
    try:
        return ExtractResult(draft=Transaction(**fields), raw=raw, errors=errors)
    except ValidationError as exc:
        errors += [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return ExtractResult(draft=None, raw=raw, errors=errors)


async def read_image(image_bytes: bytes, mime: str, prompt: str = "v2") -> dict:
    """Ask the model to transcribe the image as JSON. The proxy ignores
    `response_format`, so we parse the reply ourselves and, if it isn't JSON, show the
    model its own output once and ask again (parse-then-repair)."""
    content = [{"type": "text", "text": PROMPTS[prompt]}, llm.image_part(image_bytes, mime)]
    first = await llm.complete([{"role": "user", "content": content}])
    if (obj := llm.parse_json_object(first)) is not None:
        return obj
    repaired = await llm.complete([
        {"role": "user", "content": content},
        {"role": "assistant", "content": first},
        {"role": "user", "content": "That was not valid JSON. Reply with ONLY the JSON object."},
    ])
    return llm.parse_json_object(repaired) or {"_error": "model did not return JSON", "_raw": first[:500]}


async def extract(image_bytes: bytes, mime: str, prompt: str = "v2") -> ExtractResult:
    return to_draft(await read_image(image_bytes, mime, prompt))
