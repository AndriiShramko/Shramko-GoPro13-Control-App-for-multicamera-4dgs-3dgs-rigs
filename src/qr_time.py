"""Animated oT time QR for GoPro (stock HERO12/13 scan it — E6 research).

Payload format (strict, per dnewman gopro/labs#640):
    oT<YYMMDDHHMMSS.mmm>oTD<0|1>oTZ<tz-minutes>
The QR is re-rendered every display frame with the CURRENT wall time; each
shown payload is logged with QPC so the camera-side applied time can later be
compared against what was actually on screen (offset calibration).

Time source: host wall clock (which the camera will copy). QPC logged for the
stand's relative timeline. TZ/DST derived from the host's local timezone.

Usage:
    python src/qr_time.py --minutes 3
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import qrcode

REPO = Path(__file__).resolve().parent.parent


def payload_now() -> str:
    now = dt.datetime.now().astimezone()
    utc_off_min = int(now.utcoffset().total_seconds() // 60)
    dst = 1 if (now.dst() and now.dst().total_seconds() != 0) else 0
    ms = now.microsecond // 1000
    return f"oT{now:%y%m%d%H%M%S}.{ms:03d}oTD{dst}oTZ{utc_off_min}"


def run(minutes: float):
    import pygame

    pygame.init()
    surf = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    w, h = surf.get_size()
    font = pygame.font.SysFont("consolas", 36)
    log = (REPO / "docs" / "session-logs" /
           f"qr-time-{dt.datetime.now():%Y%m%d_%H%M%S}.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    t_end = time.perf_counter_ns() + int(minutes * 60 * 1e9)
    qr = qrcode.QRCode(border=2, box_size=1,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"start_wall": time.time(),
                             "start_perf_ns": time.perf_counter_ns()}) + "\n")
        while time.perf_counter_ns() < t_end:
            for ev in pygame.event.get():
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    pygame.quit()
                    return
            data = payload_now()
            qr.clear()
            qr.data_list.clear()
            qr.add_data(data)
            qr.make(fit=True)
            n = qr.modules_count
            scale = max(4, (min(w, h) * 3 // 4) // n)
            size = n * scale
            img = qr.make_image(fill_color="black", back_color="white")
            raw = img.convert("RGB").tobytes()
            qsurf = pygame.image.frombuffer(raw, img.size, "RGB")
            qsurf = pygame.transform.scale(qsurf, (size, size))
            surf.fill((255, 255, 255))
            surf.blit(qsurf, ((w - size) // 2, (h - size) // 2))
            surf.blit(font.render(data, True, (0, 0, 0)), (40, h - 60))
            t_before = time.perf_counter_ns()
            pygame.display.flip()
            t_after = time.perf_counter_ns()
            fh.write(json.dumps({"p": data, "b": t_before, "a": t_after}) + "\n")
    pygame.quit()
    print(f"log -> {log}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=3)
    run(ap.parse_args().minutes)
