"""E7 — sub-frame phase via monitor-scanout seam (phase-control-research.md).

The stand's `rows` mode shows the same Manchester strip at 5 screen heights.
The monitor repaints top-to-bottom within one refresh (~6 ms), so a camera
frame exposed across the repaint shows a SEAM: rows above it carry index N,
rows below carry N-1. The seam's vertical position localizes the exposure
moment INSIDE the refresh interval -> sub-frame phase of the camera's frame
grid relative to the stand clock.

decode_rows(): locate each row band independently (blob y-clustering), decode
each with the shared Manchester machinery.

E7a: N short takes WITHOUT power-cycle -> per-take phase. If "record start
does not reset sensor phase" holds, phases align on one lattice (mod frame
period, drift-corrected).
E7b (operator power-cycles between takes) and E7c (mode switches) reuse the
same runner with --tag.

Usage (stand `rows` mode must be running):
    python src/exp_phase.py --takes 10 --seconds 2 --tag nocycle
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from wired_gopro import WiredGoPro, discover_camera_ips  # noqa: E402
import decode_stand as D  # noqa: E402


def find_row_bands(gray_img: np.ndarray):
    """Cluster bright blobs into horizontal bands (one per strip row)."""
    thr_val, bw = cv2.threshold(gray_img, 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if thr_val < 100:
        _, bw = cv2.threshold(gray_img, 140, 255, cv2.THRESH_BINARY)
    n, _, stats, cents = cv2.connectedComponentsWithStats(bw, connectivity=8)
    blobs = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 4 <= w <= 200 and 8 <= h <= 100 and area >= 30:
            blobs.append((cents[i][0], cents[i][1], w, h))
    bands = []
    used = [False] * len(blobs)
    order = sorted(range(len(blobs)), key=lambda i: blobs[i][1])
    for i in order:
        if used[i]:
            continue
        cy0, h0 = blobs[i][1], blobs[i][3]
        members = [j for j in range(len(blobs))
                   if not used[j] and abs(blobs[j][1] - cy0) <= max(20, h0)]
        if len(members) >= 6:
            for j in members:
                used[j] = True
            band = sorted((blobs[j] for j in members), key=lambda b: b[0])
            bands.append(band)
    return bands


def decode_rows(img: np.ndarray):
    """Return list of (row_center_y, idx|None) top-to-bottom."""
    img = D._downscale(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    out = []
    for band in find_row_bands(gray):
        hs = sorted(b[3] for b in band)
        med_h = hs[len(hs) // 2]
        band = [b for b in band if b[3] >= 0.7 * med_h]
        if len(band) < 6:
            continue
        xs = np.array([b[0] for b in band])
        ys = np.array([b[1] for b in band])
        ycoef = np.polyfit(xs, ys, 2 if len(band) >= 5 else 1)
        loc = {"xa": float(band[0][0]), "xb": float(band[-1][0]),
               "pitch": (float(band[-1][0]) - float(band[0][0])) / (D.N_CELLS - 1),
               "ycoef": ycoef.tolist(), "cell_h": float(med_h)}
        res = D.decode_frame(gray, loc)
        out.append((float(np.mean(ys)), res["idx"]))
    return sorted(out)


def analyze_take(video: Path, max_frames: int = 90):
    """Per-frame row indices; returns seam observations."""
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS)
    seams = []
    frames = []
    n = 0
    while n < max_frames:
        ret, img = cap.read()
        if not ret:
            break
        rows = decode_rows(img)
        idxs = [i for _, i in rows]
        frames.append({"frame": n, "rows": idxs})
        good = [i for i in idxs if i is not None]
        if len(good) >= 3 and len(set(good)) == 2:
            hi, lo = max(set(good)), min(set(good))
            if hi - lo == 1:
                # seam between last row showing hi and first showing lo
                pos = None
                for k in range(len(idxs) - 1):
                    if idxs[k] == hi and idxs[k + 1] == lo:
                        pos = k + 0.5
                        break
                if pos is not None:
                    seams.append({"frame": n, "hi": hi, "lo": lo,
                                  "seam_row": pos, "n_rows": len(idxs)})
        n += 1
    cap.release()
    return fps, frames, seams


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=2)
    ap.add_argument("--pause", type=float, default=5)
    ap.add_argument("--tag", default="nocycle")
    ap.add_argument("--analyze-only", nargs="*")
    args = ap.parse_args()

    out_dir = REPO / "docs" / "experiments" / "exp07-phase"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.analyze_only:
        clips = [Path(c) for c in args.analyze_only]
    else:
        ips = discover_camera_ips()
        if not ips:
            sys.exit("camera not found")
        cam = WiredGoPro(ips[0], REPO / "docs" / "session-logs" /
                         f"exp07-{dt.date.today():%Y%m%d}.jsonl")
        cam.enable_wired_control()
        cam.start_keep_alive()
        take_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_exp07-{args.tag}"
        take_dir.mkdir(parents=True)
        (take_dir / "meta.json").write_text(json.dumps(
            {"experiment": "exp07-phase", "tag": args.tag,
             "camera_info": cam.info(), "state": cam.state()}, indent=2),
            encoding="utf-8")
        clips = []
        for i in range(args.takes):
            cam.require_cool_and_idle()
            cam.shutter_start()
            time.sleep(args.seconds)
            cam.shutter_stop()
            for _ in range(40):
                if not cam.flags()["encoding"]:
                    break
                time.sleep(0.25)
            last = cam.last_captured()
            dest = take_dir / f"t{i:02d}_{last['file']}"
            cam.download(last["folder"], last["file"], dest)
            cam.delete_file(last["folder"], last["file"])
            clips.append(dest)
            print(f"take {i}: {dest.name}")
            time.sleep(args.pause)
        cam.stop_keep_alive()

    report = {"tag": args.tag, "takes": []}
    for clip in clips:
        fps, frames, seams = analyze_take(clip)
        dec = sum(1 for f in frames for i in f["rows"] if i is not None)
        tot = sum(len(f["rows"]) for f in frames) or 1
        report["takes"].append({
            "clip": clip.name, "fps": fps,
            "row_decode_rate": round(dec / tot, 3),
            "n_seams": len(seams),
            "seams": seams[:20],
        })
        print(f"{clip.name}: rows decoded {dec}/{tot}, seams={len(seams)}")
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = out_dir / f"phase-{args.tag}-{stamp}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
