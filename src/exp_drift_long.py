"""E19 — ОПОРНЫЙ замер дрейфа: длинная запись 30-60 мин + посегментный дрейф.

Ответ на критику: короткий монитор-метод не разрешает дрейф ниже ~3-10 ppm.
Длинная база (30-60 мин) поднимает SNR наклона; посегментный дрейф (окна по
5 мин) показывает, ЛИНЕЕН ли дрейф или плывёт с прогревом (термотранзиент —
ключевая гипотеза, почему E2 14.6 vs E15 -46 разошлись).

Термо-безопасность: пишем в 1080p60 (не 4K8:7) — дрейф это КВАРЦ, от режима
не зависит, но 1080p не перегреет камеру за 40 мин и декодится так же
(стенд всё равно даунскейлится).

GPS не ловит в помещении → монитор с длинной базой + сегменты = лучшее
доступное без аппаратных LED-часов. Стенд валидируется PACING_GATE.

Запуск: стенд counter крутится.
  python src/exp_drift_long.py --minutes 40
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import cv2
import numpy as np

from wired_gopro import WiredGoPro, discover_camera_ips
from exp_phase_determinism import load_stand
from exp_drift_clean import robust_fit
from decode_stand import decode_frame

FFPROBE = str(REPO / "bin" / "ffprobe.exe")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=40.0)
    ap.add_argument("--sample_every", type=int, default=45)
    args = ap.parse_args()

    ips = discover_camera_ips()
    cam = WiredGoPro(ips[0])
    cam.enable_wired_control()
    cam.start_keep_alive()

    # термо-безопасный режим: 1080p60 (res 2=9? ставим через preset video + res)
    cam.get("/gopro/camera/presets/set_group?id=1000", timeout=12); time.sleep(2)
    cam.get("/gopro/camera/presets/load?id=0", timeout=12); time.sleep(2)
    # 1080p = setting 2 option 9 (HERO13); fps 60 = 234=5 / 3=5
    for sid, opt in ((2, 9), (234, 5), (3, 5)):
        cam.set_setting(int(sid), opt); time.sleep(1.2)
    st = cam.state()["settings"]
    print(f"mode: res2={st.get('2')} fps234={st.get('234')} (термо-безопасный)")

    out_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_E19-drift-long"
    out_dir.mkdir(parents=True)

    cam.wait_idle()
    t_cmd = time.perf_counter_ns()
    cam.shutter_start()
    t0 = time.perf_counter_ns()
    while time.perf_counter_ns() - t0 < 12e9:
        if cam.flags()["encoding"]:
            break
        time.sleep(0.05)
    print("recording... (tolerant poll, thermal-stop watch)")
    t_rec = time.perf_counter()
    stopped_early = None
    while time.perf_counter() - t_rec < args.minutes * 60:
        time.sleep(20)
        try:
            if not cam.flags()["encoding"]:
                stopped_early = round(time.perf_counter() - t_rec, 1)
                print(f"!! encoding stopped by itself at {stopped_early}s "
                      f"(термо/батарея)")
                break
        except Exception as exc:
            print(f"  poll err {round(time.perf_counter()-t_rec)}s: {type(exc).__name__} (ok)")
    for _ in range(10):
        try:
            cam.shutter_stop(); break
        except Exception:
            time.sleep(1.5)
    cam.wait_idle()
    last = cam.last_captured()
    dest = out_dir / f"{last['file']}"
    cam.download(last["folder"], last["file"], dest)
    cam.delete_file(last["folder"], last["file"])
    cam.stop_keep_alive()

    dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                                "format=duration", "-of", "csv=p=0", str(dest)],
                               capture_output=True, text=True).stdout.strip() or 0)
    print(f"clip: {dest.name}, длительность {dur:.1f}с")

    logs = sorted([f for f in glob.glob(str(
        REPO / "docs" / "session-logs" / "stand-counter-*.jsonl"))
        if "exp" not in Path(f).name])
    stand_log = Path(logs[-1])
    try:
        idx2ns, _, _ = load_stand(stand_log)
        print(f"стенд {stand_log.name}: PASS, {len(idx2ns)} меток")
    except RuntimeError as e:
        print(f"стенд гейт FAIL: {e}")
        (out_dir / "result.json").write_text(json.dumps(
            {"error": str(e), "clip_dur_s": dur}), encoding="utf-8")
        return

    # декод по всей длине
    cap = cv2.VideoCapture(str(dest))
    loc = None
    pts, host = [], []
    seen = set()
    n = 0
    while True:
        ret, img = cap.read()
        if not ret:
            break
        if n % args.sample_every == 0:
            p = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            out = decode_frame(img, loc)
            loc = out["loc"] or loc
            if out["idx"] is not None and out["idx"] in idx2ns and out["idx"] not in seen:
                seen.add(out["idx"])
                pts.append(p); host.append(idx2ns[out["idx"]] / 1e9)
        n += 1
    cap.release()
    pts = np.array(pts); host = np.array(host)
    print(f"декодировано пар: {len(pts)} по {round(float(pts[-1]-pts[0]),0) if len(pts)>2 else 0}с базе")
    if len(pts) < 40:
        (out_dir / "result.json").write_text(json.dumps(
            {"error": f"decode {len(pts)}<40", "clip_dur_s": dur}), encoding="utf-8")
        return

    a, b, rms, r2 = robust_fit(pts, host)
    drift_ppm = (a - 1.0) * 1e6
    # SNR: накопленный офсет / шум
    accum_ms = abs(drift_ppm) * 1e-6 * (pts[-1] - pts[0]) * 1e3
    # посегментно (окна 5 мин) — линейность/прогрев
    seg_len = 300.0
    segs = []
    t_lo = pts[0]
    while t_lo < pts[-1] - 60:
        m = (pts >= t_lo) & (pts < t_lo + seg_len)
        if m.sum() >= 15:
            sa = robust_fit(pts[m], host[m])[0]
            segs.append({"t_start_s": round(float(t_lo - pts[0]), 0),
                         "drift_ppm": round((sa - 1) * 1e6, 2), "n": int(m.sum())})
        t_lo += seg_len

    summary = {
        "clip_dur_s": round(dur, 1), "stopped_early_s": stopped_early,
        "baseline_s": round(float(pts[-1] - pts[0]), 1),
        "n_pairs": int(len(pts)),
        "drift_ppm_full": round(drift_ppm, 3),
        "us_per_s": round(drift_ppm, 3),
        "resid_rms_ms": round(rms * 1e3, 3), "r2": round(r2, 7),
        "accum_offset_ms": round(accum_ms, 1),
        "resolvable_ppm": round(rms * 1e3 / (pts[-1] - pts[0]) * 1e3, 2),
        "segments_5min": segs,
        "seg_drift_spread_ppm": round(
            max(s["drift_ppm"] for s in segs) - min(s["drift_ppm"] for s in segs), 2)
        if len(segs) >= 2 else None,
        "window_hold_s_1ms": round(1000.0 / abs(drift_ppm), 1) if drift_ppm else None,
        "verdict": None,
    }
    linear = (summary["seg_drift_spread_ppm"] is None or
              summary["seg_drift_spread_ppm"] < 3)
    summary["verdict"] = (
        f"дрейф {drift_ppm:.1f} ppm на базе {summary['baseline_s']:.0f}с; "
        + ("ЛИНЕЕН (сегменты сходятся)" if linear else
           f"НЕЛИНЕЕН — сегменты гуляют на {summary['seg_drift_spread_ppm']} ppm "
           f"(термотранзиент подтверждён)"))
    (out_dir / "result.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "segments_5min"},
                     indent=2, ensure_ascii=False))
    print("сегменты:", json.dumps(segs, ensure_ascii=False))


if __name__ == "__main__":
    main()
