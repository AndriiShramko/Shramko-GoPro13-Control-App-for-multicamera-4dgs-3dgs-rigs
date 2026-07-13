"""Decode stand frames (Gray-code strip) from video/PNG frames.

Locator/decoder v5 — evolution driven by real footage (2026-07-12):
  v1 integer row-scan: broke on fisheye bow + fractional pitch (4/300).
  v2 blob band + linear pitch from outer anchors: perspective drifted a full
     cell mid-strip (lead anchor mismatched while trail matched).
  v3 quadratic x(u) from 1-2-1 anchor blob trios: blob structure differs
     between sharp renders (no merge) and camera blur (merge) — fragile.
  v4 1-param projective grid-search: distortion is S-shaped (fisheye), lead wanted k=-0.25 while trail wanted k=+0.05 — no global fit.
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

DEBUG = {"on": False, "log": []}  # decode_frame_clock failure tracing

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
    # 2048 (not 1280): at 1280 a 4K-frame cell is ~16px and the ~1.4px
    # inter-cell gap fuses under dim locked exposure -> runs merge into
    # multi-cell blobs and Manchester dies (found 2026-07-13).
    if img.shape[1] > 2200:
        s = 2048 / img.shape[1]
        img = cv2.resize(img, (2048, int(img.shape[0] * s)),
                         interpolation=cv2.INTER_AREA)
    return img


def find_strip(gray_img: np.ndarray):
    """Locate the strip band. Returns loc dict {xa, xb, pitch, ycoef, cell_h}."""
    thr_val, bw = cv2.threshold(gray_img, 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # NO upward clamp: with locked exposure (1/240 ISO100) strip cells sit at
    # brightness 65-92, a forced 140 threshold blanks the strip entirely
    # (2026-07-13 — exactly the audit's "fixed clamp is brittle" flaw).
    # Guard only against a near-black empty frame where Otsu splits noise.
    if thr_val < 12:
        return None
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
    band = [b for b in band if b[3] >= 0.55 * med_h]
    if len(band) < 6:
        return None
    xs = np.array([b[0] for b in band])
    ys = np.array([b[1] for b in band])
    ycoef = np.polyfit(xs, ys, 2 if len(band) >= 5 else 1)
    # Second pass along the FITTED CURVE: the first horizontal window both
    # truncates a bowed strip at its far end and admits the counter text
    # (drift-frame 2026-07-12: text glyphs h16 vs cells h30 poisoned the band,
    # xb landed mid-strip). Re-collect against the curve, reject short blobs.
    med_h2 = med_h
    for _ in range(2):
        band2 = [b for b in blobs
                 if abs(b[1] - float(np.polyval(ycoef, b[0]))) <= 0.9 * med_h2
                 and b[3] >= 0.7 * med_h2]
        if len(band2) < 6:
            return None
        band2 = sorted(band2, key=lambda b: b[0])
        hs = sorted(b[3] for b in band2)
        med_h2 = hs[len(hs) // 2]
        xs = np.array([b[0] for b in band2])
        ys = np.array([b[1] for b in band2])
        ycoef = np.polyfit(xs, ys, 2 if len(band2) >= 5 else 1)
    band = band2
    if band[-1][0] - band[0][0] < N_CELLS * 4:
        return None
    # Outermost blobs are the markers (always lit) -> strip extent.
    approx_pitch = (band[-1][0] - band[0][0]) / (N_CELLS - 1)
    return {"xa": float(band[0][0]), "xb": float(band[-1][0]),
            "pitch": float(approx_pitch),
            "ycoef": ycoef.tolist(), "cell_h": float(med_h2)}


def _profile(gray_img: np.ndarray, loc: dict):
    """Mean-intensity profile along the (curved) strip line."""
    h, w = gray_img.shape
    half_h = max(3, int(loc["cell_h"] * 0.30))
    # FULL image width: blob extremes lied about strip extent (right cells
    # fused into one 111px blob) — first/last bright profile sample defines
    # the extent; Manchester validation rejects stray bright objects
    xs = np.arange(0, w)
    cys = np.polyval(np.array(loc["ycoef"]), xs).astype(int)
    prof = np.empty(len(xs), dtype=np.float32)
    for j in range(len(xs)):
        y0, y1 = max(0, cys[j] - half_h), min(h, cys[j] + half_h + 1)
        prof[j] = gray_img[y0:y1, xs[j]].mean()
    return prof


def _decode_runs(runs: list[tuple[int, float]]):
    """Sequential decode with per-run pitch adaptation. Runs are 1..3 cells by
    construction, so calibration never starves. Returns frame idx or None."""
    if len(runs) < 5 or runs[0][0] != 1:
        return None  # end is validated by END_MARKER, not by the last run
    # seed the local pitch from the start marker itself: its first run is
    # exactly 3 cells by construction (global mean mis-seeds under perspective)
    pitch = runs[0][1] / 3.0
    cells: list[int] = []
    for val, length in runs:
        n = min(3, max(1, round(length / pitch)))
        cells.extend([int(val)] * n)
        pitch = 0.7 * pitch + 0.3 * (length / n)
        if len(cells) >= N_CELLS:
            break  # trailing runs may be scenery beyond the strip — ignored
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


def decode_frame_clock(img: np.ndarray, loc: dict | None = None):
    """v6: clock-row grid decode. The stand draws a 1010... clock strip under
    the data strip; the local cell grid is read from clock blob centers, so
    arbitrary smooth fisheye stretch (pitch 0.5-1.3x along the strip) is
    calibrated out per frame. Data cells are then SAMPLED at grid centers —
    no run-length logic, no uniform-pitch assumption."""
    img = _downscale(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if float(gray.mean()) > 200:
        return {"flash": 1, "idx": None, "loc": loc}
    # range normalization: OpenCV decodes GoPro HEVC limited-range WITHOUT
    # expansion (frames come out ~2x darker than ffmpeg's PNG export) and
    # every fixed threshold slides (2026-07-13). Stretch p99.9 -> 255.
    hi_p = float(np.percentile(gray, 99.9))
    if hi_p > 5:
        gray = cv2.convertScaleAbs(gray, alpha=min(8.0, 250.0 / hi_p))
    thr_val, bw = cv2.threshold(gray, 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if thr_val < 12:
        return {"flash": 0, "idx": None, "loc": None}
    # data row via the proven dense-band locator; clock row lives a fixed
    # offset below it along the same curve (global 2-cluster split drowned in
    # scene clutter: Otsu at 25 admits 100+ stray blobs, 2026-07-13)
    dloc = find_strip(gray)
    if dloc is None:
        return {"flash": 0, "idx": None, "loc": None}
    # find_strip's dense band swallows BOTH rows: its curve runs between them
    # (rows are ~1.2*cell_h apart in-frame). Split blobs by dy sign.
    ycoef_mix = np.array(dloc["ycoef"])
    cell_h = float(dloc["cell_h"])
    n, _, stats, cents = cv2.connectedComponentsWithStats(bw, connectivity=8)
    upper, lower = [], []   # (cx, cy, w)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if not (4 <= w <= 260 and 8 <= h <= 140 and area >= 30):
            continue
        cx, cy = float(cents[i][0]), float(cents[i][1])
        dy = (cy - float(np.polyval(ycoef_mix, cx))) / cell_h
        # find_strip now locks onto ONE row (rows are 1.8*cell_h apart, beyond
        # its band window): that row sits at dy~0, the other at dy~±1.8.
        if -0.6 <= dy <= 0.6:
            upper.append((cx, cy, float(w)))   # the row find_strip found
        elif 1.1 <= dy <= 2.6 or -2.6 <= dy <= -1.1:
            lower.append((cx, cy, float(w)))   # the companion row
    if len(upper) < 5 or len(lower) < 5:
        return {"flash": 0, "idx": None, "loc": None}
    # NO heuristics for which row is which: Manchester-with-gaps data is
    # itself near-periodic (pitch 2 cells), so smoothness/markerness metrics
    # both misfire. Try BOTH assignments x grid shifts; markers + Manchester
    # are the validator (2026-07-13).
    # With drawn gaps every LIT cell is its own blob: bits come from blob
    # POSITIONS snapped to the clock grid — no intensity sampling, no
    # polynomial grid (a glare hole made the cubic fit slide off-cell).
    def heal(cxs0, kadj):
        """Fill clock holes / drop split-blob junk against the GLOBAL median
        step M (a running prev_gap cascaded after a midpoint merge and ate
        the grid down to 2 nodes, 2026-07-13). kadj tweaks the node count in
        the biggest hole (fisheye stretches it, round() can be off by one)."""
        gaps = np.diff(cxs0)
        if len(gaps) < 3:
            return list(cxs0)
        M = float(np.median(gaps))
        if M <= 1:
            return list(cxs0)
        big = int(np.argmax(gaps)) + 1
        out = [cxs0[0]]
        for j in range(1, len(cxs0)):
            gap = cxs0[j] - out[-1]
            k = int(round(gap / M))
            if j == big and 2 <= k <= 5:
                k = max(1, k + kadj)
            if k <= 0:
                continue  # split half / junk closer than 0.5*M: drop
            if 2 <= k <= 6:
                for m in range(1, k):
                    out.append(out[-1] + gap / k)
            out.append(cxs0[j])
        return out

    for data_c, clock_c in ((upper, lower), (lower, upper)):
        if len(clock_c) < 15 or len(data_c) < 5:
            continue
        # clock blobs are uniform-width cells; range-normalization amplifies
        # noise into small junk blobs that bend heal() (2026-07-13) — drop
        # anything much narrower than the cluster median
        cw = sorted(p[2] for p in clock_c)
        med_w = cw[len(cw) // 2]
        clock_f = [p for p in clock_c if p[2] >= 0.55 * med_w]
        if len(clock_f) < 15:
            continue
        cxs0 = sorted(p[0] for p in clock_f)
        variants = []
        for kadj in (0, 1, -1):
            h = heal(cxs0, kadj)
            if 25 <= len(h) <= 30 and h not in variants:
                variants.append(h)
        if not variants:
            continue
        ycoef_d = np.polyfit([p[0] for p in data_c],
                             [p[1] for p in data_c], 2)
        for healed in variants:
            cxs = np.array(healed, dtype=float)
            yield_result = yield_from_grid(cxs, gray, ycoef_d, cell_h)
            if yield_result is not None:
                return {"flash": 0, "idx": yield_result, "loc": loc}
    return {"flash": 0, "idx": None, "loc": None}


def yield_from_grid(cxs, gray, ycoef_d, cell_h):
    """Sample DATA-row INTENSITY at clock-grid nodes (piecewise-linear x(pos),
    edge-extrapolated), try grid shifts; return frame idx or None.
    Intensity sampling is immune to data-blob splits/merges — only the clock
    row needs blob integrity, and heal() covers that (2026-07-13)."""
    h_img, w_img = gray.shape
    half = max(2, int(cell_h * 0.3))
    # edge extension: np.interp clamps beyond the last clock blob, data cell
    # 55 sits right of clock cell 54 (stable '0110' tail before this fix)
    step_l = cxs[1] - cxs[0]
    step_r = cxs[-1] - cxs[-2]
    cxs_ext = np.concatenate(([cxs[0] - step_l], cxs, [cxs[-1] + step_r]))
    for s in (0, 2, -2, 1, -1, 3, -3, 4, -4):
        node_pos = np.arange(len(cxs)) * 2.0 + s   # clock cells are even
        if node_pos[0] < -2 or node_pos[-1] > 58:
            continue
        node_ext = np.concatenate(([node_pos[0] - 2], node_pos,
                                   [node_pos[-1] + 2]))
        xs56 = np.interp(np.arange(56, dtype=float), node_ext, cxs_ext)
        if xs56.min() < 2 or xs56.max() > w_img - 3:
            continue
        pitch_loc = np.gradient(xs56)
        vals = np.empty(56, np.float32)
        ok = True
        for c in range(56):
            xc = int(round(xs56[c]))
            yc = int(round(float(np.polyval(ycoef_d, xs56[c]))))
            dx = max(1, int(pitch_loc[c] * 0.2))
            x0, x1 = max(0, xc - dx), min(w_img, xc + dx + 1)
            y0, y1 = max(0, yc - half), min(h_img, yc + half + 1)
            if x1 <= x0 or y1 <= y0:
                ok = False
                break
            vals[c] = gray[y0:y1, x0:x1].mean()
        if not ok:
            continue
        lo, hi = float(vals.min()), float(vals.max())
        if hi - lo < 25:
            continue
        # LOCAL threshold: fisheye vignetting dims edge cells (markers!) to
        # 38-44 vs global mid 46 — the last 5-bit gap to a perfect decode
        # (2026-07-13). Rolling min/max over ±6 cells adapts the cut.
        win = 13
        pad = win // 2
        vpad = np.pad(vals, pad, mode="edge")
        view = np.lib.stride_tricks.sliding_window_view(vpad, win)[:56]
        thr56 = (view.min(axis=1) + view.max(axis=1)) / 2
        # cells in a locally flat (all-dark/all-lit) stretch: fall back to
        # global mid so a run of zeros doesn't split on noise
        flat = (view.max(axis=1) - view.min(axis=1)) < 20
        thr56[flat] = (lo + hi) / 2
        bits = (vals > thr56).astype(int).tolist()
        if DEBUG["on"]:
            DEBUG["log"].append(
                f"s={s} n_nodes={len(cxs)} bits={''.join(map(str, bits))}")
        if (bits[:len(START_MARKER)] != START_MARKER
                or bits[-len(END_MARKER):] != END_MARKER):
            continue
        data = bits[len(START_MARKER):len(START_MARKER) + DATA_CELLS]
        g = 0
        bad = False
        for j in range(0, DATA_CELLS, 2):
            pair = (data[j], data[j + 1])
            if pair == (1, 0):
                g = (g << 1) | 1
            elif pair == (0, 1):
                g = g << 1
            else:
                bad = True
                break
        if bad:
            continue
        return from_gray(g)
    return None


def decode_frame(img: np.ndarray, loc: dict | None = None):
    out = decode_frame_clock(img, loc)
    if out["idx"] is not None or out["flash"]:
        return out
    return _decode_frame_runs(img, loc)


def _decode_frame_runs(img: np.ndarray, loc: dict | None = None):
    img = _downscale(img)
    gray_img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if float(gray_img.mean()) > 200:
        return {"flash": 1, "idx": None, "loc": loc}
    if loc is None:
        loc = find_strip(gray_img)
        if loc is None:
            return {"flash": 0, "idx": None, "loc": None}
    prof = _profile(gray_img, loc)
    # Smooth BEFORE thresholding: narrow inter-cell gaps (~2.5px) dip only to
    # mid-amplitude and sit exactly at the adaptive threshold -> run chatter
    # (diag 2026-07-13). A Gaussian ~pitch/3 erases gaps and moire while
    # 26px-wide cells survive untouched.
    pitch0 = max(6.0, (loc.get("xb", 0) - loc.get("xa", 0)) / (N_CELLS - 1))
    k = int(pitch0 / 3) | 1
    if k >= 3:
        prof = cv2.GaussianBlur(prof.reshape(1, -1).astype(np.float32),
                                (k, 1), 0).ravel()
    lo = float(np.percentile(prof, 10))
    hi = float(np.percentile(prof, 90))
    if hi - lo < 30:
        return {"flash": 0, "idx": None, "loc": None}
    win = max(9, int(3 * loc["cell_h"]))  # cells ~square; blob pitch unreliable
    pad = win // 2
    padded = np.pad(prof, pad, mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(padded, win)[:len(prof)]
    thr = np.maximum((view.min(axis=1) + view.max(axis=1)) / 2,
                     lo + 0.30 * (hi - lo))
    binary = (prof > thr).astype(np.int8)
    runs = []
    val, start = int(binary[0]), 0
    for j in range(1, len(binary)):
        if binary[j] != val:
            runs.append((val, float(j - start)))
            val, start = int(binary[j]), j
    runs.append((val, float(len(binary) - start)))
    pitch_est = (loc.get("xb", 0) - loc.get("xa", 0)) / (N_CELLS - 1) or loc["cell_h"]
    loc = dict(loc, pitch=max(loc["cell_h"] * 0.4, pitch_est))
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
    # frame edges can be bright scenery (wall/door beyond the monitor), so no
    # global extent trim: try every bright run as the start marker; the decoder
    # itself validates the stop (exactly N_CELLS + both markers + Manchester)
    cruns = [(v, l) for v, l in clean]
    for i, (v, _) in enumerate(cruns):
        if v != 1:
            continue
        idx = _decode_runs(cruns[i:])
        if idx is not None:
            return {"flash": 0, "idx": idx, "loc": loc}
    return {"flash": 0, "idx": None, "loc": None}


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
