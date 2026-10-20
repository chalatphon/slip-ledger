# Slip Ledger

Turn photos of **Thai bank transfer slips and shop receipts** into a clean income /
expense ledger, then **ask questions about your money in Thai or English**
("เดือนนี้ใช้ค่ากินไปเท่าไหร่?").

Built for the KMITL *Building LLM-Powered Applications* capstone. All model calls go
through the class LiteLLM proxy (`gemma-4-E4B-it`) with the OpenAI client.

```
 slip image ──► model transcribes ──► code normalizes ──► Pydantic validates ──► user confirms ──► SQLite
                (JSON + 1 repair)     (B.E. dates, ฿)     (Transaction schema)    (edit draft)        │
 question ───► agent loop ──► ACTION: sql("SELECT …") ──► guarded read-only SQL ──► ANSWER ◄─────────┘
```

## Course capability families covered

| Family | Where |
|---|---|
| Structured output + schema validation (multimodal) | `app/services/extractor.py`, `app/schemas.py` |
| Agent loop with a tool + stop condition | `app/agent/loop.py` |
| Advanced capability: SQL tool (allow-listed, read-only) | `app/agent/tools.py` |
| Evaluation on a labelled set vs a baseline | `eval/` |
| Served interface (FastAPI + web UI) | `app/routers/api.py`, `web/` |

## Quickstart

```bash
uv sync
cp .env.example .env          # put your course key in OPENAI_API_KEY, your name in OWNER_NAMES
uv run python -m eval.seed --load data/ledger.db   # optional: 3 months of demo data
uv run uvicorn app.main:app --reload --port 8000   # http://localhost:8000
```

Tests (the model is stubbed — no key needed): `uv run pytest -q`

> On a OneDrive folder, uv can't hardlink; `pyproject.toml` sets `link-mode = "copy"`.

## Layout (router → service → repository)

```
app/
  routers/api.py            HTTP only: parse request, call a service, map errors to status codes
  services/ledger.py        use-cases: extract a draft, save, list, ask
  services/extractor.py     image -> JSON (parse + repair) -> normalized, validated draft
  repositories/transactions.py   the only SQL that writes the ledger
  agent/loop.py, tools.py   the agent loop and its guarded SQL tool
  normalize.py              deterministic Thai date / amount / time parsing
  llm.py, config.py         the one model client; env-based config
web/                        plain HTML/JS UI (renders model output as text, never HTML)
eval/                       evaluation scripts, data and results
tests/                      unit + in-process API tests
```

## Design choices worth defending

- **The model transcribes, code converts.** The prompt asks for the date *as printed*
  ("18 ต.ค. 69"); `normalize.py` maps Thai months and Buddhist-era years. A 4B model is
  unreliable at "69 B.E. = 2026", a lookup table is not.
- **Direction is decided by code**, by matching `OWNER_NAMES` against payer/payee — not
  by asking the model "is this income?".
- **Human in the loop for money.** `/api/extract` only returns a *draft*; nothing is
  saved until the user confirms or edits it. `ref_no` is `UNIQUE`, so the same slip
  can't be booked twice.
- **The SQL is untrusted.** Three independent guards: a text check (one `SELECT`), a
  read-only connection (`mode=ro`), and an sqlite authorizer that only allows reading
  the `transactions` table. Plus a VM-step budget and a row cap.
- **Let the database do the maths.** The agent writes `SUM/COUNT/GROUP BY`; the model
  never adds numbers itself.
- `temperature=0` everywhere, so evaluation runs are repeatable.

## Evaluation

### 1. Question answering over the ledger (`eval/eval_agent.py`)

20 questions (Thai + English) over a fixed, synthetic 3-month ledger
(`eval/data/seed_ledger.csv`, generated with a fixed seed by `eval/seed.py`). Each
question has a `gold_sql`; the expected answer is computed from it, so labels can't
drift from the data. Metric: answer accuracy (expected value appears in the answer).

- **Baseline:** the whole ledger pasted into the prompt as CSV, no tools.
- **Agent:** the SQL agent.

```bash
uv run python -m eval.eval_agent
```

| method | accuracy | correct / n | avg tool calls |
|---|---|---|---|
| baseline (CSV in prompt) | 15% | 3 / 20 | 0 |
| SQL agent | 70% | 14 / 20 | 1.3 |

Per-question answers and SQL traces: `eval/results/agent_*.json`.

Failure modes seen (first run): adding a month filter the question didn't ask for
(q14, q16); guessing a payee name that doesn't exactly match (q15); using a category
instead of the payee (q07 "ค่าไฟ" → all `bills`, q20); and one scorer false-negative
(q12 answered "หมวดอาหาร", the label is `food`).

### 2. Slip extraction (`eval/eval_extract.py`)

Hand-labelled, masked slips in `eval/slips/` (see `eval/slips/README.md`). Compares the
naive prompt (`baseline`, the reference app's generic "extract key info") with `v2`
(fixed keys, Thai-slip hints, closed category list). Metrics: JSON parse rate, schema
validity, per-field accuracy, and `all_key` (date + amount + direction all correct).

```bash
uv run python -m eval.eval_extract
```

*Results: TODO once the gold set is labelled.*

## Privacy

Raw slips contain names and account numbers. Originals go in `private/` (gitignored);
only masked copies are committed. `.env` is gitignored; the key is read from the
environment.
