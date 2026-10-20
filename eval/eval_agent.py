"""Evaluate question answering over the ledger: SQL agent vs a no-tool baseline.

Test set: eval/data/agent_questions.jsonl — each question has a `gold_sql`; the
expected answer is computed by running that SQL on the fixed seed ledger, so the
labels can't drift from the data.

Methods
  baseline  "stuff the table into the prompt": the whole ledger as CSV + the question,
            one call, no tools. The model must filter and add up numbers itself.
  agent     app/agent/loop.py — the model writes SQL, the database does the maths.

Metric: answer accuracy — correct if the expected value appears in the answer
(numbers compared to 0.01 after removing thousands separators).

Run:  uv run python -m eval.eval_agent              # both methods
      uv run python -m eval.eval_agent --methods agent --limit 5
"""
import argparse
import asyncio
import json
import re
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from app import llm
from app.agent import loop
from eval import seed

HERE = Path(__file__).parent
QUESTIONS = HERE / "data" / "agent_questions.jsonl"
RESULTS = HERE / "results"
TODAY = date(2026, 10, 20)  # fixed so "this month" / "last month" are reproducible


def expected_answer(db_path: str, gold_sql: str):
    db = sqlite3.connect(db_path)
    try:
        return db.execute(gold_sql).fetchone()[0]
    finally:
        db.close()


def is_correct(answer: str, expected) -> bool:
    if isinstance(expected, (int, float)):
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", answer.replace(",", ""))]
        return any(abs(n - float(expected)) < 0.01 for n in nums)
    return str(expected).lower() in answer.lower()


async def baseline(question: str, csv_text: str) -> str:
    messages = [
        {"role": "system", "content": (
            f"You are a personal-finance assistant. Today is {TODAY.isoformat()}. Here is the "
            f"user's complete ledger as CSV:\n\n{csv_text}\n\nAnswer the question using only "
            "this data. Give the final number (or name) clearly.")},
        {"role": "user", "content": question},
    ]
    return await llm.complete(messages, max_tokens=400)


async def main(methods: list[str], limit: int | None) -> None:
    questions = [json.loads(l) for l in QUESTIONS.read_text(encoding="utf-8").splitlines() if l.strip()]
    questions = questions[:limit] if limit else questions
    csv_text = seed.CSV_PATH.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "eval.db")
        seed.load(db_path)
        RESULTS.mkdir(exist_ok=True)
        summary = {}
        for method in methods:
            rows, correct, steps = [], 0, 0
            for q in questions:
                exp = expected_answer(db_path, q["gold_sql"])
                if method == "agent":
                    res = await loop.run(q["question"], db_path=db_path, today=TODAY)
                    answer, n_steps = res.answer, len(res.steps)
                    trace = [s.model_dump() for s in res.steps]
                else:
                    answer, n_steps, trace = await baseline(q["question"], csv_text), 0, []
                ok = is_correct(answer, exp)
                correct += ok
                steps += n_steps
                rows.append({"id": q["id"], "question": q["question"], "expected": exp,
                             "answer": answer, "correct": ok, "steps": trace})
                print(f"[{method}] {q['id']} {'OK ' if ok else 'BAD'} expected={exp!r} answer={answer[:80]!r}")
            acc = correct / len(questions)
            summary[method] = {"accuracy": round(acc, 3), "correct": correct, "n": len(questions),
                               "avg_tool_calls": round(steps / len(questions), 2)}
            (RESULTS / f"agent_{method}.json").write_text(
                json.dumps({"summary": summary[method], "rows": rows}, ensure_ascii=False, indent=2),
                encoding="utf-8")

    print("\n| method | accuracy | correct / n | avg tool calls |\n|---|---|---|---|")
    for m, s in summary.items():
        print(f"| {m} | {s['accuracy']:.0%} | {s['correct']} / {s['n']} | {s['avg_tool_calls']} |")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--methods", nargs="+", default=["baseline", "agent"], choices=["baseline", "agent"])
    p.add_argument("--limit", type=int)
    a = p.parse_args()
    asyncio.run(main(a.methods, a.limit))
