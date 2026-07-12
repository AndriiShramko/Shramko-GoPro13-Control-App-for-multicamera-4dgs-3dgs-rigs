"""E8 — Labs `!S` delayed start: ПРЕЦИЗИЯ на ОДНОЙ камере (60fps).

ЧЕСТНЫЕ ГРАНИЦЫ ТЕСТА (важно, отвечает на скепсис Андрия):
- Тест меряет, насколько ВОСПРОИЗВОДИМО камера начинает запись по команде `!<time>S`
  (старт по значению её внутренних часов), относительно стенд-часов хоста.
- Это ПРЕЦИЗИЯ механизма старта, НЕ синхронизация между камерами (нужна 2-я камера, Ф1)
  и НЕ внутрикадровая фаза (её `!S` НЕ даёт в принципе — фаза сенсора free-running).
- Смысл: если разброс старта `!S` заметно МЕНЬШЕ, чем у HTTP-триггера (E1 σ=17.9мс),
  значит на 100 камер `!S`+синхронные часы дадут старт кучнее → меньше обрезки на монтаже.
  Если НЕ меньше — `!S` не лучше простого одновременного HTTP-триггера, и это тоже ответ.

Метод: для каждого дубля
  1. QR precision-time (выставить часы камеры от хоста).
  2. QR `!<T>S`, где T = целая секунда через ~6 с (старт по часам камеры в момент T).
  3. Стенд-counter логирует host-time каждого своего кадра.
  4. Запись скачивается, декодится первый распознанный stand_idx → host-time первого кадра.
  5. Ошибка = host_time(первый кадр) − T_target. Разброс ошибки по N дублям = прецизия.

Требует: камера смотрит на монитор со стендом (counter в одной зоне + QR по команде).
Usage: python src/exp_delayed_start.py --takes 10
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
import pygame  # noqa: E402
import qrcode  # noqa: E402

from wired_gopro import WiredGoPro, discover_camera_ips  # noqa: E402
from decode_stand import decode_frame  # noqa: E402
from stand import StandRenderer  # noqa: E402


def show_qr(surf, payload, seconds):
    w, h = surf.get_size()
    qr = qrcode.QRCode(border=2, box_size=1)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image().convert("RGB")
    n = qr.modules_count
    scale = max(6, (min(w, h) * 2 // 3) // n)
    size = n * scale
    qs = pygame.transform.scale(
        pygame.image.frombuffer(img.tobytes(), img.size, "RGB"), (size, size))
    surf.fill((255, 255, 255))
    surf.blit(qs, ((w - size) // 2, (h - size) // 2))
    pygame.display.flip()
    time.sleep(seconds)


def first_decoded(video: Path, max_frames: int = 150):
    cap = cv2.VideoCapture(str(video))
    loc = None
    n = 0
    try:
        while n < max_frames:
            ret, img = cap.read()
            if not ret:
                break
            out = decode_frame(img, loc)
            loc = out["loc"] or loc
            if out["idx"] is not None:
                return out["idx"]
            n += 1
    finally:
        cap.release()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", type=int, default=10)
    ap.add_argument("--tz", type=int, default=120)
    args = ap.parse_args()

    ips = discover_camera_ips()
    if not ips:
        sys.exit("camera not found")
    cam = WiredGoPro(ips[0], REPO / "docs" / "session-logs" /
                     f"exp08-{dt.date.today():%Y%m%d}.jsonl")
    cam.enable_wired_control()
    cam.start_keep_alive()

    take_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_exp08-delayed-start"
    take_dir.mkdir(parents=True)
    (take_dir / "meta.json").write_text(json.dumps(
        {"experiment": "exp08-delayed-start", "info": cam.info(),
         "state": cam.state()}, indent=2), encoding="utf-8")

    pygame.init()
    surf = pygame.display.set_mode((0, 0), pygame.FULLSCREEN, vsync=1)
    renderer = StandRenderer(surf.get_size())
    stand_log = (REPO / "docs" / "session-logs" /
                 f"stand-counter-exp08-{dt.datetime.now():%Y%m%d_%H%M%S}.jsonl")
    fh = stand_log.open("a", encoding="utf-8")
    wall0, perf0 = time.time(), time.perf_counter_ns()
    fh.write(json.dumps({"mode": "counter", "wall_utc":
             dt.datetime.now(dt.timezone.utc).isoformat(), "perf_ns": perf0}) + "\n")
    frame_idx = 0

    def render_counter_until(deadline_perf):
        nonlocal frame_idx
        while time.perf_counter_ns() < deadline_perf:
            for ev in pygame.event.get():
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    return False
            renderer.draw(surf, frame_idx, False)
            b = time.perf_counter_ns()
            pygame.display.flip()
            fh.write(json.dumps({"i": frame_idx, "f": 0, "b": b,
                                 "a": time.perf_counter_ns()}) + "\n")
            frame_idx += 1
        return True

    rows = []
    for i in range(args.takes):
        # 1. sync clock from host
        now = dt.datetime.now()
        show_qr(surf, f"oT{now:%y%m%d%H%M%S}.{now.microsecond//1000:03d}"
                      f"oTD0oTZ{args.tz}", 10)
        # 2. schedule start at absolute clock second T (~7 s ahead)
        target = (dt.datetime.now() + dt.timedelta(seconds=7)).replace(microsecond=0)
        show_qr(surf, f"!{target:%H:%M:%S}S", 3)
        # 3. render the counter through the whole record window so the recorded
        # frames carry decodable stand indices
        deadline = time.perf_counter_ns() + int(
            (target + dt.timedelta(seconds=2.5) - dt.datetime.now()).total_seconds() * 1e9)
        render_counter_until(deadline)
        cam.shutter_stop()
        for _ in range(40):
            if not cam.flags()["encoding"]:
                break
            time.sleep(0.25)
        last = cam.last_captured()
        dest = take_dir / f"t{i:02d}_{last['file']}"
        try:
            cam.download(last["folder"], last["file"], dest)
            cam.delete_file(last["folder"], last["file"])
            idx = first_decoded(dest)
        except Exception as exc:  # noqa: BLE001
            idx = None
            print(f"take {i}: download/decode failed: {exc}")
        rows.append({"take": i, "target_wall": target.isoformat(),
                     "target_epoch": target.timestamp(),
                     "file": dest.name, "first_stand_idx": idx})
        print(f"take {i}: target={target:%H:%M:%S} first_idx={idx} file={dest.name}")
        time.sleep(2)
    cam.stop_keep_alive()
    fh.close()
    pygame.quit()

    # join: stand idx -> host perf_ns (our own log) -> wall time
    idx2ns = {}
    for line in stand_log.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if "i" in rec:
            idx2ns[rec["i"]] = rec["b"]

    errs = []
    for r in rows:
        idx = r["first_stand_idx"]
        if idx is None or idx not in idx2ns:
            continue
        frame_wall = wall0 + (idx2ns[idx] - perf0) / 1e9
        err_ms = (frame_wall - r["target_epoch"]) * 1000
        r["first_frame_err_ms"] = err_ms
        errs.append(err_ms)

    out_dir = REPO / "docs" / "experiments" / "exp08-delayed-start"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"n_takes": len(rows), "n_measured": len(errs)}
    if errs:
        summary.update({
            "mean_err_ms": statistics.mean(errs),
            "stdev_err_ms": statistics.stdev(errs) if len(errs) > 1 else 0,
            "min_err_ms": min(errs), "max_err_ms": max(errs),
            "note": "mean = constant offset (QR/scan/display latency); STDEV is the "
                    "reproducibility of !S start on ONE camera. Compare stdev to "
                    "E1 HTTP-trigger sigma=17.9ms. NOT inter-camera sync, NOT sub-frame."})
    (out_dir / f"delayed-start-{dt.datetime.now():%Y%m%d_%H%M%S}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
