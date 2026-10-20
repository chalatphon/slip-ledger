# Slip gold set

The extraction eval (`eval/eval_extract.py`) reads the images in this folder and the
hand-written answers in `labels.jsonl`.

## Adding a slip (aim for ~30, from several banks + some shop receipts)

1. Put the **original** image in `private/` at the repo root (gitignored — never committed).
2. Make a **masked copy** here: cover your full name, account number and QR code
   (any image editor; a solid box is enough). Keep the amount, date, time, payee and
   reference number visible — those are what we score. If a reference number is
   sensitive, replace it with a fake one *in the image and the label*.
3. Name it `<bank>_<nnn>.<ext>`, e.g. `bbl_001.jpg`, `kbank_004.png`, `receipt_002.jpg`.
4. Add one line to `labels.jsonl` with what is really on the slip:

```json
{"file": "bbl_001.jpg", "date": "2026-10-18", "time": "16:22", "amount": 59.0, "fee": 0.0, "direction": "expense", "counterparty": "บริษัท ไมเนอร์ ดีคิว จำกัด", "ref_no": "2026101816220924003073908", "category": "food"}
```

- `date` in ISO (`YYYY-MM-DD`, C.E.) — the app converts B.E. itself.
- `direction` is from **your** point of view. The eval treats both `OWNER_NAMES` (from
  `.env`) and the word `OWNER` as you — so either leave your first name visible, or
  write `OWNER` over your masked name so the model can still tell payer from payee.
- Leave a field out if it isn't on the image; it then isn't scored.

Then run `uv run python -m eval.eval_extract`.
