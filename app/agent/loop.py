"""Agent loop: the model answers questions about the ledger by writing SQL, reading
the result, and deciding what to do next — call -> run tool -> feed back, with a stop
condition (MAX_STEPS) that still forces a final answer.

Same plain text protocol as the course reference (works with any model, and the loop
stays visible): each turn the model emits ONE line, either ACTION or ANSWER.

Run:  uv run python -m app.agent.loop "เดือนนี้ใช้เงินค่ากินไปเท่าไหร่"
"""
import asyncio
import re
import sys
from datetime import date

from app import config, llm
from app.agent.tools import run_sql
from app.schemas import CATEGORIES, AgentStep, AskResponse

MAX_STEPS = 5

SYSTEM = """You are a personal-finance assistant. You answer questions about the user's
ledger by querying it with SQL. Today is {today}.

TABLE transactions(
  date TEXT 'YYYY-MM-DD', time TEXT 'HH:MM', amount REAL (THB, always positive),
  fee REAL, direction TEXT ('expense' | 'income' | 'unknown'), counterparty TEXT,
  bank TEXT, ref_no TEXT, category TEXT, memo TEXT, source TEXT)
category is one of: {categories}

Each turn reply with EXACTLY ONE line, nothing else:
  ACTION: sql("SELECT ...")      -- run one read-only SQLite query
  ANSWER: your final answer       -- once a result lets you answer

Rules:
- Use single quotes for strings inside SQL, e.g. category = 'food'.
- Money spent = direction = 'expense'; money received = direction = 'income'.
- Filter months with strftime('%Y-%m', date) = '2026-10'.
- Let SQL do the arithmetic (SUM, COUNT, AVG, GROUP BY) - never add numbers yourself.
- Answer in the same language as the question, include the number and 'บาท'/'THB'."""

FORCE_ANSWER = "Stop querying. Using the TOOL RESULTs above, reply now with `ANSWER: ...`."

ACTION_RE = re.compile(r'ACTION:\s*sql\(\s*"(.*)"\s*\)', re.S)
FENCE_RE = re.compile(r"ACTION:.*?```(?:sql)?\s*(.+?)```", re.S)


def _parse_action(reply: str) -> str | None:
    if m := ACTION_RE.search(reply):
        return m.group(1).strip()
    if m := FENCE_RE.search(reply):  # tolerate ```sql fences around the query
        return m.group(1).strip()
    return None


def _answer_text(reply: str) -> str:
    return re.sub(r"^\s*ANSWER:\s*", "", reply.strip()).strip()


async def run(question: str, *, db_path: str | None = None, today: date | None = None,
              max_steps: int = MAX_STEPS) -> AskResponse:
    db_path = db_path or config.LEDGER_DB
    system = SYSTEM.format(today=(today or date.today()).isoformat(), categories=", ".join(CATEGORIES))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
    steps: list[AgentStep] = []

    for _ in range(max_steps):
        reply = (await llm.complete(messages, max_tokens=300)).strip()
        query = _parse_action(reply)
        if query is None:  # ANSWER (or anything that isn't a tool call) -> done
            return AskResponse(answer=_answer_text(reply), steps=steps, stopped_by="answer")
        observation = run_sql(db_path, query)
        steps.append(AgentStep(action=query, observation=observation))
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": f"TOOL RESULT:\n{observation}"})

    # Stop condition hit: force one last answer from what we have.
    messages.append({"role": "user", "content": FORCE_ANSWER})
    final = await llm.complete(messages, max_tokens=300)
    return AskResponse(answer=_answer_text(final), steps=steps, stopped_by="max_steps")


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "How much did I spend on food this month?"
    result = asyncio.run(run(q))
    for i, s in enumerate(result.steps, 1):
        print(f"--- step {i} ---\nSQL: {s.action}\n{s.observation}\n")
    print(f"=== answer ({result.stopped_by}) ===\n{result.answer}")
