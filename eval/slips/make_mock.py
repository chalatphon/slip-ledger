"""Build the public test set from the private originals: same slips, fake people.

    uv run --with pillow --with numpy --with qrcode python eval/slips/make_mock.py

Reads private/slip_*.png (gitignored, the real slips) and writes eval/slips/slip_*.png:
  - the owner's name (under "จาก") -> a fictitious name, on every slip
  - payees who are private people, their phone / ID / e-wallet numbers -> fictitious
  - the QR code (it encodes the real transaction) -> a QR that encodes only "TEST DATA"
  - a "TEST DATA" strip along the top of every image
Shop names, amounts, dates, times and reference numbers are kept: they are what the
evaluation scores, and they identify no private person.

Text lines are found from the slip layout (dark-pixel rows in the text column of the
white card); every Bangkok Bank slip in the set has the owner's name on line 3.
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SRC, DST = ROOT / "private", ROOT / "eval" / "slips"
BOLD, REGULAR = "C:/Windows/Fonts/LeelaUIb.ttf", "C:/Windows/Fonts/LeelawUI.ttf"

OWNER = "นาย สมชาย ทดสอบ"
# slip -> {text line index: (fictitious text, bold?)}. Line 3 (owner) is added for every slip.
PEOPLE = {
    "slip_05.png": {6: ("นาย ธนพล มีสุข", True), 7: ("089-xxx-1234", True)},
    "slip_07.png": {6: ("Wichai Somboon", True)},
    "slip_08.png": {6: ("นาย ประเสริฐ ใจงาม", True), 11: ("00499-000-111-2222", False)},
    "slip_16.png": {6: (OWNER, True), 7: ("1-2345-0xxxx-67-8", True)},
    "slip_19.png": {6: ("น.ส. สุดา รักดี", True), 7: ("081-xxx-5678", True)},
    "slip_20.png": {6: ("นาย กิตติ ชัยมงคล", True), 11: ("00499-000-333-4444", False)},
}
WATERMARK = "TEST DATA · ข้อมูลทดสอบ (นามสมมติ)"


def card_box(a):
    h, _ = a.shape
    light = a > 200
    xs = np.where(light[h // 2])[0]
    x0, x1 = xs.min(), xs.max()
    ys = np.where(light[:, (x0 + x1) // 2])[0]
    return x0, x1, ys.min(), ys.max()


def text_lines(a, x0, x1, y0, y1, thr=110, gap=2, minh=10):
    """(top, bottom, left, right) of each dark text line in the box."""
    dark = a[y0:y1, x0:x1] < thr
    rows = dark.sum(1) > 1
    out, start, last = [], None, -99
    for y, v in enumerate(rows):
        if v:
            if start is None:
                start = y
            last = y
        elif start is not None and y - last > gap:
            if last - start >= minh:
                cols = np.where(dark[start:last + 1].any(0))[0]
                out.append((y0 + start, y0 + last, x0 + cols.min(), x0 + cols.max()))
            start = None
    return out


def replace_line(img, a, line, text, bold, size):
    top, bottom, _, right = line
    # Text starts after the gap that follows the round icon (when the line has one).
    cols = np.where((a[top:bottom + 1, 250:right + 1] < 110).any(0))[0] + 250
    gaps = [cols[i + 1] for i in range(len(cols) - 1) if cols[i + 1] - cols[i] > 15]
    left = int(gaps[0]) if gaps and cols.min() < 380 else int(cols.min())
    ink = tuple(int(c) for c in np.median(
        np.array(img)[top:bottom + 1, left:right + 1][a[top:bottom + 1, left:right + 1] < 110], axis=0))
    d = ImageDraw.Draw(img)
    d.rectangle([left - 6, top - 16, right + 8, bottom + 12], fill="white")
    font = ImageFont.truetype(BOLD if bold else REGULAR, size)
    d.text((left, bottom + 1), text, font=font, fill=ink, anchor="ls")


def replace_qr(img, a, card):
    """Swap the real QR (it encodes the transaction) for one that encodes only a
    test-data notice. A blank box would also hide it, but the slip would no longer look
    like a slip: the extraction prompt points the model at "the number next to the QR"."""
    import qrcode

    x0, x1, _, y1 = card
    qx0 = x0 + int(0.65 * (x1 - x0))
    ys, xs = np.where(a[y1 - 420:y1, qx0:x1] < 110)
    # the QR is the largest dark block in the bottom-right; its rows end above the caption
    top, left, right = y1 - 420 + ys.min(), qx0 + xs.min(), qx0 + xs.max()
    side = right - left
    fake = qrcode.make("TEST DATA - not a real transaction (Slip Ledger eval)", border=0)
    img.paste(fake.convert("RGB").resize((side, side), Image.NEAREST), (left, top))


def watermark(img):
    w, h = img.size
    d = ImageDraw.Draw(img, "RGBA")
    font = ImageFont.truetype(BOLD, 34)
    # Top edge only: a strip along the bottom sat next to the footer reference number,
    # and the model then stopped reading it (v3 ref_no 85% -> 10% on this set).
    d.rectangle([0, 0, w, 64], fill=(176, 0, 32, 225))
    d.text((w // 2, 32), WATERMARK, font=font, fill="white", anchor="mm")


def main():
    for src in sorted(SRC.glob("slip_*.png")):
        img = Image.open(src).convert("RGB")
        a = np.array(img.convert("L")).astype(int)
        card = card_box(a)
        x0, x1, y0, y1 = card
        lines = text_lines(a, x0 + int(0.43 * (x1 - x0)), x1 - 5, y0 + 5, y1 - 5)
        edits = {3: (OWNER, True), **PEOPLE.get(src.name, {})}
        # Font size from the owner's account line (line 4, "226-0-xxx569"): digits fill
        # about 70% of the font size, and the slip prints names at the same size.
        size = round((lines[4][1] - lines[4][0]) / 0.70)
        for idx, (text, bold) in edits.items():
            replace_line(img, a, lines[idx], text, bold, size)
        replace_qr(img, a, card)
        watermark(img)
        img.save(DST / src.name)
        print(src.name, "edited lines", sorted(edits))


if __name__ == "__main__":
    main()
