"""E16 — марафон rejection-калибровки в окно 1/50 кадра (вопрос оператора).

Цель: 5-10 ПОПАДАНИЙ с дистанцией <= P/50 (0.334 мс), чтобы измерить
среднее число передёргов на попадание. Теория: p = 2*(P/50)/P = 0.04
-> ~25 ребутов/попадание; 8 попаданий ~ 200 циклов ~ 5-8 часов. Ночной фон.

Против уплывания цели: дрейф камеры нелинеен на часах (температура), поэтому
дрейф ПЕРЕ-замеряется в начале каждого раунда и каждые 6 неудачных попыток.
Батарея: ребуты жрут; при <15% марафон сам уходит в 30-мин зарядную паузу.
Прогресс пишется в progress.json после КАЖДОЙ попытки.
"""
from __future__ import annotations

import datetime as dt
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
import exp_power_cycle as pc

PERIOD_MS = 1e3 / (60000 / 1001)
WINDOW_MS = PERIOD_MS / 50          # 0.334 мс — «1/50 кадра»
TARGET_HITS = 8
MAX_ATTEMPTS = 260
DRIFT_REFRESH_EVERY = 6             # неудач между пере-замерами дрейфа
TAKE_S = 8
pc.TAKE_S = TAKE_S


def phase_of(rec, out_dir):
    try:
        idx2ns, _, _ = load_stand(Path(rec["stand_log"]))
    except RuntimeError as exc:
        print(f"  ! {exc}")
        return None
    xs, ys = take_pairs(out_dir / rec["file"], idx2ns, sample_every=2)
    if len(xs) < 30:
        return None
    A = np.vstack([np.ones_like(xs), xs]).T
    coef, *_ = np.linalg.lstsq(A, ys, rcond=None)
    return {"phase_ms": float((coef[0] * 1e9 % (PERIOD_MS * 1e6)) / 1e6),
            "t_s": rec["t_cmd_ns"] / 1e9, "n": int(len(xs))}


def circ_dist(a, b):
    d = abs(a - b) % PERIOD_MS
    return min(d, PERIOD_MS - d)


def battery_pct(cam) -> int:
    try:
        return int(cam.state()["status"].get("70", 0))
    except Exception:
        return -1


def measure_drift(cam, out_dir, tag):
    """Два дубля подряд -> (опорная фаза, время, дрейф мс/с) или None."""
    for attempt in range(3):
        r1 = take_clip(cam, out_dir, f"{tag}a{attempt}")
        time.sleep(15)
        r2 = take_clip(cam, out_dir, f"{tag}b{attempt}")
        p1, p2 = phase_of(r1, out_dir), phase_of(r2, out_dir)
        if p1 and p2:
            dphi = ((p2["phase_ms"] - p1["phase_ms"] + PERIOD_MS / 2)
                    % PERIOD_MS - PERIOD_MS / 2)
            drift = dphi / (p2["t_s"] - p1["t_s"])
            return p2["phase_ms"], p2["t_s"], drift
        print(f"  drift {tag} attempt {attempt} failed")
    return None


def main():
    t_start = time.perf_counter()
    out_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_E16-marathon"
    out_dir.mkdir(parents=True)
    progress = {"window_ms": round(WINDOW_MS, 3), "target_hits": TARGET_HITS,
                "hits": [], "attempts": [], "started": dt.datetime.now().isoformat()}

    def save():
        progress["elapsed_min"] = round((time.perf_counter() - t_start) / 60, 1)
        (out_dir / "progress.json").write_text(
            json.dumps(progress, indent=2, ensure_ascii=False), encoding="utf-8")

    hits = 0
    attempt_no = 0
    fails_since_drift = 0
    ref = None  # (phase, t, drift)

    while hits < TARGET_HITS and attempt_no < MAX_ATTEMPTS:
        cam = WiredGoPro(IP)
        try:
            cam.enable_wired_control()
        except Exception:
            time.sleep(10)
            continue
        b = battery_pct(cam)
        if 0 <= b < 15:
            print(f"батарея {b}% -> зарядная пауза 30 мин")
            progress["attempts"].append({"note": f"charge pause at {b}%"})
            save()
            time.sleep(1800)
            continue

        if ref is None or fails_since_drift >= DRIFT_REFRESH_EVERY:
            cam.start_keep_alive()
            print(f"=== drift refresh (battery {b}%) ===")
            ref = measure_drift(cam, out_dir, f"d{attempt_no:03d}")
            cam.stop_keep_alive()
            if ref is None:
                print("  drift failed, retry loop")
                time.sleep(20)
                continue
            fails_since_drift = 0
            print(f"  ref phase={ref[0]:.3f} drift={ref[2]*1000:.2f} us/s")
            save()

        # ребут-попытка
        attempt_no += 1
        try:
            w = WiredGoPro(IP)
            w.enable_wired_control()
            w.get("/gopro/camera/stream/start?port=8554", timeout=10)
            time.sleep(2)
        except Exception:
            time.sleep(3)
        if not show_qr_until_reboot():
            print(f"[{attempt_no}] QR fail")
            progress["attempts"].append({"n": attempt_no, "note": "qr_fail"})
            save()
            continue
        if not wait_camera_back():
            print(f"[{attempt_no}] камера не вернулась")
            progress["attempts"].append({"n": attempt_no, "note": "no_return"})
            save()
            time.sleep(30)
            continue
        time.sleep(5)
        cam = WiredGoPro(IP)
        try:
            cam.enable_wired_control()
            cam.start_keep_alive()
            rec = take_clip(cam, out_dir, f"t{attempt_no:03d}")
            cam.stop_keep_alive()
        except Exception as exc:
            print(f"[{attempt_no}] take failed: {type(exc).__name__}")
            progress["attempts"].append({"n": attempt_no, "note": "take_fail"})
            save()
            continue
        ph = phase_of(rec, out_dir)
        # клипы марафона большие — чистим после декода
        try:
            (out_dir / rec["file"]).unlink()
        except OSError:
            pass
        if ph is None:
            print(f"[{attempt_no}] decode fail")
            progress["attempts"].append({"n": attempt_no, "note": "decode_fail"})
            save()
            continue
        target = (ref[0] + ref[2] * (ph["t_s"] - ref[1])) % PERIOD_MS
        dist = circ_dist(ph["phase_ms"], target)
        row = {"n": attempt_no, "phase_ms": round(ph["phase_ms"], 3),
               "target_ms": round(target, 3), "dist_ms": round(dist, 3),
               "pts": ph["n"]}
        hit = dist <= WINDOW_MS
        if hit:
            hits += 1
            row["HIT"] = hits
            print(f"[{attempt_no}] *** HIT #{hits} *** dist={dist:.3f}")
            ref = None  # новый раунд: свежая цель и дрейф
        else:
            fails_since_drift += 1
            print(f"[{attempt_no}] phase={ph['phase_ms']:.3f} dist={dist:.3f}")
        progress["attempts"].append(row)
        if hit:
            progress["hits"].append(row)
        save()

    # итоговая статистика
    valid = [a for a in progress["attempts"] if "dist_ms" in a]
    n_valid = len(valid)
    p_hat = hits / max(1, n_valid)
    progress["summary"] = {
        "hits": hits, "valid_attempts": n_valid,
        "p_hat": round(p_hat, 4),
        "mean_reboots_per_hit": round(1 / p_hat, 1) if p_hat > 0 else None,
        "theory_mean": round(PERIOD_MS / (2 * WINDOW_MS), 1),
        "uniformity_check_dists": [a["dist_ms"] for a in valid][:50]}
    save()
    print(json.dumps(progress["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
