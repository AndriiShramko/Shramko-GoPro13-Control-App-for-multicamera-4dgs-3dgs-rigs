"""Decode stand frames (Gray-code strip) from video/PNG frames.

Locator/decoder v5 — evolution driven by real footage (2026-07-12):
  v1 integer row-scan: broke on curved panel + fractional pitch (4/300).
  v2 blob band + linear pitch from outer anchors: perspective drifted a full
     cell mid-strip (lead anchor mismatched while trail matched).
  v3 quadratic x(u) from 1-2-1 anchor blob trios: blob structure differs
     between sharp renders (no merge) and camera blur (merge) — fragile.
  v4 1-param projective grid-search: distortion is S-shaped (curved panel +
     lens), lead wanted k=-0.25 while trail wanted k=+0.05 — no global fit.
  v5 run-length barcode decode along the curved strip profile with a locally
     adapting pitch — tolerates smooth ARBITRARY x-distortion. Validation is
     structural: exactly N_CELLS cells and both anchors must match.

Self-validates on the headless render set (stand.py render-selftest) BEFORE
being trusted on camera footage (red-team #15).

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
START_MARKER = [1, 1, 1, 0]   # 3-run "violation" — cannot occur inside data
END_MARKER = [0, 1, 1, 1]
DATA_CELLS = GRAY_BITS * 2    # Manchester: bit 1 -> (1,0), bit 0 -> (0,1)
N_CELLS = len(START_MARKER) + DATA_CELLS + len(END_MARKER)


def encode_cells(frame_idx: int) -> list[int]:
    """Cell pattern for the stand renderer (shared with the decoder).
    Manchester keeps every run <= 3 cells, so local pitch is continuously
    re-calibrated by transitions — the property Gray-strip v1..v6 lacked, and
    pair validity (01/10) rejects mis-allocated frames instead of silently
    decoding a wrong index."""
    g = frame_idx ^ (frame_idx >> 1)  # Gray, MSB first
    cells = list(START_MARKER)
    for i in range(GRAY_BITS - 1, -1, -1):
        cells.extend([1, 0] if (g >> i) & 1 else [0, 1])
    cells.extend(END_MARKER)
    return cells


def from_gray(g: int) -> int:
    b = 0
    while g:
        b ^= g
        g >>= 1
    return b


def _downscale(img: np.ndarray) -> np.ndarray:
    if img.shape[1] > 1400:
        s = 1280 / img.shape[1]
        img = cv2.resize(img, (1280, int(img.shape[0] * s)),
                         interpolation=cv2.INTER_AREA)
    return img


def find_strip(gray_img: np.ndarray):
    """Locate the strip band. Returns loc dict {xa, xb, pitch, ycoef, cell_h}."""
    thr_val, bw = cv2.threshold(gray_img, 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if thr_val < 100:  # dark scene: Otsu may split screen glow; clamp up
        _, bw = cv2.threshold(gray_img, 140, 255, cv2.THRESH_BINARY)
    n, _, stats, cents = cv2.connectedComponentsWithStats(bw, connectivity=8)
    blobs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 4 <= w <= 200 and 10 <= h <= 100 and area >= 40:
            blobs.append((cents[i][0], cents[i][1], w, h))
    if len(blobs) < 6:
        return None
    # densest y-band = the strip (bar/text/cursor live elsewhere)
    best = None
    for _, cy0, _, h0 in blobs:
        band = [b for b in blobs if abs(b[1] - cy0) <= max(30, h0)]
        if best is None or len(band) > len(best):
            best = band
    if best is None or len(best) < 6:
        return None
    band = sorted(best, key=lambda b: b[0])
    heights = sorted(b[3] for b in band)
    med_h = heights[len(heights) // 2]
    band = [b for b in band if b[3] >= 0.55 * med_h]  # drop text glyphs
    if len(band) < 6:
        return None
    if band[-1][0] - band[0][0] < N_CELLS * 4:
        return None
    # Outermost blobs are the always-lit single cells u=0 / u=39 (their inner
    # neighbours are 0-bits by pattern design) -> exact strip extent.
    approx_pitch = (band[-1][0] - band[0][0]) / (N_CELLS - 1)
    xs = np.array([b[0] for b in band])
    ys = np.array([b[1] for b in band])
    ycoef = np.polyfit(xs, ys, 2 if len(band) >= 5 else 1)
    return {"xa": float(band[0][0]), "xb": float(band[-1][0]),
            "pitch": float(approx_pitch),
            "ycoef": ycoef.tolist(), "cell_h": float(med_h)}


def _profile(gray_img: np.ndarray, loc: dict):
    """Mean-intensity profile along the (curved) strip line."""
    h, w = gray_img.shape
    half_h = max(3, int(loc["cell_h"] * 0.30))
    x0 = max(0, int(loc["xa"] - 1.5 * loc["pitch"]))
    x1 = min(w - 1, int(loc["xb"] + 1.5 * loc["pitch"]))
    xs = np.arange(x0, x1 + 1)
    cys = np.polyval(np.array(loc["ycoef"]), xs).astype(int)
    prof = np.empty(len(xs), dtype=np.float32)
    for j in range(len(xs)):
        y0, y1 = max(0, cys[j] - half_h), min(h, cys[j] + half_h + 1)
        prof[j] = gray_img[y0:y1, xs[j]].mean()
    return prof


def _decode_runs(runs: list[tuple[int, float]]):
    """Sequential decode with per-run pitch adaptation. Runs are 1..3 cells by
    construction, so calibration never starves. Returns frame idx or None."""
    if len(runs) < 5 or runs[0][0] != 1 or runs[-1][0] != 1:
        return None
    # seed the local pitch from the start marker itself: its first run is
    # exactly 3 cells by construction (global mean mis-seeds under perspective)
    pitch = runs[0][1] / 3.0
    cells: list[int] = []
    for val, length in runs:
        n = min(3, max(1, round(length / pitch)))
        cells.extend([int(val)] * n)
        pitch = 0.7 * pitch + 0.3 * (length / n)
        if len(cells) > N_CELLS:
            return None
    if len(cells) != N_CELLS:
        return None
    if (cells[:len(START_MARKER)] != START_MARKER
            or cells[-len(END_MARKER):] != END_MARKER):
        return None
    data = cells[len(START_MARKER):len(START_MARKER) + DATA_CELLS]
    g = 0
    for j in range(0, DATA_CELLS, 2):
        pair = (data[j], data[j + 1])
        if pair == (1, 0):
            g = (g << 1) | 1
        elif pair == (0, 1):
            g = g << 1
        else:  # Manchester violation -> mis-allocation, reject frame
            return None
    return from_gray(g)


def decode_frame(img: np.ndarray, loc: dict | None = None):
    img = _downscale(img)
    gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if float(gray_img.mean()) > 200:
        return {"flash": 1, "idx": None, "loc": loc}
    if loc is None:
        loc = find_strip(gray_img)
        if loc is None:
            return {"flash": 0, "idx": None, "loc": None}
    prof = _profile(gray_img, loc)
    lo = float(np.percentile(prof, 10))
    hi = float(np.percentile(prof, 90))
    if hi - lo < 30:
        return {"flash": 0, "idx": None, "loc": None}
    win = max(9, int(3 * loc["pitch"]))
    pad = win // 2
    padded = np.pad(prof, pad, mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, win)[:len(prof)]
    thr = np.maximum((view.min(axis=1) + view.max(axis=1)) / 2,
                     lo + 0.30 * (hi - lo))
    binary = (prof > thr).astype(np.int8)
    on = np.flatnonzero(binary)
    if len(on) == 0:
        return {"flash": 0, "idx": None, "loc": None}
    binary = binary[on[0]:on[-1] + 1]
    runs = []
    val, start = int(binary[0]), 0
    for j in range(1, len(binary)):
        if binary[j] != val:
            runs.append((val, float(j - start)))
            val, start = int(binary[j]), j
    runs.append((val, float(len(binary) - start)))
    # merge sub-cell cosmetic gaps/nicks into neighbours
    clean: list[list] = []
    i = 0
    while i < len(runs):
        val, length = runs[i]
        if length < 0.45 * loc["pitch"] and clean and i + 1 < len(runs):
            clean[-1][1] += length + runs[i + 1][1]
            i += 2
            continue
        if clean and clean[-1][0] == val:
            clean[-1][1] += length
        else:
            clean.append([val, length])
        i += 1
    idx = _decode_runs([(v, l) for v, l in clean])
    if idx is None:
        return {"flash": 0, "idx": None, "loc": None}
    return {"flash": 0, "idx": idx, "loc": loc}


def run_selftest(d: Path) -> int:
    truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
    ok = bad = 0
    loc = None
    failures = []
    for rec in truth:
        img = cv2.imread(str(d / f"st_{rec['i']:06d}.png"))
        out = decode_frame(img, loc)
        loc = out["loc"] or loc
        if out["flash"] != rec["flash"]:
            bad += 1; failures.append((rec["i"], "flash"))
        elif not rec["flash"] and out["idx"] != rec["i"]:
            bad += 1; failures.append((rec["i"], "idx", out["idx"]))
        else:
            ok += 1
    rate = ok / len(truth) * 100
    print(f"selftest: {ok}/{len(truth)} decoded correctly ({rate:.2f}%)")
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
