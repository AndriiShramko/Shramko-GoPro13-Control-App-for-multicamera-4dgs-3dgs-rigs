"""E8-min — функциональная проверка Labs `!S` (старт по часам камеры), 60fps.

Чистая версия: НЕ декодит стенд. Логика:
  1. Часы камеры выставлены от хоста (HTTP set_date_time, точность 1 с — для функц.
     проверки достаточно; для мс — QR/serial отдельно).
  2. QR `!<T>S` (старт в абсолютную секунду T часов камеры).
  3. Хост поллит encoding-флаг камеры → фиксирует host-время, когда флаг стал True.
  4. err = host_time(encoding=True) − T. Учитывать: поллинг HTTP ~50-100 мс = ПОЛ этого
     разброса, поэтому это ПОТОЛОК точности измерения, не самой камеры.

Отвечает на: (а) вообще ли `!S` срабатывает по часам (функционально да/нет),
(б) грубый разброс. НЕ отвечает на inter-camera sync (нужна 2-я камера) и НЕ на
субкадровую фазу (её `!S` не даёт в принципе).

Требует: камера видит монитор (для скана QR). Usage: python src/exp_ss_functional.py --takes 6
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import pygame  # noqa: E402
import qrcode  # noqa: E402

from wired_gopro import WiredGoPro, discover_camera_ips  # noqa: E402


def show_qr(surf, payload, seconds):
    w, h = surf.get_size()
    qr = qrcode.QRCode(border=2, box_size=1)
    qr.add_data(payload); qr.make(fit=True)
    img = qr.make_image().convert("RGB")
    n = qr.modules_count
    scale = max(6, (min(w, h) * 2 // 3) // n)
    size = n * scale
    qs = pygame.transform.scale(pygame.image.frombuffer(img.tobytes(), img.size, "RGB"),
                                (size, size))
    surf.fill((255, 255, 255)); surf.blit(qs, ((w - size) // 2, (h - size) // 2))
    pygame.display.flip(); time.sleep(seconds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", type=int, default=6)
    ap.add_argument("--tz", type=int, default=120)
    args = ap.parse_args()

    ips = discover_camera_ips()
    if not ips:
        sys.exit("camera not found")
    cam = WiredGoPro(ips[0])
    cam.enable_wired_control()
    cam.start_keep_alive()

    pygame.init()
    surf = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)

    rows = []
    for i in range(args.takes):
        if cam.flags()["encoding"]:
            cam.shutter_stop(); time.sleep(1)
        now = dt.datetime.now()
        cam.get(f"/gopro/camera/set_date_time?date={now:%Y_%m_%d}"
                f"&time={now:%H_%M_%S}&tzone={args.tz}&dst=0")
        target = (dt.datetime.now() + dt.timedelta(seconds=6)).replace(microsecond=0)
        show_qr(surf, f"!{target:%H:%M:%S}S", 3)
        surf.fill((0, 0, 0)); pygame.display.flip()  # dark: don't disturb scene
        # poll encoding flag from ~2s before target to +4s after
        fired_at = None
        while dt.datetime.now() < target + dt.timedelta(seconds=5):
            for ev in pygame.event.get():
                pass
            if cam.flags()["encoding"]:
                fired_at = time.time()
                break
            time.sleep(0.03)
        err_ms = (fired_at - target.timestamp()) * 1000 if fired_at else None
        time.sleep(1.5)
        if cam.flags()["encoding"]:
            cam.shutter_stop()
        rows.append({"take": i, "target": target.isoformat(),
                     "fired": fired_at is not None, "err_ms": err_ms})
        print(f"take {i}: target={target:%H:%M:%S} fired={fired_at is not None} "
              f"err_ms={err_ms:.0f}" if err_ms is not None else
              f"take {i}: target={target:%H:%M:%S} DID NOT FIRE")
        time.sleep(1)
    cam.stop_keep_alive()
    pygame.quit()

    fired = [r for r in rows if r["fired"]]
    errs = [r["err_ms"] for r in fired]
    summary = {"n_takes": len(rows), "n_fired": len(fired)}
    if len(errs) >= 2:
        summary.update({"mean_err_ms": statistics.mean(errs),
                        "stdev_err_ms": statistics.stdev(errs),
                        "min_err_ms": min(errs), "max_err_ms": max(errs),
                        "poll_floor_ms": "~30-100 (HTTP poll RTT) — measurement ceiling",
                        "note": "!S fires by camera clock; stdev bounded by our poll rate. "
                                "Inter-camera sync = Phase 1 (2nd camera). Sub-frame: N/A for !S."})
    out = (REPO / "docs" / "experiments" / "exp08-delayed-start" /
           f"ss-functional-{dt.datetime.now():%Y%m%d_%H%M%S}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
