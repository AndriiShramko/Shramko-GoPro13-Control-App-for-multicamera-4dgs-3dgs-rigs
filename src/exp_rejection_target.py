"""E15 — подгонка фазы к ЦЕЛИ софт-ребутами (вопрос оператора дословно):
«включил камеру, определил фазу — сколько софт-ребутов, чтобы снова найти
такую же фазу?»

Протокол:
1. Два замера фазы без ребута с зазором -> локальный дрейф d (ppm камеры vs
   хост-клок). Цель = вторая фаза, экстраполируемая дрейфом: target(t).
2. Цикл: QR !OR ребут -> wake -> короткий дубль -> фаза phi(t) -> попадание,
   если circ-дистанция(phi, target(t)) <= WINDOW_MS. Иначе повтор.
3. Отчёт: число ребутов до попадания, все фазы/дистанции.
Теория: p = 2*WINDOW/16.683; WINDOW=1.0 мс -> p~0.12 -> геометрич. среднее
~8.3 ребута. Совпадение с теорией валидирует rejection-архитектуру.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import numpy as np

from wired_gopro import WiredGoPro
from exp_phase_determinism import load_stand, take_pairs
from exp_power_cycle import show_qr_until_reboot, wait_camera_back, take_clip, IP

WINDOW_MS = 1.0
MAX_REBOOTS = 20
TAKE_S = 8  # edges-only fit: ~240 edges -> SE ~0.6ms (4s gave ~0.9)
PERIOD_MS = 1e3 / (60000 / 1001)


def phase_of(rec, out_dir):
    try:
        idx2ns, _, _ = load_stand(Path(rec["stand_log"]))
    except RuntimeError as exc:  # pacing gate failed -> замер невалиден, не смерть
        print(f"  ! {exc}")
        return None
    # sample_every=2 == one stand period per sample: COHERENT sampling, the
    # camera-vs-stand lag creeps by ~22us/sample and the fit residual drops
    # from ~9ms (incoherent 1.5P steps) to ~0.3ms (2026-07-13)
    xs, ys = take_pairs(out_dir / rec["file"], idx2ns, sample_every=2)
    if len(xs) < 12:
        return None
    A = np.vstack([np.ones_like(xs), xs]).T
    coef, *_ = np.linalg.lstsq(A, ys, rcond=None)
    return {"phase_ms": float((coef[0] * 1e9 % (PERIOD_MS * 1e6)) / 1e6),
            "t_s": rec["t_cmd_ns"] / 1e9, "n": int(len(xs))}


def circ_dist(a, b):
    d = abs(a - b) % PERIOD_MS
    return min(d, PERIOD_MS - d)


def main():
    global_t0 = time.perf_counter()
    out_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_E15-target"
    out_dir.mkdir(parents=True)

    import exp_power_cycle as pc
    pc.TAKE_S = TAKE_S  # короткие дубли для скорости цикла

    cam = WiredGoPro(IP)
    cam.enable_wired_control()
    cam.start_keep_alive()
    # 1) дрейф: два замера без ребута (retry при провале гейта/декода)
    print("=== drift estimation (no reboot) ===")
    p1 = p2 = None
    for attempt in range(3):
        r1 = take_clip(cam, out_dir, f"drift1_{attempt}")
        time.sleep(20)
        r2 = take_clip(cam, out_dir, f"drift2_{attempt}")
        p1, p2 = phase_of(r1, out_dir), phase_of(r2, out_dir)
        if p1 and p2:
            break
        print(f"  drift attempt {attempt} failed, retry")
    cam.stop_keep_alive()
    if not (p1 and p2):
        sys.exit("drift takes failed после 3 попыток")
    dphi = (p2["phase_ms"] - p1["phase_ms"] + PERIOD_MS / 2) % PERIOD_MS - PERIOD_MS / 2
    drift_ms_per_s = dphi / (p2["t_s"] - p1["t_s"])
    print(f"phi1={p1['phase_ms']:.3f} phi2={p2['phase_ms']:.3f} "
          f"drift={drift_ms_per_s*1000:.3f} us/s")

    def target_at(t_s):
        return (p2["phase_ms"] + drift_ms_per_s * (t_s - p2["t_s"])) % PERIOD_MS

    # 2) rejection-цикл
    attempts = []
    hit = None
    for k in range(MAX_REBOOTS):
        print(f"=== reboot {k+1} ===")
        try:
            w = WiredGoPro(IP)
            w.enable_wired_control()
            w.get("/gopro/camera/stream/start?port=8554", timeout=10)
            time.sleep(2)
        except Exception:
            time.sleep(3)
        if not show_qr_until_reboot():
            print("  QR fail, retry")
            continue
        if not wait_camera_back():
            print("  камера не вернулась")
            break
        time.sleep(5)
        cam = WiredGoPro(IP)
        cam.enable_wired_control()
        cam.start_keep_alive()
        rec = take_clip(cam, out_dir, f"a{k:02d}")
        cam.stop_keep_alive()
        ph = phase_of(rec, out_dir)
        if ph is None:
            attempts.append({"reboot": k + 1, "phase_ms": None})
            print("  decode fail")
            continue
        tgt = target_at(ph["t_s"])
        dist = circ_dist(ph["phase_ms"], tgt)
        attempts.append({"reboot": k + 1, "phase_ms": round(ph["phase_ms"], 3),
                         "target_ms": round(tgt, 3), "dist_ms": round(dist, 3),
                         "n": ph["n"]})
        print(f"  phase={ph['phase_ms']:.3f} target={tgt:.3f} dist={dist:.3f}")
        if dist <= WINDOW_MS:
            hit = k + 1
            print(f"  *** HIT после {hit} ребутов ***")
            break

    summary = {"window_ms": WINDOW_MS,
               "theory_mean_reboots": round(PERIOD_MS / (2 * WINDOW_MS), 1),
               "drift_us_per_s": round(drift_ms_per_s * 1000, 2),
               "reboots_to_hit": hit,
               "attempts": attempts,
               "total_minutes": round((time.perf_counter() - global_t0) / 60, 1)}
    (out_dir / "result.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
