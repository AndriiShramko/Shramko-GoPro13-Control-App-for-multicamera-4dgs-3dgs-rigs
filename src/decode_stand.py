"""Decode stand frames (Gray-code strip) from video/PNG frames.

Validates itself against the headless self-test set (stand.py render-selftest)
BEFORE being trusted on camera footage (red-team #15). For camera clips the
strip is located by the fixed 8-cell sync pattern, tolerating scale/offset —
Phase-0 assumes a roughly fronto-parallel camera (operator aims once).

Usage:
    python src/decode_stand.py selftest captures/selftest
    python src/decode_stand.py video captures/<take>/GX010001.MP4 --out docs/data/<take>.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np

GRAY_BITS = 24
SYNC_PATTERN = [1, 0, 1, 1, 0, 0, 1, 0]
SYNC_TRAIL = [0, 1, 0, 0, 1, 1, 0, 1]
N_CELLS = GRAY_BITS + len(SYNC_PATTERN) + len(SYNC_TRAIL)


def from_gray(g: int) -> int:
    b = 0
    while g:
        b ^= g
        g >>= 1
    return b


def find_strip(gray_img: np.ndarray) -> tuple[int, int, int] | None:
    """Locate the cell strip: returns (row, x_start, cell_px) or None.
    Strategy: scan rows for a run of alternating high-contrast square cells
    matching SYNC_PATTERN at the strip's left edge."""
    h, w = gray_img.shape
    blur = cv2.GaussianBlur(gray_img, (5, 5), 0)
    _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    lead = len(SYNC_PATTERN)
    for frac in np.linspace(0.1, 0.9, 33):
        row = int(h * frac)
        line = bw[row].astype(np.int16) // 255
        # cell sizes LARGE -> small: the true size matches both anchors; small
        # aliases usually fail the trailing anchor (self-test proved the risk)
        for cell in range(w // N_CELLS, max(5, w // 200), -1):
            span = cell * N_CELLS
            if span > w:
                continue
            for x0 in range(0, w - span, max(1, cell // 4)):
                lead_c = [x0 + i * cell + cell // 2 for i in range(lead)]
                if [int(line[c]) for c in lead_c] != SYNC_PATTERN:
                    continue
                trail_c = [x0 + (lead + GRAY_BITS + j) * cell + cell // 2
                           for j in range(len(SYNC_TRAIL))]
                if [int(line[c]) for c in trail_c] == SYNC_TRAIL:
                    return row, x0, cell
    return None


def decode_frame(img: np.ndarray, loc: tuple[int, int, int] | None = None):
    gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    mean = float(gray_img.mean())
    if mean > 200:
        return {"flash": 1, "idx": None, "loc": loc}
    if loc is None:
        loc = find_strip(gray_img)
        if loc is None:
            return {"flash": 0, "idx": None, "loc": None}
    row, x0, cell = loc
    # sample a small patch at each cell center, average rows around strip line
    y0 = max(0, row - cell // 3)
    y1 = min(gray_img.shape[0], row + cell // 3)
    vals = []
    for i in range(N_CELLS):
        cx = x0 + i * cell + cell // 2
        patch = gray_img[y0:y1, max(0, cx - cell // 4):cx + cell // 4]
        vals.append(float(patch.mean()))
    lo, hi = min(vals), max(vals)
    if hi - lo < 30:  # no contrast -> lost strip
        return {"flash": 0, "idx": None, "loc": None}
    thresh = (lo + hi) / 2
    bits = [1 if v > thresh else 0 for v in vals]
    lead = len(SYNC_PATTERN)
    if (bits[:lead] != SYNC_PATTERN
            or bits[lead + GRAY_BITS:] != SYNC_TRAIL):
        return {"flash": 0, "idx": None, "loc": None}
    g = 0
    for b in bits[lead:lead + GRAY_BITS]:
        g = (g << 1) | b
    return {"flash": 0, "idx": from_gray(g), "loc": loc}


def run_selftest(d: Path) -> int:
    truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
    ok = bad = 0
    loc = None
    failures = []
    for rec in truth:
        img = cv2.imread(str(d / f"st_{rec['i']:06d}.png"))
        out = decode_frame(img, loc)
        loc = out["loc"] or loc
        expected_flash = rec["flash"]
        if out["flash"] != expected_flash:
            bad += 1; failures.append((rec["i"], "flash", out))
        elif not expected_flash and out["idx"] != rec["i"]:
            bad += 1; failures.append((rec["i"], "idx", out["idx"]))
        else:
            ok += 1
    total = len(truth)
    rate = ok / total * 100
    print(f"selftest: {ok}/{total} decoded correctly ({rate:.2f}%)")
    for f in failures[:10]:
        print("  FAIL", f)
    return 0 if rate >= 99.0 else 1


def run_video(video: Path, out_csv: Path):
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        sys.exit(f"cannot open {video}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    loc = None
    n = decoded = 0
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["video_frame", "video_pts_s", "stand_idx", "flash", "src"])
        while True:
            ret, img = cap.read()
            if not ret:
                break
            pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            out = decode_frame(img, loc)
            loc = out["loc"] or loc
            wr.writerow([n, f"{pts:.6f}",
                         out["idx"] if out["idx"] is not None else "",
                         out["flash"], video.name])
            if out["idx"] is not None or out["flash"]:
                decoded += 1
            n += 1
    print(f"{video.name}: fps={fps:.3f} frames={n} decoded={decoded} "
          f"({decoded/max(1,n)*100:.1f}%) -> {out_csv}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("selftest"); p.add_argument("dir")
    p = sub.add_parser("video"); p.add_argument("video"); p.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "selftest":
        sys.exit(run_selftest(Path(args.dir)))
    run_video(Path(args.video), Path(args.out))


if __name__ == "__main__":
    main()
