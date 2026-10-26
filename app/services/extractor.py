"""Slip / receipt image -> validated Transaction draft.

Two steps, kept apart on purpose so each can be evaluated:
  1. `read_image`  — the model transcribes the image into JSON (parse + one repair).
  2. `to_draft`    — code normalizes and validates that JSON into a `Transaction`.

Three prompt versions exist so the eval can compare them (eval/eval_extract.py):
  - "baseline":   the naive default (the reference app's generic "extract key info").
  - "fixed_keys": the same generic instruction, but with v2's key set and category
                  list — no Thai-slip hints. baseline -> fixed_keys measures what fixed
                  keys buy; fixed_keys -> v2 measures what the hints buy (an ablation).
  - "v2":         fixed keys + Thai-slip hints + a closed category list.
  - "v3":         v2 + the payer/payee ACCOUNT numbers, and a ref_no hint that names the
                  footer number and rules out the shop/biller codes. Code then decides
                  direction from OWNER_ACCOUNTS and cross-checks ref_no against the slip's
                  date and time (see to_draft).
"""
import re
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
    "fixed_keys": (
        "Read this document image and extract its key information as a FLAT JSON object "
        "with exactly these keys: date, time, amount, fee, sender_name, sender_bank, "
        "receiver_name, receiver_bank, ref_no, memo, category. category is one of: "
        f"{', '.join(CATEGORIES)}. Return ONLY the JSON object, no prose, no code fences."
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
    "v3": (
        "This image is a Thai bank transfer slip or a shop receipt. Copy what is printed "
        "into this JSON object and return ONLY the JSON (no prose, no code fences):\n"
        "{\n"
        '  "date": "the date exactly as printed, e.g. 18 ต.ค. 69",\n'
        '  "time": "HH:MM or null",\n'
        '  "amount": "the transferred / total amount as printed, digits only, e.g. 1,250.00",\n'
        '  "fee": "fee (ค่าธรรมเนียม) or 0",\n'
        '  "sender_name": "name under จาก / From (payer), or null for a receipt",\n'
        '  "sender_account": "account number under the payer name, as printed, e.g. 123-4-xxx678",\n'
        '  "sender_bank": "payer bank or null",\n'
        '  "receiver_name": "name under ไปที่ / To (payee), or the shop name on a receipt",\n'
        '  "receiver_account": "account, phone or e-wallet number under the payee name, or null",\n'
        '  "receiver_bank": "payee bank or null",\n'
        '  "ref_no": "the LONG number labelled เลขที่อ้างอิง in the footer at the very bottom, '
        'next to the QR code; it usually starts with the date and time, e.g. 2026010112304512345678901",\n'
        '  "memo": "note / บันทึกช่วยจำ or null",\n'
        f'  "category": "one of: {", ".join(CATEGORIES)}"\n'
        "}\n"
        "Rules: copy Thai text exactly as printed; do NOT convert the year; do not invent "
        "values — use null when a field is not on the image. ref_no is NEVER the shop code "
        "(รหัสร้านค้า, หมายเลขร้านค้า), the Biller ID, the account number or the short "
        "หมายเลขอ้างอิง. Choose category from what the payee is (a restaurant or cafe is "
        "food, a person-to-person transfer is transfer)."
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
    "sender_account": ["sender_account", "from_account", "sender_account_number"],
    "sender_bank": ["sender_bank", "from_bank"],
    "receiver_name": ["receiver_name", "receiver", "to", "to_name", "payee", "recipient",
                      "merchant", "merchant_name", "shop_name", "ไปที่"],
    "receiver_account": ["receiver_account", "to_account", "receiver_account_number"],
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


def _is_owner(name: str | None, account: str | None, owners: list[str], accounts: list[str]) -> bool:
    """The owner by account number first (the model reads digits far better than Thai
    names: on our 20 slips it misread the owner's name on every one), then by name."""
    if account and accounts:
        digits = re.sub(r"[^0-9xX]", "", str(account))
        if any(digits.endswith(a) for a in accounts):
            return True
    return bool(name) and any(o.lower() in name.lower() for o in owners)


def _check_ref(ref: str | None, raw: dict, date, time: str | None) -> tuple[str | None, str | None]:
    """Thai bank refs start with the slip's own date and time (YYYYMMDDHHMM...). If the
    model's ref_no doesn't but another value it read does, that value is the ref: the
    model swapped two fields. Slips whose refs follow another pattern are left alone."""
    if not (date and time):
        return ref, None
    stamp = date.strftime("%Y%m%d") + time.replace(":", "")
    if ref and re.sub(r"\s", "", ref).startswith(stamp):
        return ref, None
    for value in raw.values():
        candidate = re.sub(r"\s", "", str(value)) if isinstance(value, (str, int)) else ""
        if candidate.startswith(stamp) and len(candidate) >= 20:
            return candidate, f"ref_no {ref!r} does not match the slip's date/time -> {candidate}"
    return ref, None


def to_draft(raw: dict, owners: list[str] | None = None, accounts: list[str] | None = None,
             check_ref: bool = True) -> ExtractResult:
    """Normalize the model's JSON and validate it as a Transaction. Never raises:
    problems come back as `errors` so the UI can ask the user to fix them."""
    owners = config.OWNER_NAMES if owners is None else owners
    accounts = config.OWNER_ACCOUNTS if accounts is None else accounts
    errors: list[str] = []
    if "_error" in raw:
        return ExtractResult(draft=None, raw=raw, errors=[raw["_error"]])

    sender = normalize.clean_text(_pick(raw, "sender_name"))
    receiver = normalize.clean_text(_pick(raw, "receiver_name"))
    # Direction is decided by code, not the model: is the owner the payer or the payee?
    sender_acct, receiver_acct = _pick(raw, "sender_account"), _pick(raw, "receiver_account")
    owner_receives = _is_owner(receiver, receiver_acct, owners, accounts)
    if _is_owner(sender, sender_acct, owners, accounts):
        direction, counterparty, bank = "expense", receiver, _pick(raw, "receiver_bank")
    elif owner_receives and (sender or sender_acct):
        direction, counterparty, bank = "income", sender, _pick(raw, "sender_bank")
    elif sender is None and receiver and not owner_receives:  # a shop receipt -> spending
        direction, counterparty, bank = "expense", receiver, None
    else:
        # Includes: the owner read on the payee side with no payer at all — the model
        # has likely put the payer block in the wrong field. Don't guess; let the user say.
        direction, counterparty, bank = "unknown", receiver or sender, _pick(raw, "receiver_bank")
        if owner_receives:
            errors.append("owner found on the payee side but no payer was read - check direction")

    category = str(_pick(raw, "category") or "other").strip().lower()
    if category not in CATEGORIES:
        errors.append(f"unknown category {category!r} -> other")
        category = "other"

    date, time = normalize.parse_date(_pick(raw, "date")), normalize.parse_time(_pick(raw, "time"))
    ref_no, note = normalize.clean_text(_pick(raw, "ref_no")), None
    if check_ref:
        ref_no, note = _check_ref(ref_no, raw, date, time)
    if note:
        errors.append(note)

    fields = {
        "date": date,
        "time": time,
        "amount": normalize.parse_amount(_pick(raw, "amount")),
        "fee": normalize.parse_amount(_pick(raw, "fee")) or Decimal("0.00"),
        "direction": direction,
        "counterparty": counterparty,
        "bank": normalize.clean_text(bank),
        "ref_no": ref_no,
        "category": category,
        "memo": normalize.clean_text(_pick(raw, "memo")),
    }
    try:
        return ExtractResult(draft=Transaction(**fields), raw=raw, errors=errors)
    except ValidationError as exc:
        errors += [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
        return ExtractResult(draft=None, raw=raw, errors=errors)


async def read_image(image_bytes: bytes, mime: str, prompt: str = "v3") -> dict:
    """Ask the model to transcribe the image as JSON. The proxy ignores
    `response_format`, so we parse the reply ourselves and, if it isn't JSON, show the
    model its own output once and ask again (parse-then-repair)."""
    content = [{"type": "text", "text": PROMPTS[prompt]}, llm.image_part(image_bytes, mime)]
    return await llm.complete_json([{"role": "user", "content": content}])


async def extract(image_bytes: bytes, mime: str, prompt: str = "v3") -> ExtractResult:
    return to_draft(await read_image(image_bytes, mime, prompt))
