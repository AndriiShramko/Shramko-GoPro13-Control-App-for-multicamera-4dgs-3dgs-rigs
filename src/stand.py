"""Measurement stand: fullscreen machine-readable time display + scheduled flashes.

Design (per docs/specs/spec-experiments.md):
- Host timebase = time.perf_counter_ns (QPC). Wall clock logged once per session.
- Machine-readable encoding, NOT OCR digits: a strip of large cells encoding the
  stand frame index in 24-bit Gray code, plus an 8-cell fixed sync/threshold
  pattern. Big cells survive motion blur and camera downscale.
- Moving bar for sub-refresh phase reading; human digits only as a courtesy.
- Every display flip is logged (frame idx, perf_ns before/after flip) -> JSONL.
- Flash mode paints full-white frames at scheduled indices (logged the same way).
- Beep mode plays a short chirp via pygame mixer at scheduled times; the *schedule*
  is logged, but WASAPI latency is unknown/unstable, so audio events are used for
  RELATIVE camera-vs-camera comparisons only (spec-measure).
- Display sleep suppressed via SetThreadExecutionState within this process only.
- The stand keeps content inside a central safe-zone (fraction of screen) so the
  narrowest camera FOV still frames it (red-team #12).

Usage:
    python src/stand.py counter --minutes 25
    python src/stand.py flash --period 5 --minutes 2
    python src/stand.py render-selftest --frames 300 --out captures/selftest
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
import sys as _sys
_sys.path.insert(0, str(REPO / "src"))

from decode_stand import encode_cells, N_CELLS  # shared encoding

ES_CONTINUOUS = 0x80000000
ES_DISPLAY_REQUIRED = 0x00000002


def keep_display_awake():
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_DISPLAY_REQUIRED)


def release_display():
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)


class StandRenderer:
    """Draw one stand frame onto a pygame surface. Kept pygame-optional so the
    self-test renderer can also use it headless (SDL dummy driver)."""

    def __init__(self, size: tuple[int, int], safe_zone: float = 0.6):
        self.w, self.h = size
        zone_w = int(self.w * safe_zone)
        zone_h = int(self.h * safe_zone)
        self.x0 = (self.w - zone_w) // 2
        self.y0 = (self.h - zone_h) // 2
        self.zone_w, self.zone_h = zone_w, zone_h
        self.cell = max(8, zone_w // N_CELLS)
        # strip sits at 55% height: cameras near the desk see the LOWER part of
        # the panel first (framing probe 2026-07-12 — top of screen got clipped)
        self.strip_y = self.y0 + int(zone_h * 0.55)
        self.bar_y = self.y0 + int(zone_h * 0.75)
        self.bar_h = max(10, zone_h // 20)

    def draw(self, surf, frame_idx: int, flash: bool, font=None, extra_text: str = ""):
        import pygame

        if flash:
            surf.fill((255, 255, 255))
            return
        surf.fill((0, 0, 0))
        # sync pattern then Gray-coded frame index, MSB first
        bits = encode_cells(frame_idx)
        for i, b in enumerate(bits):
            color = (255, 255, 255) if b else (30, 30, 30)
            x = self.x0 + i * self.cell
            # 2x-tall cells: curved panels + wide lens bow the strip by ~a cell
            pygame.draw.rect(surf, color,
                             (x, self.strip_y, self.cell - 2, self.cell * 2))
        # moving bar (position = sub-second phase at 1 px/frame wrap)
        bar_x = self.x0 + (frame_idx * 7) % max(1, self.zone_w - 40)
        pygame.draw.rect(surf, (255, 255, 255), (bar_x, self.bar_y, 40, self.bar_h))
        if font is not None:
            txt = f"{frame_idx}  {extra_text}"
            surf.blit(font.render(txt, True, (200, 200, 200)),
                      (self.x0, self.strip_y + self.cell * 2 + 24))


def run_display(mode: str, minutes: float, flash_period_s: float):
    import pygame

    keep_display_awake()
    pygame.init()
    surf = pygame.display.set_mode((0, 0),
                                   pygame.FULLSCREEN | pygame.DOUBLEBUF,
                                   vsync=1)
    font = pygame.font.SysFont("consolas", 48)
    renderer = StandRenderer(surf.get_size())
    log = (REPO / "docs" / "session-logs" /
           f"stand-{mode}-{dt.datetime.now():%Y%m%d_%H%M%S}.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    clock = pygame.time.Clock()
    t_end = time.perf_counter_ns() + int(minutes * 60 * 1e9)
    frame_idx = 0
    session_header = {
        "mode": mode, "screen": surf.get_size(), "cell_px": renderer.cell,
        "wall_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "perf_ns": time.perf_counter_ns(), "flash_period_s": flash_period_s,
        "display_hz_reported": pygame.display.get_current_refresh_rate()
        if hasattr(pygame.display, "get_current_refresh_rate") else None,
    }
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(session_header) + "\n")
        next_flash_ns = time.perf_counter_ns() + int(flash_period_s * 1e9)
        try:
            while time.perf_counter_ns() < t_end:
                for ev in pygame.event.get():
                    if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                        return
                flash = False
                if mode == "flash" and time.perf_counter_ns() >= next_flash_ns:
                    flash = True
                    next_flash_ns += int(flash_period_s * 1e9)
                renderer.draw(surf, frame_idx, flash, font,
                              extra_text="FLASH" if flash else "")
                t_before = time.perf_counter_ns()
                pygame.display.flip()
                t_after = time.perf_counter_ns()
                fh.write(json.dumps({"i": frame_idx, "f": int(flash),
                                     "b": t_before, "a": t_after}) + "\n")
                frame_idx += 1
                clock.tick(0)  # run at vsync pace (flip blocks on vsync)
        finally:
            pygame.quit()
            release_display()
    print(f"log -> {log}")


def render_selftest(frames: int, out_dir: Path):
    """Render stand frames headless to PNGs (known ground truth) for decoder
    validation — no camera required (red-team #15)."""
    import os

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame

    pygame.init()
    size = (1280, 720)
    surf = pygame.Surface(size)
    renderer = StandRenderer(size)
    out_dir.mkdir(parents=True, exist_ok=True)
    truth = []
    for i in range(frames):
        flash = (i % 97 == 0 and i > 0)
        renderer.draw(surf, i, flash)
        pygame.image.save(surf, str(out_dir / f"st_{i:06d}.png"))
        truth.append({"i": i, "flash": int(flash)})
    (out_dir / "truth.json").write_text(json.dumps(truth), encoding="utf-8")
    print(f"{frames} frames + truth.json -> {out_dir}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("counter"); p.add_argument("--minutes", type=float, default=1)
    p = sub.add_parser("flash"); p.add_argument("--minutes", type=float, default=1)
    p.add_argument("--period", type=float, default=5.0)
    p = sub.add_parser("render-selftest")
    p.add_argument("--frames", type=int, default=300)
    p.add_argument("--out", default="captures/selftest")
    args = ap.parse_args()
    if args.cmd == "counter":
        run_display("counter", args.minutes, 0)
    elif args.cmd == "flash":
        run_display("flash", args.minutes, args.period)
    else:
        render_selftest(args.frames, REPO / args.out)


if __name__ == "__main__":
    main()
