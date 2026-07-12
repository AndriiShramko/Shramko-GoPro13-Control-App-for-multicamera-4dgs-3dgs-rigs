"""Валидная матрица режимов HERO13: перебор resolution x fps по HTTP (200/403).

Операционный профиль Андрия: 8:7 (максимум информации для 4DGS), чаще 60fps.
Сканируем целево: framing 8:7 (232=3) и 16:9 (232=1) x разрешения x fps.
Камера отвечает 200 (принято) / 403 (невалидная комбинация в текущем состоянии).

Usage: python src/scan_combos.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from wired_gopro import WiredGoPro, discover_camera_ips  # noqa: E402

RES = {1: "4K", 4: "2.7K", 9: "1080", 100: "5.3K",
       18: "4K 4:3", 27: "5.3K 4:3", 107: "5.3K 8:7 V2", 108: "4K 8:7 V2"}
FPS = {0: 240, 1: 120, 2: 100, 5: 60, 6: 50, 8: 30, 9: 25, 10: 24}
FRAMING = {1: "16:9", 3: "8:7", 0: "4:3"}


def main():
    ips = discover_camera_ips()
    if not ips:
        sys.exit("camera not found")
    cam = WiredGoPro(ips[0], REPO / "docs" / "session-logs" /
                     f"combo-scan-{dt.date.today():%Y%m%d}.jsonl")
    cam.enable_wired_control()
    cam.start_keep_alive()
    snapshot = cam.state()["settings"]
    results = []
    for fr_id, fr_name in FRAMING.items():
        r = cam.set_setting(232, fr_id)
        time.sleep(0.6)
        if r.status_code != 200:
            results.append({"framing": fr_name, "framing_code": r.status_code})
            continue
        for res_id, res_name in RES.items():
            r1 = cam.set_setting(2, res_id)
            time.sleep(0.5)
            if r1.status_code != 200:
                results.append({"framing": fr_name, "res": res_name,
                                "res_code": r1.status_code})
                continue
            for fps_id, fps_val in FPS.items():
                r2 = cam.set_setting(234, fps_id)
                time.sleep(0.4)
                ok = r2.status_code == 200
                results.append({"framing": fr_name, "res": res_name,
                                "fps": fps_val, "ok": ok})
                if ok:
                    print(f"OK   {fr_name:5} {res_name:11} {fps_val}fps")
    cam.stop_keep_alive()
    out = (REPO / "docs" / "experiments" /
           f"combo-matrix-{dt.datetime.now():%Y%m%d_%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"snapshot_before": snapshot,
                               "results": results}, indent=2), encoding="utf-8")
    valid = [r for r in results if r.get("ok")]
    print(f"\nвалидных комбо: {len(valid)} -> {out}")
    print("8:7 варианты:", [f"{r['res']}@{r['fps']}" for r in valid
                            if r["framing"] == "8:7"])


if __name__ == "__main__":
    main()
