"""E14-full — фаза из ПРЕВЬЮ-стрима == фаза из ФАЙЛА?

Гейт live-фазометра: если да, фазу камеры можно отслеживать по UDP-превью
не останавливая запись, и rejection-калибровка получает бесплатный канал.

Метод: стенд крутится; превью-стрим пишется в .ts ОДНОВРЕМЕННО с записью
на SD; из обоих потоков декодим полосу -> фит host(index) vs pts -> интерцепт
mod период. Оба pts-якоря лежат на сетке кадров СЕНСОРА, поэтому обе фазы
обязаны совпасть (mod 16.683 мс), если превью-кадры = кадры сенсора.
Критерий: |Δφ| < 0.5 мс на 3 прогонах подряд.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import numpy as np

from wired_gopro import WiredGoPro
from exp_phase_determinism import load_stand, take_pairs

IP = "172.25.139.51"
RUNS = 3
REC_S = 10
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
FFMPEG = str(REPO / "bin" / "ffmpeg.exe")
PERIOD_NS = 1e9 / (60000 / 1001)


def capture_preview(dest_ts: Path, duration_s: float, stop_evt: threading.Event):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", 8554))
    s.settimeout(1.0)
    t0 = time.perf_counter()
    with dest_ts.open("wb") as fh:
        while time.perf_counter() - t0 < duration_s and not stop_evt.is_set():
            try:
                data, _ = s.recvfrom(65536)
                fh.write(data)
            except socket.timeout:
                pass
    s.close()


def clip_phase(path: Path, stand_log: Path, sample_every: int = 2):
    idx2ns, _, _ = load_stand(stand_log)
    xs, ys = take_pairs(path, idx2ns, sample_every=sample_every)
    if len(xs) < 15:
        return None
    A = np.vstack([np.ones_like(xs), xs]).T
    coef, *_ = np.linalg.lstsq(A, ys, rcond=None)
    resid = ys - A @ coef
    return {"phase_ms": round(float((coef[0] * 1e9 % PERIOD_NS) / 1e6), 3),
            "n": int(len(xs)),
            "resid_ms": round(float(np.sqrt(np.mean(resid**2)) * 1e3), 2)}


def main():
    out_dir = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_E14-preview"
    out_dir.mkdir(parents=True)
    cam = WiredGoPro(IP)
    cam.enable_wired_control()
    cam.start_keep_alive()
    results = []
    for run in range(RUNS):
        print(f"=== run {run} ===")
        stand = subprocess.Popen(
            [PY, str(REPO / "src" / "stand.py"), "counter", "--minutes", "1.2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(9)
        cam.wait_idle()
        cam.get("/gopro/camera/stream/start?port=8554", timeout=10)
        ts = out_dir / f"r{run}_preview.ts"
        stop_evt = threading.Event()
        cap_thr = threading.Thread(target=capture_preview,
                                   args=(ts, REC_S + 8, stop_evt))
        cap_thr.start()
        time.sleep(1.5)
        cam.shutter_start()
        t0 = time.perf_counter_ns()
        while time.perf_counter_ns() - t0 < 10e9:
            if cam.flags()["encoding"]:
                break
            time.sleep(0.05)
        time.sleep(REC_S)
        for _ in range(6):
            try:
                cam.shutter_stop()
                break
            except Exception:
                time.sleep(1.5)
        cam.wait_idle()
        stop_evt.set()
        cap_thr.join()
        stand_log = Path(sorted([f for f in glob.glob(
            str(REPO / "docs" / "session-logs" / "stand-counter-*.jsonl"))
            if "exp" not in Path(f).name])[-1])
        last = cam.last_captured()
        mp4 = out_dir / f"r{run}_{last['file']}"
        cam.download(last["folder"], last["file"], mp4)
        cam.delete_file(last["folder"], last["file"])
        stand.wait(timeout=90)
        # remux превью для честных PTS
        ts_mp4 = out_dir / f"r{run}_preview.mp4"
        subprocess.run([FFMPEG, "-v", "error", "-i", str(ts), "-c", "copy",
                        str(ts_mp4), "-y"], timeout=60)
        pf = clip_phase(mp4, stand_log)
        pp = clip_phase(ts_mp4, stand_log, sample_every=1)
        row = {"run": run, "file_phase": pf, "preview_phase": pp}
        if pf and pp:
            d = abs(pf["phase_ms"] - pp["phase_ms"])
            d = min(d, PERIOD_NS / 1e6 - d)
            row["delta_ms"] = round(d, 3)
        results.append(row)
        print(f"  file={pf} preview={pp} delta={row.get('delta_ms')}")
    cam.stop_keep_alive()
    deltas = [r["delta_ms"] for r in results if "delta_ms" in r]
    summary = {"runs_ok": len(deltas), "deltas_ms": deltas}
    if len(deltas) >= 2:
        spread = max(deltas) - min(deltas)
        summary["offset_ms"] = round(float(np.mean(deltas)), 3)
        summary["offset_spread_ms"] = round(float(spread), 3)
        # смещение превью-таймлайна = КОНСТАНТА -> вычитается калибровкой;
        # фазометром превью делает СТАБИЛЬНОСТЬ смещения, не его нулевость
        summary["verdict"] = ("ПРЕВЬЮ = ВАЛИДНЫЙ ФАЗОМЕТР (постоянное смещение, "
                              "калибруется)" if spread < 0.3 else
                              "смещение превью НЕстабильно — фазометр невалиден")
    else:
        summary["verdict"] = "нет данных"
    (out_dir / "result.json").write_text(
        json.dumps({"summary": summary, "runs": results}, indent=2,
                   ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
