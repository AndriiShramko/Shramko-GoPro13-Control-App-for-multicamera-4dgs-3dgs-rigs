"""E18 — ЧИСТЫЙ замер дрейфа часов камеры (методика RocSync + перепроверка).

Оператор усомнился в дрейфе (E2 +14.6 ppm vs E15 -46 ppm — знак-флип, 3x).
Причины подозрений: правильно ли настроен стенд, корректен ли декод.

Метод (RocSync s26031036): одна ДЛИННАЯ непрерывная запись стенда → пары
(cam_pts, host_ns) по всей длине → робастный линейный фит host = a*cam + b →
drift = a-1. Валидация: (1) стенд прошёл PACING_GATE; (2) фит чистый (R^2,
resid); (3) первая половина ≈ вторая (линейность = нет температурного излома);
(4) знак и величина стабильны между повторами.

Запуск: стенд counter уже крутится.
  python src/exp_drift_clean.py --minutes 4 --runs 2
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import cv2
import numpy as np

from wired_gopro import WiredGoPro, discover_camera_ips
from exp_phase_determinism import load_stand
from decode_stand import decode_frame


def robust_fit(x, y):
    """Theil-Sen slope + median intercept + residual RMS. x,y in seconds."""
    n = len(x)
    order = np.argsort(x)
    xs, ys = x[order], y[order]
    lag = max(1, n // 4)
    slopes = [(ys[i + lag] - ys[i]) / (xs[i + lag] - xs[i])
              for i in range(n - lag) if xs[i + lag] != xs[i]]
    a = float(np.median(slopes))
    b = float(np.median(ys - a * xs))
    resid = ys - (a * xs + b)
    rms = float(np.sqrt(np.mean(resid**2)))
    # R^2
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((ys - np.mean(ys))**2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return a, b, rms, r2


def measure_clip(video: Path, idx2ns: dict, sample_every: int = 20):
    cap = cv2.VideoCapture(str(video))
    loc = None
    fn, pts, idx = [], [], []
    n = 0
    while True:
        ret, img = cap.read()
        if not ret:
            break
        if n % sample_every == 0:
            p = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            out = decode_frame(img, loc)
            loc = out["loc"] or loc
            if out["idx"] is not None and out["idx"] in idx2ns:
                fn.append(n); pts.append(p); idx.append(out["idx"])
        n += 1
    cap.release()
    if len(fn) < 20:
        return None
    # temporal-model dedup: keep first frame of each stand index (edge)
    seen = set(); keep = []
    for i in range(len(idx)):
        if idx[i] not in seen:
            seen.add(idx[i]); keep.append(i)
    pts = np.array([pts[i] for i in keep])
    host = np.array([idx2ns[idx[i]] / 1e9 for i in keep])
    return pts, host


def analyze(pts, host):
    a, b, rms, r2 = robust_fit(pts, host)
    drift_ppm = (a - 1.0) * 1e6
    # первая vs вторая половина (линейность / температура)
    mid = len(pts) // 2
    a1 = robust_fit(pts[:mid], host[:mid])[0]
    a2 = robust_fit(pts[mid:], host[mid:])[0]
    return {"drift_ppm": round(drift_ppm, 2), "resid_rms_ms": round(rms * 1e3, 3),
            "r2": round(r2, 6), "n": int(len(pts)),
            "span_s": round(float(pts[-1] - pts[0]), 1),
            "drift_ppm_firsthalf": round((a1 - 1) * 1e6, 2),
            "drift_ppm_secondhalf": round((a2 - 1) * 1e6, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=4.0)
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    ips = discover_camera_ips()
    cam = WiredGoPro(ips[0])
    cam.enable_wired_control()
    cam.start_keep_alive()
    out_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_E18-drift"
    out_dir.mkdir(parents=True)
    results = []
    for run in range(args.runs):
        print(f"=== run {run}: {args.minutes} min continuous ===")
        cam.wait_idle()
        cam.shutter_start()
        t0 = time.perf_counter_ns()
        while time.perf_counter_ns() - t0 < 10e9:
            if cam.flags()["encoding"]:
                break
            time.sleep(0.05)
        # держим запись, редкий tolerant-опрос (HTTP хрупок при записи)
        t_rec = time.perf_counter()
        while time.perf_counter() - t_rec < args.minutes * 60:
            time.sleep(15)
        for _ in range(8):
            try:
                cam.shutter_stop(); break
            except Exception:
                time.sleep(1.5)
        cam.wait_idle()
        last = cam.last_captured()
        dest = out_dir / f"r{run}_{last['file']}"
        cam.download(last["folder"], last["file"], dest)
        cam.delete_file(last["folder"], last["file"])
        # свежий стенд-лог (тот, что крутился во время записи)
        logs = sorted([f for f in glob.glob(str(
            REPO / "docs" / "session-logs" / "stand-counter-*.jsonl"))
            if "exp" not in Path(f).name])
        stand_log = Path(logs[-1])
        try:
            idx2ns, _, _ = load_stand(stand_log)
            gate = "PASS"
        except RuntimeError as e:
            print(f"  ! стенд гейт: {e}")
            results.append({"run": run, "error": str(e)}); continue
        m = measure_clip(dest, idx2ns)
        if m is None:
            results.append({"run": run, "error": "decode<20"}); continue
        res = analyze(*m)
        res["run"] = run; res["stand"] = stand_log.name; res["gate"] = gate
        results.append(res)
        print(f"  drift={res['drift_ppm']} ppm | R2={res['r2']} resid={res['resid_rms_ms']}мс "
              f"n={res['n']} span={res['span_s']}с | 1я={res['drift_ppm_firsthalf']} "
              f"2я={res['drift_ppm_secondhalf']} ppm")
        time.sleep(5)
    cam.stop_keep_alive()

    valid = [r for r in results if "drift_ppm" in r]
    summary = {"runs": results}
    if valid:
        d = [r["drift_ppm"] for r in valid]
        summary["drift_ppm_mean"] = round(float(np.mean(d)), 2)
        summary["drift_ppm_spread"] = round(float(np.max(d) - np.min(d)), 2) if len(d) > 1 else 0
        summary["us_per_s"] = round(float(np.mean(d)), 2)  # ppm == us/s
        summary["window_hold_s_1ms"] = round(1000.0 / abs(np.mean(d)), 1) if np.mean(d) else None
        summary["note"] = ("линейность ок если 1я≈2я половина; знак стабилен между run")
    p = out_dir / "result.json"
    p.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "runs"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
