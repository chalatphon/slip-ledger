# Slip Ledger

Turn photos of **Thai bank transfer slips and shop receipts** — or a line you type,
like "ข้าวมันไก่ 50 บาท เมื่อวาน" — into a clean income / expense ledger, then **ask
questions about your money in Thai or English** ("เดือนนี้ใช้ค่ากินไปเท่าไหร่?").

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
  services/text_entry.py    a typed line -> the same kind of draft (relative dates resolved in code)
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

**Test set:** 20 real Bangkok Bank slips (4–22 Oct 2026) in `eval/slips/`, with the
people replaced by fictitious ones (names, phone/ID numbers, QR) — see
`eval/slips/README.md`. Amounts, dates, shop names and reference numbers are real.
Answers in `eval/slips/labels.jsonl`, all 8 fields on every slip.

Four prompts, each adding one thing:

| prompt | what it adds |
|---|---|
| `baseline` | the reference app's generic "extract key info"; the model picks its own keys |
| `fixed_keys` | + a fixed key set and the category list (no Thai-slip hints) |
| `v2` | + Thai-slip hints: where payer/payee/ref are, copy dates as printed, null if absent |
| `v3` (used by the app) | + payer/payee account numbers, a ref_no hint naming the footer number |

plus a **majority-class** category baseline (always "food", no model). Two code checks
apply to every prompt: the owner is matched by account suffix before name, and a
`ref_no` that doesn't start with the slip's `YYYYMMDDHHMM` is swapped for a value the
model read that does.

```bash
uv run python -m eval.eval_extract                         # all prompts (80 model calls)
uv run python -m eval.eval_extract --rescore               # current code on saved output
uv run python -m eval.eval_extract --rescore --no-checks   # same output, without the checks
```

**Results** (`eval/results/extract_*.json`):

| prompt | valid | all_key | date | time | amount | direction | counterparty | ref_no | category |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 90% | **90%** | 90% | 60% | 90% | **90%** | 55% | 65% | 5% |
| fixed_keys | 85% | 85% | 85% | 85% | 85% | 85% | 60% | 0% | 45% |
| v2 | 100% | 75% | 100% | 100% | 100% | 75% | 60% | 0% | 60% |
| **v3** | **100%** | **90%** | **100%** | **100%** | **100%** | **90%** | **65%** | **85%** | 50% |

Majority class ("food"): category 50%.

**Code checks, on vs off** (same model output, `--no-checks`): baseline `ref_no` 25% →
65%; everything else unchanged on this set.

**Reading this honestly.**

- v3 is the only prompt that gets date, time and amount on every slip, every draft
  valid, and the most reference numbers (85%). It ties the generic prompt on `all_key`
  (90%) and its category guess is no better than always answering "food" (50%).
- The account-number check matters on the **original** slips, not on this set: the
  model misread the real owner's Thai name on every one of them (baseline direction
  5% → 90% with the check), but it reads the fictitious "สมชาย" cleanly. On the
  originals, v3 scored 70–80% `all_key`, 70–75% `ref_no`, 60–65% category over two runs.
- Anonymizing changed the scores: a red "TEST DATA" strip along the bottom, next to
  the reference number, made v3 skip it (85% → 10%); the strip is now on top only.
- The slips were also the development set: v3 and the checks were written after
  looking at them. Results vary between runs at temperature 0 (5–10 points).

## Privacy

Raw slips contain names and account numbers. Originals stay in `private/` (gitignored);
only the anonymized copies built by `eval/slips/make_mock.py` are committed. `.env` is gitignored; the key is read from the
environment.
