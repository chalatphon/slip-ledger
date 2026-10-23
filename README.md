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

- **CSV in prompt:** the whole ledger pasted into the prompt, no tools — the LLM-only baseline.
- **Rules (no LLM):** `eval/rule_baseline.py` — keyword rules + SQL templates.
- **Agent:** the SQL agent.

```bash
uv run python -m eval.eval_agent
```

| method | accuracy | correct / n | avg tool calls |
|---|---|---|---|
| CSV in prompt | 15% | 3 / 20 | 0 |
| rules (no LLM) | **80%** | 16 / 20 | 0 |
| SQL agent | 70% | 14 / 20 | 1.3 |

Same numbers on two separate runs. Per-question answers and SQL traces:
`eval/results/agent_*.json`.

**Reading this honestly.** The agent beats asking the model to do arithmetic itself
(15% → 70%), but on *this* test set hand-written rules beat the agent. Caveat that
favours the rules: they were written by someone who had already seen these 20
questions, so they match their phrasing. The rules also fail in ways that don't
show up as errors:

- q11: "ยา" (medicine) is a substring of "กันยายน" (September), so the rule silently
  filters to `health` — a wrong number, not "I don't know".
- q15: the question says "ข้าวมันไก่ป้าศรี" without "ร้าน", so no payee matches and
  the rule counts *all* food purchases (23 instead of 8).
- q03: "กี่บาท" isn't one of the "how much" keywords, so no rule fires.

Agent failures (from its SQL traces): adding a month filter the question didn't ask
for (q14, q16); guessing a payee name that doesn't exactly match (q15); using a
category instead of the payee (q07 "ค่าไฟ" → all `bills`, q20); and one scorer
false-negative (q12 answered "หมวดอาหาร", the label is `food`).

Next step: a held-out set of new phrasings written *without* looking at the rules or
the agent prompt, to test which approach generalizes.

### 2. Slip extraction (`eval/eval_extract.py`)

20 real Bangkok Bank slips (4–22 Oct 2026), hand-labelled (`labels.jsonl`). Compares
four prompts, each adding one thing:

| prompt | what it adds |
|---|---|
| `baseline` | the reference app's generic "extract key info"; the model picks its own keys |
| `fixed_keys` | + a fixed key set and the category list (no Thai-slip hints) |
| `v2` | + Thai-slip hints: where payer/payee/ref are, copy dates as printed, null if absent |
| `v3` | + payer/payee account numbers, a ref_no hint naming the footer number |

plus a **majority-class** category baseline (always "food", no model). Metrics: schema
validity, per-field accuracy, and `all_key` (date + amount + direction all correct).
Two code changes apply to every prompt: the owner is matched by account suffix
(`OWNER_ACCOUNTS`) before name, and a `ref_no` that doesn't start with the slip's
`YYYYMMDDHHMM` is swapped for a value the model read that does.

```bash
uv run python -m eval.eval_extract                 # all prompts, model calls
uv run python -m eval.eval_extract --rescore       # current code on saved model output
```

**Before → after the code changes** (`baseline`, the same saved model output re-scored
with `--rescore`, so only the code differs):

| | all_key | direction | ref_no |
|---|---|---|---|
| before | 5% | 5% | 30% |
| after | **90%** | **90%** | 65% |

(`v2` scored 55% direction / 0% ref on its first run and 70% / 10% on the next two; its
first output wasn't kept, so that change mixes the code with run-to-run variance.)

**Current code, all prompts** (v2 and v3 over two fresh runs; v2 identical both times):

| prompt | valid | all_key | date | time | amount | direction | counterparty | ref_no | category |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 90% | **90%** | 90% | 60% | 90% | **90%** | 35% | 65% | 6% |
| fixed_keys | 80% | 0% | 80% | 80% | 80% | 0% | 45% | 0% | 44% |
| v2 | 100% | 70% | 100% | 100% | 100% | 70% | 45% | 10% | 69% |
| v3 | 100% | 70–80% | 100% | 100% | 100% | 70–80% | 45% | **70–75%** | 62–69% |

Majority class ("food" every time): category 38%.

**Reading this honestly.**

- The biggest gain came from *code*, not prompts: matching the owner by account number
  (the model misread the owner's Thai name on every slip) and checking the ref number
  against the slip's own timestamp.
- That gain is largest for the generic `baseline` prompt, because it writes out every
  number it sees (payer account, both ref numbers), so the code has something to
  check. `v2`/`v3` often return the payer as `null`; their "expense" is then a guess
  (no payer → treated as a shop receipt), right on these slips only because all 20
  are payments.
- `v3` is the only prompt that gets date, time and amount on every slip *and* most ref
  numbers, and it is the default in the app. `baseline` wins on direction.
- The test set was also the development set: v3 and the code checks were written
  after looking at these slips. A held-out set (other banks, incoming transfers) is
  needed before trusting any of these numbers beyond this bank's layout.
- Results vary between runs at temperature 0 (v3 moved 5–10 points between two runs).

The slips contain other people's names, so the images are kept out of Git for now
(see Privacy).

## Privacy

Raw slips contain names and account numbers. Originals go in `private/` (gitignored);
only masked copies are committed. `.env` is gitignored; the key is read from the
environment.
