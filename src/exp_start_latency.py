"""E1 — start-latency jitter (DONE#5).

Setup: camera aimed at the monitor running `stand.py counter` (operator aims once).
For each of N takes: log t_cmd (perf_ns) -> shutter/start -> ~2 s -> stop ->
download -> decode first readable stand index from the clip -> map that index to
its display time via the stand JSONL -> offset_i = t_display(first content) - t_cmd.

Reported values are RELATIVE (contain a constant unknown display latency, DWM +
panel lag); the deliverable is the DISTRIBUTION (jitter): mean/sigma/min/max +
histogram (spec-experiments E1, red-team #10).

Usage (stand must already be running in another process):
    python src/exp_start_latency.py --n 30 --stand-log docs/session-logs/stand-counter-*.jsonl
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from wired_gopro import WiredGoPro, discover_camera_ips  # noqa: E402
from decode_stand import decode_frame  # noqa: E402

import cv2  # noqa: E402


def load_stand_index(stand_log: Path) -> dict[int, int]:
    """stand frame idx -> perf_ns at flip (before-flip timestamp)."""
    mapping = {}
    with stand_log.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if "i" in rec:
                mapping[rec["i"]] = rec["b"]
    return mapping


def first_decoded_index(video: Path, max_frames: int = 120):
    cap = cv2.VideoCapture(str(video))
    loc = None
    n = 0
    try:
        while n < max_frames:
            ret, img = cap.read()
            if not ret:
                break
            out = decode_frame(img, loc)
            loc = out["loc"] or loc
            if out["idx"] is not None:
                return n, out["idx"]
            n += 1
    finally:
        cap.release()
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--pause", type=float, default=10.0)
    ap.add_argument("--stand-log", required=True)
    args = ap.parse_args()

    stand_logs = sorted(glob.glob(args.stand_log))
    if not stand_logs:
        sys.exit("stand log not found — start src/stand.py counter first")
    stand_log = Path(stand_logs[-1])

    ips = discover_camera_ips()
    if not ips:
        sys.exit("camera not found")
    cam = WiredGoPro(ips[0], REPO / "docs" / "session-logs" /
                     f"exp01-{dt.date.today():%Y%m%d}.jsonl")
    cam.enable_wired_control()
    cam.start_keep_alive()

    take_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_exp01-start-latency"
    take_dir.mkdir(parents=True)
    (take_dir / "meta.json").write_text(json.dumps({
        "experiment": "exp01-start-latency", "n": args.n,
        "camera_info": cam.info(), "state_snapshot": cam.state(),
        "stand_log": str(stand_log)}, indent=2), encoding="utf-8")

    rows = []
    for i in range(args.n):
        cam.require_cool_and_idle()
        t_cmd = time.perf_counter_ns()
        cam.shutter_start()
        time.sleep(args.seconds)
        cam.shutter_stop()
        for _ in range(40):
            if not cam.flags()["encoding"]:
                break
            time.sleep(0.25)
        last = cam.last_captured()
        dest = take_dir / f"take{i:03d}_{last['file']}"
        cam.download(last["folder"], last["file"], dest)
        cam.delete_file(last["folder"], last["file"])  # keep SD lean (post-download)
        vf, stand_idx = first_decoded_index(dest)
        rows.append({"take": i, "t_cmd_perf_ns": t_cmd, "file": dest.name,
                     "first_decoded_video_frame": vf, "stand_idx": stand_idx})
        print(f"take {i}: first decoded video frame={vf} stand_idx={stand_idx}")
        time.sleep(args.pause)
    cam.stop_keep_alive()

    # join with stand flip times
    idx2ns = load_stand_index(stand_log)
    out_dir = REPO / "docs" / "experiments" / "exp01-start-latency"
    out_dir.mkdir(parents=True, exist_ok=True)
    offsets_ms = []
    csv_path = out_dir / f"latency-{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["take", "file", "t_cmd_perf_ns", "stand_idx",
                     "t_display_perf_ns", "offset_ms"])
        for r in rows:
            t_disp = idx2ns.get(r["stand_idx"]) if r["stand_idx"] is not None else None
            off = (t_disp - r["t_cmd_perf_ns"]) / 1e6 if t_disp else None
            if off is not None:
                offsets_ms.append(off)
            wr.writerow([r["take"], r["file"], r["t_cmd_perf_ns"],
                         r["stand_idx"], t_disp, f"{off:.3f}" if off else ""])

    if offsets_ms:
        stats = {"n": len(offsets_ms),
                 "mean_ms": statistics.mean(offsets_ms),
                 "stdev_ms": statistics.stdev(offsets_ms) if len(offsets_ms) > 1 else 0,
                 "min_ms": min(offsets_ms), "max_ms": max(offsets_ms),
                 "note": "RELATIVE offsets (contain constant display latency); "
                         "jitter (stdev/min-max spread) is the deliverable"}
        (out_dir / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            plt.hist(offsets_ms, bins=15)
            plt.xlabel("cmd->first-visible-content offset, ms (relative)")
            plt.ylabel("takes")
            plt.title(f"E1 start latency, n={len(offsets_ms)}")
            plt.savefig(REPO / "docs" / "plots" / "exp01-start-latency-hist.png", dpi=120)
        except Exception as exc:  # plot is a nicety, data is the deliverable
            print("plot skipped:", exc)
        print(json.dumps(stats, indent=2))
    print(f"csv -> {csv_path}")


if __name__ == "__main__":
    main()
