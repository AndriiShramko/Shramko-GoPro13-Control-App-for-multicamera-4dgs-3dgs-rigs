"""E10 — что делает РЕБУТ камеры с фазой сенсора (без человека!).

Механика: Labs-камера сканирует QR с монитора. Показываем `!OR` (reboot) →
камера уходит в ребут (HTTP пропадает) → ждём возвращения → короткий
стенд-прогон + дубль → фаза. Повтор N циклов.

Фазы разных циклов сравнимы: все интерцепты в одном host-QPC таймлайне.

Интерпретация:
- фазы циклов ложатся на ppm-прямую (как E9v2) → фаза ПЕРЕЖИВАЕТ ребут
  (сенсор-клок не сбрасывается) → пересброс питанием фазу НЕ меняет.
- фазы скачут между циклами → ребут РАНДОМИЗИРУЕТ фазу → rejection-калибровка
  питанием реальна (пересбрасывать до попадания в окно, потом фаза держится).
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import numpy as np
import requests

from wired_gopro import WiredGoPro
from exp_phase_determinism import load_stand, take_pairs

IP = "172.25.139.51"
CYCLES = 6
TAKE_S = 8
PY = str(REPO / ".venv" / "Scripts" / "python.exe")


def show_qr_until_reboot(timeout_s: float = 75) -> bool:
    """Fullscreen !OR QR; success = camera drops off HTTP (reboot started)."""
    import pygame
    import qrcode

    pygame.init()
    surf = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    w, h = surf.get_size()
    qr = qrcode.QRCode(border=2, box_size=1)
    qr.add_data("!OR")
    qr.make(fit=True)
    n = qr.modules_count
    scale = max(8, (min(w, h) * 3 // 5) // n)
    img = qr.make_image(fill_color="black", back_color="white")
    raw = img.convert("RGB").tobytes()
    qsurf = pygame.image.frombuffer(raw, img.size, "RGB")
    qsurf = pygame.transform.scale(qsurf, (n * scale, n * scale))
    surf.fill((255, 255, 255))
    surf.blit(qsurf, ((w - n * scale) // 2, (h - n * scale) // 2))
    pygame.display.flip()
    t0 = time.perf_counter()
    rebooted = False
    while time.perf_counter() - t0 < timeout_s:
        pygame.event.pump()
        try:
            requests.get(f"http://{IP}:8080/gopro/camera/state", timeout=2)
        except requests.RequestException:
            rebooted = True
            break
        time.sleep(1.5)
    pygame.quit()
    return rebooted


def wait_camera_back(timeout_s: float = 120) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout_s:
        try:
            r = requests.get(f"http://{IP}:8080/gopro/camera/state", timeout=3)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    return False


def main():
    out_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_E10-reboot"
    out_dir.mkdir(parents=True)
    rows = []
    for c in range(CYCLES):
        print(f"=== cycle {c} ===")
        cam = WiredGoPro(IP)
        cam.enable_wired_control()
        cam.start_keep_alive()  # wake для скана QR
        time.sleep(2)
        cam.stop_keep_alive()
        if not show_qr_until_reboot():
            print("  камера НЕ ребутнулась по QR — стоп")
            break
        print("  reboot detected, waiting back...")
        if not wait_camera_back():
            print("  камера не вернулась — стоп")
            break
        time.sleep(5)
        cam = WiredGoPro(IP)
        cam.enable_wired_control()
        cam.start_keep_alive()
        # mode verify (persist через ребут, но проверяем)
        st = cam.state()["settings"]
        got = {k: st.get(k) for k in ("2", "234", "3")}
        if got != {"2": 108, "234": 5, "3": 5}:
            cam.get("/gopro/camera/presets/set_group?id=1000", timeout=12); time.sleep(2)
            cam.get("/gopro/camera/presets/load?id=0", timeout=12); time.sleep(2)
            for sid, opt in (("2", 108), ("234", 5), ("3", 5)):
                cam.set_setting(int(sid), opt); time.sleep(1.2)
            st = cam.state()["settings"]
            got = {k: st.get(k) for k in ("2", "234", "3")}
            if got != {"2": 108, "234": 5, "3": 5}:
                print("  mode verify failed:", got)
                break
        # свежий стенд на время дубля
        stand = subprocess.Popen(
            [PY, str(REPO / "src" / "stand.py"), "counter", "--minutes", "1.2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(9)  # warm-up стенда
        cam.wait_idle()
        t_cmd = time.perf_counter_ns()
        cam.shutter_start()
        t0 = time.perf_counter_ns()
        while time.perf_counter_ns() - t0 < 10e9:
            if cam.flags()["encoding"]:
                break
            time.sleep(0.05)
        time.sleep(TAKE_S)
        for _ in range(6):
            try:
                cam.shutter_stop(); break
            except Exception:
                time.sleep(1.5)
        cam.wait_idle()
        stand_log = sorted([f for f in glob.glob(
            str(REPO / "docs" / "session-logs" / "stand-counter-*.jsonl"))
            if "exp" not in Path(f).name])[-1]
        last = cam.last_captured()
        dest = out_dir / f"c{c:02d}_{last['file']}"
        cam.download(last["folder"], last["file"], dest)
        cam.delete_file(last["folder"], last["file"])
        cam.stop_keep_alive()
        stand.wait(timeout=90)
        rows.append({"cycle": c, "t_cmd_ns": t_cmd, "file": dest.name,
                     "stand_log": stand_log})
        print(f"  clip {dest.name}, stand {Path(stand_log).name}")

    # анализ: фаза каждого цикла в общем host-QPC таймлайне
    period_ns = 1e9 / (60000 / 1001)
    results = []
    for r in rows:
        idx2ns, _, _ = load_stand(Path(r["stand_log"]))
        xs, ys = take_pairs(out_dir / r["file"], idx2ns, sample_every=2)
        if len(xs) < 20:
            r["phase_ms"] = None
            r["n"] = int(len(xs))
            results.append(r); continue
        A = np.vstack([np.ones_like(xs), xs]).T
        coef, *_ = np.linalg.lstsq(A, ys, rcond=None)
        a_ns = coef[0] * 1e9
        resid = ys - A @ coef
        r["phase_ms"] = round(float((a_ns % period_ns) / 1e6), 3)
        r["n"] = int(len(xs))
        r["resid_rms_ms"] = round(float(np.sqrt(np.mean(resid**2)) * 1000), 2)
        r["t_host_s"] = round(r["t_cmd_ns"] / 1e9, 1)
        results.append(r)
        print(f"  cycle {r['cycle']}: phase={r['phase_ms']}ms n={r['n']}")

    ph = [(r["t_host_s"], r["phase_ms"]) for r in results if r.get("phase_ms") is not None]
    summary = {"cycles_measured": len(ph), "period_ms": period_ns / 1e6}
    if len(ph) >= 3:
        tt = np.array([p[0] for p in ph]); pm = np.array([p[1] for p in ph])
        period_ms = period_ns / 1e6
        unw = np.unwrap(pm / period_ms * 2 * np.pi) / (2 * np.pi) * period_ms
        trend = np.polyfit(tt - tt[0], unw, 1)
        det = unw - np.polyval(trend, tt - tt[0])
        summary["phases_ms"] = [round(p, 2) for p in pm]
        summary["detrended_sigma_ms"] = round(float(np.std(det)), 3)
        summary["verdict"] = ("ФАЗА ПЕРЕЖИВАЕТ РЕБУТ (ppm-прямая)"
                              if float(np.std(det)) < 1.0 else
                              "РЕБУТ РАНДОМИЗИРУЕТ ФАЗУ -> rejection-калибровка питанием реальна")
    out = out_dir / "result.json"
    out.write_text(json.dumps({"summary": summary, "cycles": results}, indent=2,
                              ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
