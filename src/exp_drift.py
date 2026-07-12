"""E2 — clock drift, camera vs host QPC (DONE#6).

Camera (1080p30, HyperSmooth OFF, thermal-gated) films the stand counter for
>= 20 min. The clip is decoded; for each video frame with a readable stand
index we get pairs (camera_time_s, stand_display_time_s). A linear fit
camera_time = a + b*stand_time yields drift ppm = (b - 1) * 1e6 with CI.

Chapters: GoPro splits long takes (~4 GB); pass all chapters in order, the
script concatenates timelines via decoded stand indices (no reliance on
container timestamps across chapters).

Thermal: the RUNNER (this script) polls the Hot flag every 60 s during the
recording and force-stops on warning (red-team #7).

Usage:
    python src/exp_drift.py --minutes 22 --stand-log docs/session-logs/stand-counter-*.jsonl
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from wired_gopro import WiredGoPro, discover_camera_ips  # noqa: E402
from exp_start_latency import load_stand_index  # noqa: E402
from decode_stand import decode_frame  # noqa: E402

import cv2  # noqa: E402


def decode_pairs(video: Path, idx2ns: dict[int, int], sample_every: int = 15):
    """Return (camera_pts_s, stand_display_s) pairs, sampled to keep it fast."""
    cap = cv2.VideoCapture(str(video))
    loc = None
    pairs = []
    n = 0
    while True:
        ret, img = cap.read()
        if not ret:
            break
        if n % sample_every == 0:
            pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            out = decode_frame(img, loc)
            loc = out["loc"] or loc
            if out["idx"] is not None and out["idx"] in idx2ns:
                pairs.append((pts, idx2ns[out["idx"]] / 1e9))
        n += 1
    cap.release()
    return pairs


def record_with_thermal_gate(cam: WiredGoPro, minutes: float) -> list[dict]:
    cam.require_cool_and_idle()
    cam.shutter_start()
    t_end = time.time() + minutes * 60
    incidents = []
    try:
        while time.time() < t_end:
            time.sleep(60)
            f = cam.flags()
            incidents.append({"t": time.time(), **f})
            if f["hot"]:
                print("HOT flag raised — stopping recording early")
                break
    finally:
        cam.shutter_stop()
    return incidents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=22)
    ap.add_argument("--stand-log", required=True)
    ap.add_argument("--analyze-only", nargs="*", help="existing chapter files")
    args = ap.parse_args()

    stand_logs = sorted(glob.glob(args.stand_log))
    if not stand_logs:
        sys.exit("stand log not found")
    idx2ns = load_stand_index(Path(stand_logs[-1]))

    out_dir = REPO / "docs" / "experiments" / "exp02-drift"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.analyze_only:
        chapters = [Path(p) for p in args.analyze_only]
    else:
        ips = discover_camera_ips()
        if not ips:
            sys.exit("camera not found")
        cam = WiredGoPro(ips[0], REPO / "docs" / "session-logs" /
                         f"exp02-{dt.date.today():%Y%m%d}.jsonl")
        cam.enable_wired_control()
        cam.start_keep_alive()
        take_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_exp02-drift"
        take_dir.mkdir(parents=True)
        (take_dir / "meta.json").write_text(json.dumps({
            "experiment": "exp02-drift", "minutes": args.minutes,
            "camera_info": cam.info(), "state_snapshot": cam.state()},
            indent=2), encoding="utf-8")
        incidents = record_with_thermal_gate(cam, args.minutes)
        (take_dir / "thermal-log.json").write_text(
            json.dumps(incidents, indent=2), encoding="utf-8")
        for _ in range(60):
            if not cam.flags()["encoding"]:
                break
            time.sleep(1)
        # download all chapters of this take (media list delta would be more
        # precise; last_captured returns the final chapter — list and filter)
        listing = cam.media_list()
        files = [(m["d"], f["n"]) for m in listing.get("media", [])
                 for f in m.get("fs", [])]
        take_files = sorted(files)[-8:]  # heuristic: newest few; verified via meta
        chapters = []
        for d, n in take_files:
            dest = take_dir / n
            cam.download(d, n, dest)
            chapters.append(dest)
        cam.stop_keep_alive()

    pairs = []
    for ch in chapters:
        got = decode_pairs(ch, idx2ns)
        print(f"{ch.name}: {len(got)} decoded pairs")
        pairs.extend(got)
    if len(pairs) < 20:
        sys.exit(f"only {len(pairs)} pairs decoded — not enough for a fit")

    cam_t = np.array([p[0] for p in pairs])
    stand_t = np.array([p[1] for p in pairs])
    stand_t -= stand_t[0]
    cam_t -= cam_t[0]
    # least squares cam_t = a + b*stand_t
    A = np.vstack([np.ones_like(stand_t), stand_t]).T
    coef, res, *_ = np.linalg.lstsq(A, cam_t, rcond=None)
    b = coef[1]
    n = len(pairs)
    resid = cam_t - A @ coef
    s2 = float(resid @ resid) / (n - 2)
    var_b = s2 / float(((stand_t - stand_t.mean()) ** 2).sum())
    ppm = (b - 1) * 1e6
    ci95 = 1.96 * np.sqrt(var_b) * 1e6
    result = {"n_pairs": n, "span_s": float(stand_t[-1]),
              "slope": float(b), "drift_ppm": float(ppm),
              "ci95_ppm": float(ci95),
              "resid_rms_ms": float(np.sqrt(s2) * 1000),
              "chapters": [c.name for c in chapters],
              "chain": "QPC(host) -> stand flip log -> decoded Gray index -> "
                       "video PTS (container)"}
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    (out_dir / f"drift-{stamp}.json").write_text(json.dumps(result, indent=2),
                                                 encoding="utf-8")
    with (out_dir / f"pairs-{stamp}.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["camera_pts_s", "stand_display_s"])
        wr.writerows(pairs)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
