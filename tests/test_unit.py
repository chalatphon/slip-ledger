"""Pure-logic tests: no network, no key."""
from datetime import date
from decimal import Decimal

import pytest

from app import normalize
from app.agent import loop
from app.agent.tools import run_sql
from app.repositories import transactions as repo
from app.schemas import Transaction
from app.services.extractor import to_draft
from eval.eval_agent import is_correct
from eval.eval_extract import field_correct


@pytest.mark.parametrize("text,expected", [
    ("18 ต.ค. 69, 16:22", date(2026, 10, 18)),
    ("18 ตุลาคม 2569", date(2026, 10, 18)),
    ("5 ม.ค.68", date(2025, 1, 5)),
    ("18/10/2569", date(2026, 10, 18)),
    ("18/10/69", date(2026, 10, 18)),
    ("2026-10-18", date(2026, 10, 18)),
    ("18 Oct 2026", date(2026, 10, 18)),
    ("31 ก.พ. 69", None),   # impossible date -> None, not a crash
    ("no date here", None),
])
def test_parse_date(text, expected):
    assert normalize.parse_date(text) == expected


def test_parse_amount_and_time():
    assert normalize.parse_amount("1,234.50 THB") == Decimal("1234.50")
    assert normalize.parse_amount("59.00 บาท") == Decimal("59.00")
    assert normalize.parse_amount(59) == Decimal("59.00")
    assert normalize.parse_amount("-") is None
    assert normalize.parse_time("16:22 น.") == "16:22"
    assert normalize.parse_time("25:00") is None


SLIP = {"date": "18 ต.ค. 69", "time": "16:22", "amount": "59.00", "fee": "0.00",
        "sender_name": "นาย ชลัฐพล", "receiver_name": "บริษัท ไมเนอร์ ดีคิว จำกัด",
        "ref_no": "2026101816220924003073908", "category": "food"}


def test_to_draft_expense_when_owner_is_sender():
    res = to_draft(SLIP, ["ชลัฐพล"])
    assert res.errors == []
    assert res.draft.direction == "expense"
    assert res.draft.counterparty == "บริษัท ไมเนอร์ ดีคิว จำกัด"
    assert res.draft.date == date(2026, 10, 18) and res.draft.amount == Decimal("59.00")


def test_to_draft_income_and_unknown():
    assert to_draft(SLIP, ["ไมเนอร์"]).draft.direction == "income"
    assert to_draft(SLIP, []).draft.direction == "unknown"


def test_to_draft_maps_baseline_keys_and_bad_category():
    raw = {"transaction_date": "18/10/2569", "total": "120", "merchant": "ร้านกาแฟ", "category": "coffee"}
    res = to_draft(raw, [])
    assert res.draft.amount == Decimal("120.00")
    assert res.draft.direction == "expense"  # receipt: payee only -> spending
    assert res.draft.category == "other" and res.errors


def test_to_draft_invalid_is_reported_not_raised():
    res = to_draft({"date": "?", "amount": "0"}, [])
    assert res.draft is None
    assert any("date" in e for e in res.errors)


@pytest.fixture
def ledger(tmp_path):
    path = str(tmp_path / "ledger.db")
    db = repo.connect(path)
    repo.add(db, Transaction(date=date(2026, 10, 1), amount=100, counterparty="A",
                             direction="expense", category="food", ref_no="R1"))
    yield path, db
    db.close()


def test_duplicate_ref_rejected(ledger):
    _, db = ledger
    with pytest.raises(repo.DuplicateError):
        repo.add(db, Transaction(date=date(2026, 10, 2), amount=5, counterparty="B", ref_no="R1"))


@pytest.mark.parametrize("query", [
    "DELETE FROM transactions",
    "UPDATE transactions SET amount = 0",
    "SELECT 1; DROP TABLE transactions",
    "PRAGMA table_info(transactions)",
    "SELECT * FROM sqlite_master",
    "ATTACH DATABASE 'x.db' AS x",
])
def test_sql_guard_blocks_unsafe(ledger, query):
    path, _ = ledger
    assert run_sql(path, query).startswith("ERROR")
    assert run_sql(path, "SELECT COUNT(*) FROM transactions").endswith("1")  # table intact


def test_sql_tool_reads(ledger):
    path, _ = ledger
    assert run_sql(path, "SELECT SUM(amount) AS s FROM transactions;") == "s\n100.0"


def test_agent_action_parsing():
    assert loop._parse_action('ACTION: sql("SELECT 1")') == "SELECT 1"
    assert loop._parse_action("ACTION:\n```sql\nSELECT 2\n```") == "SELECT 2"
    assert loop._parse_action("ANSWER: 100 บาท") is None


def test_eval_scoring():
    assert is_correct("ใช้ไป 1,314 บาท", 1314.0)
    assert not is_correct("ใช้ไป 1,341 บาท", 1314.0)
    assert is_correct("The top category is food.", "food")
    assert field_correct("counterparty", "นาย ชลัฐพล", "ชลัฐพล")
    assert field_correct("amount", 59.0, 59)


def test_rule_baseline_builds_sql():
    from eval.rule_baseline import build_sql
    today = date(2026, 10, 20)
    sql = build_sql("เดือนนี้ใช้ค่ากินไปเท่าไหร่", ["Grab"], today)
    assert "category = 'food'" in sql and "'2026-10'" in sql and sql.startswith("SELECT SUM")
    assert "COUNT(*)" in build_sql("How many Grab rides?", ["Grab"], today)
    assert "counterparty = 'Grab'" in build_sql("How many Grab rides?", ["Grab"], today)
    assert build_sql("สวัสดี", [], today) is None  # no rule -> gives up


def test_rule_baseline_known_weakness_thai_substring():
    # Documented failure: "ยา" (medicine) is inside "กันยายน" (September).
    from eval.rule_baseline import build_sql
    sql = build_sql("เดือนกันยายนใช้ไปเท่าไหร่", [], date(2026, 10, 20))
    assert "category = 'health'" in sql


def test_majority_category():
    from eval.eval_extract import majority_category
    assert majority_category([{"category": "food"}, {"category": "food"}, {"category": "bills"}]) == ("food", 2 / 3)
    assert majority_category([{}]) == (None, 0.0)


def test_owner_found_by_account_when_name_is_misread():
    raw = {**SLIP, "sender_name": "นาย ชลวัช", "sender_account": "226-0-xxx569"}
    assert to_draft(raw, ["ชลัฐพล"], ["569"]).draft.direction == "expense"
    assert to_draft(raw, ["ชลัฐพล"], []).draft.direction == "unknown"  # name alone fails
    incoming = {**SLIP, "sender_name": "x", "receiver_name": "y", "receiver_account": "226-0-xxx569"}
    assert to_draft(incoming, [], ["569"]).draft.direction == "income"


def test_ref_no_swapped_for_value_matching_slip_timestamp():
    raw = {**SLIP, "ref_no": "000002206271835", "memo": "2026101816220924003073908"}
    res = to_draft(raw, [], [])
    assert res.draft.ref_no == "2026101816220924003073908"
    assert any("does not match" in e for e in res.errors)


def test_ref_no_left_alone_when_nothing_matches():
    raw = {**SLIP, "ref_no": "ABC123"}  # another bank's format: can't check, keep it
    res = to_draft(raw, [], [])
    assert res.draft.ref_no == "ABC123" and res.errors == []


def test_owner_on_payee_side_without_payer_is_unknown_not_invalid():
    # Seen on real slips: the model put the owner's account under the payee and read no payer.
    raw = {**SLIP, "sender_name": None, "receiver_name": "นาย ชลวัช", "receiver_account": "226-0-xxx569"}
    res = to_draft(raw, [], ["569"])
    assert res.draft is not None and res.draft.direction == "unknown"
    assert any("payee side" in e for e in res.errors)


def test_relative_dates_resolved_in_code():
    today = date(2026, 10, 26)
    assert normalize.parse_relative_date("เมื่อวาน", today) == date(2026, 10, 25)
    assert normalize.parse_relative_date("เมื่อวานซืน", today) == date(2026, 10, 24)
    assert normalize.parse_relative_date("Yesterday", today) == date(2026, 10, 25)
    assert normalize.parse_relative_date("18 ต.ค. 69", today) == date(2026, 10, 18)


def test_text_to_draft():
    from app.services.text_entry import text_to_draft
    today = date(2026, 10, 26)
    res = text_to_draft({"amount": "50", "counterparty": "ข้าวมันไก่", "date": "เมื่อวาน",
                         "direction": "expense", "category": "food"}, today)
    d = res.draft
    assert (d.date, d.amount, d.direction, d.category, d.source) == (
        date(2026, 10, 25), Decimal("50.00"), "expense", "food", "manual")
    # No date typed -> today; an odd direction -> expense; missing amount -> reported, not raised
    assert text_to_draft({"amount": "65", "counterparty": "กาแฟ", "direction": "?"}, today).draft.date == today
    assert text_to_draft({"counterparty": "กาแฟ"}, today).draft is None
