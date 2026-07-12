"""Phase-0 CLI: discover / status / record / cycle / download / meta / sd-inventory.

Usage (from repo root, venv active):
    python src/gpcli.py discover
    python src/gpcli.py status
    python src/gpcli.py record --seconds 5
    python src/gpcli.py cycle --n 10 --seconds 3     # DONE#3: 10 start/stop cycles
    python src/gpcli.py download-last --dest captures/manual
    python src/gpcli.py meta <file.mp4>
    python src/gpcli.py sd-inventory                  # DONE#10: before any cleanup

Every run appends JSONL to docs/session-logs/gpcli-YYYYMMDD.jsonl.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FFPROBE = REPO / "bin" / "ffprobe.exe"  # absolute path only: System32 has a 0-byte decoy
sys.path.insert(0, str(REPO / "src"))

from wired_gopro import WiredGoPro, discover_camera_ips  # noqa: E402


def log_path() -> Path:
    p = REPO / "docs" / "session-logs" / f"gpcli-{dt.date.today():%Y%m%d}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_camera() -> WiredGoPro:
    ips = discover_camera_ips()
    if not ips:
        sys.exit("No GoPro USB adapter found (no 172.2x gateway .51). "
                 "Check driver / GoPro Connect mode; see docs/session-logs/*E0*.md")
    cam = WiredGoPro(ips[0], log_path())
    cam.enable_wired_control()
    return cam


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_discover(_args):
    ips = discover_camera_ips()
    print(json.dumps({"candidates": ips}, indent=2))
    if ips:
        cam = WiredGoPro(ips[0], log_path())
        cam.enable_wired_control()
        info = cam.info()
        print(json.dumps(info, indent=2))


def cmd_status(_args):
    cam = get_camera()
    print(json.dumps({"flags": cam.flags(), "info": cam.info()}, indent=2))


def _record_once(cam: WiredGoPro, seconds: float) -> dict:
    cam.require_cool_and_idle()
    t0 = time.perf_counter_ns()
    r1 = cam.shutter_start()
    time.sleep(seconds)
    r2 = cam.shutter_stop()
    # wait for encoding flag to drop (file finalization)
    for _ in range(40):
        if not cam.flags()["encoding"]:
            break
        time.sleep(0.25)
    last = cam.last_captured()
    return {"start_code": r1.status_code, "stop_code": r2.status_code,
            "t_start_perf_ns": t0, "last_captured": last}


def cmd_record(args):
    cam = get_camera()
    cam.start_keep_alive()
    res = _record_once(cam, args.seconds)
    print(json.dumps(res, indent=2))


def cmd_cycle(args):
    cam = get_camera()
    cam.start_keep_alive()
    results = []
    for i in range(args.n):
        res = _record_once(cam, args.seconds)
        res["cycle"] = i
        results.append(res)
        print(f"cycle {i}: start={res['start_code']} stop={res['stop_code']} "
              f"file={res['last_captured'].get('file')}")
        time.sleep(args.pause)
    out = REPO / "docs" / "session-logs" / f"cycle-{dt.datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    ok = sum(1 for r in results if r["start_code"] == 200 and r["stop_code"] == 200)
    print(f"{ok}/{args.n} cycles OK -> {out}")


def cmd_download_last(args):
    cam = get_camera()
    last = cam.last_captured()
    folder, name = last["folder"], last["file"]
    dest = REPO / args.dest / name
    cam.download(folder, name, dest)
    digest = sha256(dest)
    print(json.dumps({"file": str(dest), "bytes": dest.stat().st_size,
                      "sha256": digest}, indent=2))


def cmd_meta(args):
    src = Path(args.file)
    if not FFPROBE.exists():
        sys.exit(f"ffprobe not found at {FFPROBE} (bin/ bootstrap incomplete)")
    probe = subprocess.run(
        [str(FFPROBE), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(src)],
        capture_output=True, text=True, check=True)
    data = json.loads(probe.stdout)
    summary = {
        "duration": data.get("format", {}).get("duration"),
        "streams": [
            {"codec": s.get("codec_name"), "type": s.get("codec_type"),
             "fps": s.get("r_frame_rate"), "frames": s.get("nb_frames"),
             "handler": (s.get("tags") or {}).get("handler_name")}
            for s in data.get("streams", [])
        ],
    }
    print(json.dumps(summary, indent=2))
    out = src.with_suffix(".ffprobe.json")
    out.write_text(probe.stdout, encoding="utf-8")
    print(f"full ffprobe -> {out}")


def cmd_sd_inventory(_args):
    cam = get_camera()
    listing = cam.media_list()
    out = (REPO / "docs" / "session-logs" /
           f"sd-inventory-{dt.datetime.now():%Y%m%d_%H%M%S}.json")
    out.write_text(json.dumps(listing, indent=2), encoding="utf-8")
    files = [f for m in listing.get("media", []) for f in m.get("fs", [])]
    total = sum(int(f.get("size", 0)) for f in files)
    print(f"{len(files)} files, {total/1e9:.2f} GB -> {out}")


def main():
    ap = argparse.ArgumentParser(prog="gpcli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover").set_defaults(fn=cmd_discover)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("record"); p.add_argument("--seconds", type=float, default=5)
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("cycle"); p.add_argument("--n", type=int, default=10)
    p.add_argument("--seconds", type=float, default=3)
    p.add_argument("--pause", type=float, default=8)
    p.set_defaults(fn=cmd_cycle)
    p = sub.add_parser("download-last"); p.add_argument("--dest", default="captures/manual")
    p.set_defaults(fn=cmd_download_last)
    p = sub.add_parser("meta"); p.add_argument("file")
    p.set_defaults(fn=cmd_meta)
    sub.add_parser("sd-inventory").set_defaults(fn=cmd_sd_inventory)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
