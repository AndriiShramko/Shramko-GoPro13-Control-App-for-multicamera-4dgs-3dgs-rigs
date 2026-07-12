"""E9/E10 — детерминизм фазы кадра (прямой ответ на вопрос оператора).

ВОПРОС: внутрикадровая фаза постоянна от дубля к дублю или плавает?
        сбрасывается ли она стартом записи? выключением камеры?

МЕТОД (одна камера): камера снимает монитор-стенд (счётчик, привязан к QPC хоста,
непрерывный лог index→host_ns). Для дубля декодим кадры → пары (cam_pts, host_ns).
Линейный фит host_ns = a + b*cam_pts. Интерцепт `a` = host-время кадра cam_pts=0.
Фаза = a mod (период кадра). Точность интерцепта из сотен точек ~ resid/sqrt(N)
(~0.3 мс при resid 6 мс, N>200) — достаточно, чтобы отличить «постоянна» (σ<1мс,
только дрейф) от «плавает» (σ~период/√12 ≈ 4.8 мс равномерно).

ИНТЕРПРЕТАЦИЯ:
- фаза постоянна между дублями БЕЗ выключения (E9) → старт записи НЕ сбрасывает
  фазу; сенсор free-running с включения; фаза ДЕТЕРМИНИРОВАНА в пределах сессии
  питания → на площадке камеры, включённые вместе, разделяют фазу (путь Б жив).
- фаза случайна → старт сбрасывает фазу рандомно → командой не управляема.
- E10 (выключение между дублями): фаза случайна → задаётся при включении рандомно;
  фаза кластеризуется → детерминизм переживает питание.

Usage:
  # стенд counter уже запущен в отдельном процессе
  python src/exp_phase_determinism.py --takes 10 --tag E9-nocycle
  python src/exp_phase_determinism.py --takes 10 --tag E10-powercycle  # оператор жмёт кнопку между
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from wired_gopro import WiredGoPro, discover_camera_ips  # noqa: E402
from decode_stand import decode_frame  # noqa: E402


def load_stand(log: Path):
    idx2ns, wall0, perf0 = {}, None, None
    for line in log.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if "wall_utc" in r:
            wall0 = dt.datetime.fromisoformat(r["wall_utc"]).timestamp()
            perf0 = r["perf_ns"]
        elif "i" in r:
            idx2ns[r["i"]] = r["b"]
    return idx2ns, wall0, perf0


def take_pairs(video: Path, idx2ns: dict, sample_every: int = 2):
    cap = cv2.VideoCapture(str(video))
    loc = None
    xs, ys = [], []
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
                xs.append(pts)
                ys.append(idx2ns[out["idx"]] / 1e9)  # host seconds
        n += 1
    cap.release()
    return np.array(xs), np.array(ys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--pause", type=float, default=4.0)
    ap.add_argument("--tag", default="E9-nocycle")
    ap.add_argument("--fps", type=float, default=60000/1001)
    args = ap.parse_args()

    logs = sorted([f for f in glob.glob(str(REPO / "docs" / "session-logs" / "stand-counter-*.jsonl")) if "exp" not in Path(f).name])
    if not logs:
        sys.exit("start src/stand.py counter first")
    stand_log = Path(logs[-1])

    ips = discover_camera_ips()
    if not ips:
        sys.exit("camera not found")
    cam = WiredGoPro(ips[0], REPO / "docs" / "session-logs" /
                     f"exp09-{dt.date.today():%Y%m%d}.jsonl")
    cam.enable_wired_control()
    cam.start_keep_alive()

    take_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_{args.tag}"
    take_dir.mkdir(parents=True)
    (take_dir / "meta.json").write_text(json.dumps(
        {"tag": args.tag, "info": cam.info(), "state": cam.state(),
         "stand_log": str(stand_log), "fps": args.fps}, indent=2), encoding="utf-8")

    rows = []
    for i in range(args.takes):
        if args.tag.startswith("E10"):
            input(f"[E10] выключи+включи камеру, нажми Enter для дубля {i}...")  # оператор
        cam.wait_idle()
        t_cmd = time.perf_counter_ns()
        cam.shutter_start()
        time.sleep(args.seconds)
        cam.shutter_stop()
        cam.wait_idle()
        last = cam.last_captured()
        dest = take_dir / f"t{i:02d}_{last['file']}"
        cam.download(last["folder"], last["file"], dest)  # verifies size + retries
        cam.delete_file(last["folder"], last["file"])
        rows.append({"take": i, "t_cmd_ns": t_cmd, "file": dest.name})
        print(f"take {i}: {dest.name}")
        time.sleep(args.pause)
    cam.stop_keep_alive()

    idx2ns, _, _ = load_stand(stand_log)
    period_ns = 1e9 / args.fps
    results = []
    for r in rows:
        xs, ys = take_pairs(take_dir / r["file"], idx2ns)
        if len(xs) < 30:
            r["phase_ms"] = None
            r["n"] = len(xs)
            results.append(r)
            continue
        # ys(host s) = a + b*xs(cam pts s); a = host time of cam frame 0
        A = np.vstack([np.ones_like(xs), xs]).T
        coef, *_ = np.linalg.lstsq(A, ys, rcond=None)
        a_ns = coef[0] * 1e9
        resid = ys - A @ coef
        r["intercept_host_ns"] = float(a_ns)
        r["phase_ns"] = float(a_ns % period_ns)
        r["phase_ms"] = float((a_ns % period_ns) / 1e6)
        r["resid_rms_ms"] = float(np.sqrt(np.mean(resid**2)) * 1000)
        r["n"] = len(xs)
        r["start_latency_ms"] = float((a_ns - r["t_cmd_ns"]) / 1e6)
        results.append(r)
        print(f"  take {r['take']}: phase={r['phase_ms']:.2f}ms n={len(xs)} "
              f"resid={r['resid_rms_ms']:.1f}ms")

    phases = [r["phase_ms"] for r in results if r.get("phase_ms") is not None]
    # фазы циклические (mod period): считаем разброс по кругу
    summary = {"tag": args.tag, "period_ms": period_ns/1e6, "n_measured": len(phases)}
    if len(phases) >= 3:
        pm = np.array(phases)
        ang = pm / (period_ns/1e6) * 2*np.pi
        R = np.sqrt(np.mean(np.cos(ang))**2 + np.mean(np.sin(ang))**2)
        circ_std_ms = np.sqrt(-2*np.log(R)) / (2*np.pi) * (period_ns/1e6) if R > 0 else None
        summary.update({
            "phase_circular_stdev_ms": float(circ_std_ms) if circ_std_ms else None,
            "phase_values_ms": [round(p, 2) for p in phases],
            "verdict": ("ДЕТЕРМИНИРОВАНА (фаза постоянна, старт/питание не рандомит)"
                        if circ_std_ms and circ_std_ms < 2.0 else
                        "ПЛАВАЕТ (фаза случайна между дублями)")
            if circ_std_ms is not None else "недостаточно данных",
            "note": "circ_std << period/sqrt(12)=4.8ms -> deterministic; ~4.8ms -> random uniform"})
    out = (REPO / "docs" / "experiments" / "exp09-phase-determinism" /
           f"{args.tag}-{dt.datetime.now():%Y%m%d_%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "takes": results}, indent=2,
                              ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
