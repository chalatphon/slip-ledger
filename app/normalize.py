"""Deterministic clean-up of what the model read off a slip.

Design choice: the model only *transcribes* (copies the text it sees); code does the
parts that have one right answer — Thai month names, Buddhist-era years, "1,234.00 THB".
A 4B model is unreliable at "convert 69 B.E. to 2026", but a lookup table never is.
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

THAI_MONTHS = {
    "ม.ค.": 1, "มกราคม": 1, "ก.พ.": 2, "กุมภาพันธ์": 2, "มี.ค.": 3, "มีนาคม": 3,
    "เม.ย.": 4, "เมษายน": 4, "พ.ค.": 5, "พฤษภาคม": 5, "มิ.ย.": 6, "มิถุนายน": 6,
    "ก.ค.": 7, "กรกฎาคม": 7, "ส.ค.": 8, "สิงหาคม": 8, "ก.ย.": 9, "กันยายน": 9,
    "ต.ค.": 10, "ตุลาคม": 10, "พ.ย.": 11, "พฤศจิกายน": 11, "ธ.ค.": 12, "ธันวาคม": 12,
}
EN_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _year(y: str) -> int:
    """Thai slips print Buddhist-era years, often as 2 digits ("69" = 2569 = 2026)."""
    n = int(y)
    if n < 100:
        n += 2500  # 2-digit years on Thai slips are B.E.
    if n > 2400:
        n -= 543   # B.E. -> C.E.
    return n


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    s = str(text).strip()
    # ISO already: 2026-10-18
    if m := re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        y, mo, d = _year(m[1]), int(m[2]), int(m[3])
        return _safe_date(y, mo, d)
    # 18/10/2569, 18/10/69, 18-10-26
    if m := re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", s):
        return _safe_date(_year(m[3]), int(m[2]), int(m[1]))
    # 18 ต.ค. 69 / 18 ตุลาคม 2569 / 18 Oct 2026
    if m := re.search(r"(\d{1,2})\s*([^\d\s,]+)\s*(\d{2,4})", s):
        word = m[2].strip()
        month = THAI_MONTHS.get(word) or THAI_MONTHS.get(word.rstrip(".") + ".")
        if month is None:
            month = EN_MONTHS.get(word[:3].lower())
        if month:
            return _safe_date(_year(m[3]), month, int(m[1]))
    return None


def _safe_date(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_time(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"(\d{1,2})[:.](\d{2})", str(text))
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return f"{int(m[1]):02d}:{m[2]}"


def parse_amount(value) -> Decimal | None:
    """'1,234.50 THB' / '59.00 บาท' / 59 -> Decimal('1234.50')."""
    if value is None:
        return None
    s = re.sub(r"[^\d.\-]", "", str(value).replace(",", ""))
    if not s or s in {".", "-"}:
        return None
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def clean_text(value) -> str | None:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None
