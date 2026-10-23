"""Evaluate slip extraction against a hand-labelled gold set: prompts "baseline",
"fixed_keys" and "v2" (see app/services/extractor.py), plus a majority-class
category baseline.

Gold set: eval/slips/labels.jsonl — one line per (masked) image in eval/slips/:
  {"file": "bbl_001.jpg", "date": "2026-10-18", "time": "16:22", "amount": 59.0,
   "fee": 0.0, "direction": "expense", "counterparty": "บริษัท ไมเนอร์ ดีคิว จำกัด",
   "ref_no": "2026...", "category": "food"}
Leave a field out (or null) if it isn't on the slip — it is then not scored.

Metrics (per prompt)
  json_ok     reply parsed as JSON (after the one repair retry)
  valid       passed schema validation -> a usable draft
  <field>     per-field accuracy over the slips that have that field labelled
  all_key     date + amount + direction all correct (the fields a ledger can't do without)

Run:  uv run python -m eval.eval_extract
      uv run python -m eval.eval_extract --prompts v2 --limit 5
      uv run python -m eval.eval_extract --rescore     # code change only, same model output
      uv run python -m eval.eval_extract --slips-dir private   # unmasked originals;
          # per-slip results (which contain names) then go to private/results, not Git
"""
import argparse
import asyncio
import json
import mimetypes
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from app import config
from app.services import extractor

HERE = Path(__file__).parent
SLIPS = HERE / "slips"
LABELS = SLIPS / "labels.jsonl"
RESULTS = HERE / "results"
FIELDS = ["date", "time", "amount", "fee", "direction", "counterparty", "ref_no", "category"]
KEY_FIELDS = ["date", "amount", "direction"]


def _norm_name(s: str) -> str:
    s = re.sub(r"^(นาย|นางสาว|นาง|น\.ส\.|mr\.?|ms\.?|mrs\.?)\s*", "", s.strip().lower())
    return re.sub(r"\s+", " ", s)


def field_correct(field: str, got, gold) -> bool:
    if got is None:
        return False
    if field in ("amount", "fee"):
        return abs(float(got) - float(gold)) < 0.005
    if field == "counterparty":  # names: forgive titles/spacing, allow small OCR slips
        return SequenceMatcher(None, _norm_name(str(got)), _norm_name(str(gold))).ratio() >= 0.85
    if field == "ref_no":
        return re.sub(r"\s", "", str(got)) == re.sub(r"\s", "", str(gold))
    return str(got) == str(gold)


async def main(prompts: list[str], limit: int | None, slips_dir: Path = SLIPS,
               rescore: bool = False) -> None:
    labels = slips_dir / "labels.jsonl"
    results = RESULTS if slips_dir.resolve() == SLIPS.resolve() else slips_dir / "results"
    if not labels.exists():
        raise SystemExit(f"No gold set yet: add masked slips to {slips_dir} and label them in {labels.name} "
                         "(see eval/slips/README.md).")
    gold = [json.loads(l) for l in labels.read_text(encoding="utf-8").splitlines() if l.strip()]
    gold = gold[:limit] if limit else gold
    # Masked slips can show the placeholder OWNER instead of your name.
    owners = config.OWNER_NAMES + ["OWNER"]
    results.mkdir(exist_ok=True)
    summary = {}

    for prompt in prompts:
        rows = []
        # --rescore: reuse the model's saved output and re-run only the code (to_draft),
        # so a code change is measured on its own — no model calls, no run-to-run noise.
        saved = {}
        if rescore:
            saved = {r["file"]: r["raw"] for r in json.loads(
                (results / f"extract_{prompt}.json").read_text(encoding="utf-8"))["rows"]}
        for g in gold:
            path = slips_dir / g["file"]
            mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
            raw = saved[g["file"]] if rescore else await extractor.read_image(path.read_bytes(), mime, prompt)
            res = extractor.to_draft(raw, owners, config.OWNER_ACCOUNTS)
            draft = res.draft.model_dump(mode="json") if res.draft else {}
            scores = {f: field_correct(f, draft.get(f), g[f]) for f in FIELDS if g.get(f) is not None}
            rows.append({"file": g["file"], "json_ok": "_error" not in raw, "valid": res.draft is not None,
                         "scores": scores, "draft": draft, "raw": raw, "errors": res.errors})
            wrong = [f for f, ok in scores.items() if not ok]
            print(f"[{prompt}] {g['file']}: {'valid' if res.draft else 'INVALID'} wrong={wrong}")

        n = len(rows)
        s = {"n": n,
             "json_ok": sum(r["json_ok"] for r in rows) / n,
             "valid": sum(r["valid"] for r in rows) / n,
             "all_key": sum(all(r["scores"].get(f, False) for f in KEY_FIELDS) for r in rows) / n}
        for f in FIELDS:
            scored = [r["scores"][f] for r in rows if f in r["scores"]]
            s[f] = sum(scored) / len(scored) if scored else None
        summary[prompt] = s
        (results / f"extract_{prompt}.json").write_text(
            json.dumps({"summary": s, "rows": rows}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")

    cols = ["json_ok", "valid", "all_key", *FIELDS]
    print("\n| prompt | " + " | ".join(cols) + " |\n|---|" + "---|" * len(cols))
    for p, s in summary.items():
        print(f"| {p} | " + " | ".join("-" if s[c] is None else f"{s[c]:.0%}" for c in cols) + " |")

    label, acc = majority_category(gold)
    if label is not None:
        print(f"\nmajority-class category baseline: always {label!r} -> category accuracy {acc:.0%}")
        (results / "extract_majority.json").write_text(
            json.dumps({"label": label, "category_accuracy": acc, "n": len(gold)}), encoding="utf-8")


def majority_category(gold: list[dict]) -> tuple[str | None, float]:
    """Baseline B: no model at all — predict the most common category for every slip.
    A model is only worth asking for the category if it beats this.
    (The label is taken from the gold set itself, which flatters this baseline slightly.)"""
    cats = [g["category"] for g in gold if g.get("category")]
    if not cats:
        return None, 0.0
    label, count = Counter(cats).most_common(1)[0]
    return label, count / len(cats)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--prompts", nargs="+", default=list(extractor.PROMPTS), choices=list(extractor.PROMPTS))
    p.add_argument("--limit", type=int)
    p.add_argument("--slips-dir", type=Path, default=SLIPS,
                   help="folder with the images + labels.jsonl (default: eval/slips)")
    p.add_argument("--rescore", action="store_true",
                   help="re-score the saved model output with the current code (no model calls)")
    a = p.parse_args()
    asyncio.run(main(a.prompts, a.limit, a.slips_dir, a.rescore))
