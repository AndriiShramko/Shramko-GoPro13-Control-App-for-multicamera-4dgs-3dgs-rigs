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
        # the STRIP alone uses 92% of screen width: at operator's camera
        # distance 60%-zone cells washed out optically (grey-zone gaps lost)
        strip_w = int(self.w * 0.92)
        self.strip_x0 = (self.w - strip_w) // 2
        self.cell = max(8, strip_w // N_CELLS)
        # strip at 70% height: daylight glare hits the panel around 45-55%
        # and fuses data cells with the bright spot in EVERY frame
        # (2026-07-13); the lower third is dark. Was 55% (framing probe).
        self.strip_y = self.y0 + int(zone_h * 0.70)
        # E7 multi-row mode: same strip at several heights; a camera frame
        # exposed during the monitor's top-to-bottom scanout shows a SEAM
        # (upper rows = index N, lower rows = N-1) — seam position = sub-frame
        # phase of the exposure relative to the refresh (phase-control-research)
        self.row_ys = [self.y0 + int(zone_h * f) for f in (0.08, 0.28, 0.48, 0.68, 0.88)]
        self.bar_y = self.y0 + int(zone_h * 0.75)
        self.bar_h = max(10, zone_h // 20)

    def _draw_strip(self, surf, bits, y):
        import pygame

        # Gap must survive the camera optics: 2px on screen shrinks to <1px
        # in-frame and cells fuse (runs of 4.6 cells, Manchester dies —
        # 2026-07-13). cell//6 keeps ~2.5px in-frame at 2048 decode width.
        gap = max(4, self.cell // 6)
        for i, b in enumerate(bits):
            color = (255, 255, 255) if b else (30, 30, 30)
            x = self.strip_x0 + i * self.cell
            # 2x-tall cells: GoPro fisheye bows the strip by ~a cell
            pygame.draw.rect(surf, color,
                             (x, y, self.cell - gap, self.cell * 2))

    def draw(self, surf, frame_idx: int, flash: bool, font=None,
             extra_text: str = "", rows: bool = False):
        import pygame

        if flash:
            surf.fill((255, 255, 255))
            return
        surf.fill((0, 0, 0))
        bits = encode_cells(frame_idx)
        clock = [1 - (i % 2) for i in range(len(bits))]  # 1010... local grid:
        # fisheye makes cell pitch vary 0.5-1.3x along the strip; the clock
        # row lets the decoder read the grid POSITION at every cell instead
        # of assuming a uniform pitch (2026-07-13)
        if rows:
            for y in self.row_ys:
                self._draw_strip(surf, bits, y)
        else:
            self._draw_strip(surf, bits, self.strip_y)
            # 3.6*cell row spacing: at 2.6 the data and clock cells fused
            # vertically through G2G blur (L-shaped blobs fell in the dy gap
            # between clusters and vanished from both rows, 2026-07-13)
            self._draw_strip(surf, clock, self.strip_y + int(self.cell * 3.6))
        # NO moving bar: at strip_y=70% it flew straight through the clock
        # row (bar_y=75%) leaving moving junk blobs (2026-07-13)
        if font is not None:
            txt = f"{frame_idx}  {extra_text}"
            surf.blit(font.render(txt, True, (200, 200, 200)),
                      (self.x0, self.strip_y + self.cell * 2 + 24))


def _current_refresh_hz() -> float:
    """Actual panel refresh from Windows (EnumDisplaySettings), not pygame."""
    class DEVMODE(ctypes.Structure):
        _fields_ = [("dmDeviceName", ctypes.c_wchar * 32)] + \
            [(n, ctypes.c_uint16) for n in
             ("dmSpecVersion", "dmDriverVersion", "dmSize", "dmDriverExtra")] + \
            [("dmFields", ctypes.c_uint32), ("dmPositionX", ctypes.c_long),
             ("dmPositionY", ctypes.c_long)] + \
            [(n, ctypes.c_uint32) for n in
             ("dmDisplayOrientation", "dmDisplayFixedOutput")] + \
            [(n, ctypes.c_short) for n in
             ("dmColor", "dmDuplex", "dmYResolution", "dmTTOption", "dmCollate")] + \
            [("dmFormName", ctypes.c_wchar * 32), ("dmLogPixels", ctypes.c_uint16)] + \
            [(n, ctypes.c_uint32) for n in
             ("dmBitsPerPel", "dmPelsWidth", "dmPelsHeight", "dmDisplayFlags",
              "dmDisplayFrequency", "dmICMMethod", "dmICMIntent", "dmMediaType",
              "dmDitherType", "dmReserved1", "dmReserved2",
              "dmPanningWidth", "dmPanningHeight")]
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    ctypes.windll.user32.EnumDisplaySettingsW(None, -1, ctypes.byref(dm))
    return float(dm.dmDisplayFrequency)


def validate_pacing(intervals_ms: list[float], period_ms: float) -> dict:
    """Pacing gate (audit R0): flips must sit on the vblank grid.
    Skipped vblanks (~2x period) are allowed but counted; chaos is not."""
    import statistics
    # DwmFlush return has ~±1ms jitter around the vblank grid; the frames
    # themselves land on the grid (verified 2026-07-13: 739/750 within ±1.5ms).
    ok_lo, ok_hi = period_ms - 1.5, period_ms + 1.5
    skip_lo, skip_hi = 2 * period_ms - 2.0, 2 * period_ms + 2.0
    n = len(intervals_ms)
    on_grid = sum(1 for d in intervals_ms if ok_lo <= d <= ok_hi)
    skipped = sum(1 for d in intervals_ms if skip_lo <= d <= skip_hi)
    chaos = n - on_grid - skipped
    med = statistics.median(intervals_ms) if intervals_ms else 0
    gate = (abs(med - period_ms) < 0.1) and (chaos / max(1, n) < 0.02)
    return {"n": n, "median_ms": round(med, 3), "on_grid": on_grid,
            "skipped_vblank": skipped, "chaos": chaos,
            "chaos_frac": round(chaos / max(1, n), 4), "PACING_GATE": gate}


def run_display(mode: str, minutes: float, flash_period_s: float):
    import pygame

    keep_display_awake()
    # ms-precise sleeps + priority (audit fix A3)
    ctypes.windll.winmm.timeBeginPeriod(1)
    ctypes.windll.kernel32.SetPriorityClass(
        ctypes.windll.kernel32.GetCurrentProcess(), 0x00000080)  # HIGH_PRIORITY
    hz = _current_refresh_hz()
    period_ms = 1000.0 / hz
    pygame.init()
    surf = pygame.display.set_mode((0, 0),
                                   pygame.FULLSCREEN | pygame.DOUBLEBUF,
                                   vsync=1)
    renderer = StandRenderer(surf.get_size())
    log = (REPO / "docs" / "session-logs" /
           f"stand-{mode}-{dt.datetime.now():%Y%m%d_%H%M%S}.jsonl")
    log.parent.mkdir(parents=True, exist_ok=True)
    t_end = time.perf_counter_ns() + int(minutes * 60 * 1e9)
    frame_idx = 0
    session_header = {
        "mode": mode, "screen": surf.get_size(), "cell_px": renderer.cell,
        "wall_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "perf_ns": time.perf_counter_ns(), "flash_period_s": flash_period_s,
        "panel_hz": hz, "period_ms": period_ms,
        "warmup_s": 5.0,
    }
    # DwmFlush blocks until next composition -> real vblank pacing under DWM
    # (audit fix B4: bare flip() only queues, it does not block)
    dwm_flush = ctypes.windll.dwmapi.DwmFlush
    # Log: header immediately (so experiments can discover the live log),
    # rows flushed in 3s batches — one buffered write per ~90 frames is
    # harmless; what killed pacing was dumps+write+font EVERY frame.
    fh = log.open("a", encoding="utf-8", buffering=1 << 20)
    fh.write(json.dumps(session_header) + "\n")
    fh.flush()
    rows_buf: list[str] = []
    all_a: list[tuple[int, int]] = []  # (a_ns, warmup) for final pacing verdict
    next_flash_ns = time.perf_counter_ns() + int(flash_period_s * 1e9)
    t_start = time.perf_counter_ns()
    try:
        while time.perf_counter_ns() < t_end:
            for ev in pygame.event.get():
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    t_end = 0
            flash = False
            if mode == "flash" and time.perf_counter_ns() >= next_flash_ns:
                flash = True
                next_flash_ns += int(flash_period_s * 1e9)
            renderer.draw(surf, frame_idx, flash, None,  # no font in hot loop (B6)
                          rows=(mode == "rows"))
            t_before = time.perf_counter_ns()
            pygame.display.flip()
            dwm_flush()
            t_after = time.perf_counter_ns()
            warm = int(t_after - t_start < 5e9)
            rows_buf.append(json.dumps(
                {"i": frame_idx, "f": int(flash), "b": t_before, "a": t_after,
                 "w": warm}))
            all_a.append((t_after, warm))
            if len(rows_buf) >= 90:  # ~3s batch
                fh.write("\n".join(rows_buf) + "\n")
                fh.flush()
                rows_buf.clear()
            frame_idx += 1
    finally:
        pygame.quit()
        release_display()
        ctypes.windll.winmm.timeEndPeriod(1)
        a_vals = [a for a, w in all_a if not w]
        intervals = [(a_vals[j + 1] - a_vals[j]) / 1e6 for j in range(len(a_vals) - 1)]
        verdict = validate_pacing(intervals, period_ms)
        if rows_buf:
            fh.write("\n".join(rows_buf) + "\n")
        fh.write(json.dumps({"pacing": verdict}) + "\n")
        fh.close()
        print(f"log -> {log}")
        print("PACING:", json.dumps(verdict))


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
    p = sub.add_parser("rows"); p.add_argument("--minutes", type=float, default=5)
    p = sub.add_parser("flash"); p.add_argument("--minutes", type=float, default=1)
    p.add_argument("--period", type=float, default=5.0)
    p = sub.add_parser("render-selftest")
    p.add_argument("--frames", type=int, default=300)
    p.add_argument("--out", default="captures/selftest")
    args = ap.parse_args()
    if args.cmd == "counter":
        run_display("counter", args.minutes, 0)
    elif args.cmd == "rows":
        run_display("rows", args.minutes, 0)
    elif args.cmd == "flash":
        run_display("flash", args.minutes, args.period)
    else:
        render_selftest(args.frames, REPO / args.out)


if __name__ == "__main__":
    main()
