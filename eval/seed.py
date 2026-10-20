"""A synthetic 3-month ledger (Aug-Oct 2026) — the fixed database the agent eval runs
against, and handy demo data.

It is generated with a fixed random seed, so the CSV in the repo is reproducible:
  uv run python -m eval.seed --write              # (re)generate eval/data/seed_ledger.csv
  uv run python -m eval.seed --load data/ledger.db  # load it into a ledger for the demo
"""
import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

from app.repositories import transactions as repo
from app.schemas import Transaction

CSV_PATH = Path(__file__).parent / "data" / "seed_ledger.csv"

# (counterparty, category, min THB, max THB, times per month)
EXPENSES = [
    ("ร้านข้าวมันไก่ป้าศรี", "food", 45, 70, 8),
    ("โรงอาหาร สจล.", "food", 35, 60, 10),
    ("Cafe Amazon", "food", 55, 85, 5),
    ("BTS Skytrain", "transport", 30, 60, 6),
    ("Grab", "transport", 80, 220, 2),
    ("Shopee", "shopping", 150, 900, 2),
    ("การไฟฟ้านครหลวง", "bills", 450, 900, 1),
    ("AIS", "bills", 399, 399, 1),
    ("ร้านยาเภสัช", "health", 90, 350, 1),
    ("Major Cineplex", "entertainment", 180, 260, 1),
]
INCOME = [("แม่", "transfer", 6000, 6000, 1), ("งาน TA ภาควิชา", "salary", 3000, 3000, 1)]


def generate(seed: int = 42) -> list[Transaction]:
    rnd = random.Random(seed)
    out: list[Transaction] = []
    ref = 100000
    for month in (8, 9, 10):
        last_day = 20 if month == 10 else (date(2026, month + 1, 1) - timedelta(days=1)).day
        for rows, direction in ((EXPENSES, "expense"), (INCOME, "income")):
            for name, cat, lo, hi, n in rows:
                for _ in range(n):
                    ref += 1
                    out.append(Transaction(
                        date=date(2026, month, rnd.randint(1, last_day)),
                        time=f"{rnd.randint(7, 22):02d}:{rnd.randint(0, 59):02d}",
                        amount=rnd.randint(lo, hi), direction=direction, counterparty=name,
                        bank="ธนาคารกรุงเทพ", ref_no=f"SEED{ref}", category=cat, source="manual",
                    ))
    return sorted(out, key=lambda t: (t.date, t.time))


def write_csv(rows: list[Transaction], path: Path = CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=repo.COLUMNS)
        w.writeheader()
        for t in rows:
            w.writerow(t.model_dump(mode="json"))


def read_csv(path: Path = CSV_PATH) -> list[Transaction]:
    with path.open(encoding="utf-8") as f:
        return [Transaction(**{k: (v or None) for k, v in r.items()}) for r in csv.DictReader(f)]


def load(db_path: str, path: Path = CSV_PATH) -> int:
    db = repo.connect(db_path)
    n = 0
    for t in read_csv(path):
        try:
            repo.add(db, t)
            n += 1
        except repo.DuplicateError:
            pass
    db.close()
    return n


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true", help="regenerate the CSV")
    p.add_argument("--load", metavar="DB", help="load the CSV into this ledger DB")
    a = p.parse_args()
    if a.write:
        rows = generate()
        write_csv(rows)
        print(f"wrote {len(rows)} rows to {CSV_PATH}")
    if a.load:
        print(f"loaded {load(a.load)} rows into {a.load}")
