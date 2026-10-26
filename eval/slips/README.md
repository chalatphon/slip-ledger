# Slip test set (anonymized)

20 real Bangkok Bank transfer slips (4–22 Oct 2026) with **the people replaced by
fictitious ones**, so the extraction eval can be re-run from this repo:

```bash
uv run python -m eval.eval_extract
```

## What was changed, and what was not

Every image carries a red **"TEST DATA · ข้อมูลทดสอบ (นามสมมติ)"** strip along the top
(one along the bottom, next to the reference number, made the model skip that number). `make_mock.py` built these images from the originals, which stay out of Git.

| On the slip | In this set |
|---|---|
| Owner's name (under "จาก") | fictitious: **นาย สมชาย ทดสอบ** |
| Payees who are private people (slips 5, 7, 8, 16, 19, 20) | fictitious names |
| Their phone, PromptPay ID and e-wallet numbers | fictitious numbers |
| QR code (encodes the real transaction) | replaced by a QR that encodes only "TEST DATA" |
| Shop names, amounts, dates, times, fees, reference numbers | **unchanged**: these are what the eval scores, and they identify no private person |
| Owner's masked account number (226-0-xxx569) | unchanged, as the bank prints it |

`labels.jsonl` holds the answers (dates in C.E., direction from the owner's point of
view; payee names are the fictitious ones). `owner.json` tells the eval who the owner is
on these slips (name `สมชาย`, account ending `569`), so it needs no `.env`.

## Adding slips

Put originals in `private/` (gitignored), add their lines to `PEOPLE` in `make_mock.py`
if a payee is a private person, run it, then add the answers to `labels.jsonl`.
