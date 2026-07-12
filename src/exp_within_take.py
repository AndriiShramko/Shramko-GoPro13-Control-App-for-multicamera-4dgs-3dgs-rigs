"""E12 — стабильность фазы ВНУТРИ одной непрерывной записи.

Если фаза рандомится только НА СТАРТЕ, то внутри записи она должна быть
стабильной (уход только на дрейф ppm ~0.9 мс/мин). Проверка: одна запись 60с,
окно-фиты по 10с: интерцепт каждого окна mod период. Критерий:
- окно-фазы образуют гладкую линию с наклоном ~дрейф → СТАБИЛЬНА;
- скачки >2 мс между окнами → фаза нестабильна даже внутри записи (всё хуже).
"""
import datetime as dt
import glob
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import cv2
import numpy as np

from wired_gopro import WiredGoPro, discover_camera_ips
from exp_phase_determinism import load_stand
from decode_stand import decode_frame

SECONDS = 60
WIN_S = 10.0

logs = sorted([f for f in glob.glob(str(REPO / "docs" / "session-logs" / "stand-counter-*.jsonl"))
               if "exp" not in Path(f).name])
stand_log = Path(logs[-1])

ips = discover_camera_ips()
cam = WiredGoPro(ips[0], REPO / "docs" / "session-logs" / f"exp12-{dt.date.today():%Y%m%d}.jsonl")
cam.enable_wired_control()
cam.start_keep_alive()

# mode enforcement (та же процедура, что в E9)
WANT = {"2": 108, "234": 5, "3": 5}
cam.get("/gopro/camera/presets/set_group?id=1000", timeout=12); time.sleep(2)
cam.get("/gopro/camera/presets/load?id=0", timeout=12); time.sleep(2)
for sid, opt in WANT.items():
    cam.set_setting(int(sid), opt); time.sleep(1.2)
st = cam.state()["settings"]
got = {k: st.get(k) for k in WANT}
assert got == WANT, f"MODE VERIFY FAILED: {got}"
print("mode verified 4K8:7@60")

cam.wait_idle()
cam.shutter_start()
t0 = time.perf_counter_ns()
while time.perf_counter_ns() - t0 < 10e9:
    if cam.flags()["encoding"]:
        break
    time.sleep(0.05)
# следим за encoding РЕДКО и толерантно: в 4K8:7@60 камера под нагрузкой
# рвёт HTTP (RemoteDisconnected) — это не повод валить запись
t_rec0 = time.perf_counter()
stopped_early = None
while time.perf_counter() - t_rec0 < SECONDS:
    time.sleep(10.0)
    try:
        if not cam.flags()["encoding"]:
            stopped_early = time.perf_counter() - t_rec0
            print(f"!! encoding dropped by itself at {stopped_early:.1f}s")
            break
    except Exception as exc:  # HTTP хрупок при активной записи — глотаем
        print(f"  poll error at {time.perf_counter()-t_rec0:.0f}s: {type(exc).__name__} (ok)")
for attempt in range(10):
    try:
        cam.shutter_stop()
        break
    except Exception:
        time.sleep(1.5)
cam.wait_idle()
last = cam.last_captured()
dest = REPO / "captures" / f"{dt.datetime.now():%Y%m%d_%H%M%S}_E12" / last["file"]
cam.download(last["folder"], last["file"], dest)
cam.delete_file(last["folder"], last["file"])
cam.stop_keep_alive()
print("clip:", dest)

idx2ns, _, _ = load_stand(stand_log)
cap = cv2.VideoCapture(str(dest))
fps = cap.get(cv2.CAP_PROP_FPS)
period_ms = 1000.0 / fps
loc = None
xs, ys = [], []
n = 0
while True:
    ret, img = cap.read()
    if not ret:
        break
    if n % 3 == 0:
        pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        out = decode_frame(img, loc)
        loc = out["loc"] or loc
        if out["idx"] is not None and out["idx"] in idx2ns:
            xs.append(pts)
            ys.append(idx2ns[out["idx"]] / 1e9)
    n += 1
cap.release()
xs = np.array(xs); ys = np.array(ys)
print(f"fps={fps:.3f}, points={len(xs)} of ~{n//3}")

wins = []
for w0 in np.arange(0, SECONDS, WIN_S):
    m = (xs >= w0) & (xs < w0 + WIN_S)
    if m.sum() < 12:
        wins.append({"t": float(w0), "n": int(m.sum())})
        continue
    A = np.vstack([np.ones(m.sum()), xs[m]]).T
    coef, *_ = np.linalg.lstsq(A, ys[m], rcond=None)
    resid = ys[m] - A @ coef
    # интерцепт, экстраполированный к общему опорному x=0
    ph = float((coef[0] * 1000.0) % period_ms)
    wins.append({"t": float(w0), "n": int(m.sum()), "phase_ms": round(ph, 3),
                 "slope_ppm": round((coef[1] - 1) * 1e6, 1),
                 "resid_rms_ms": round(float(np.sqrt(np.mean(resid**2)) * 1000), 2)})
    print(wins[-1])

ph = [w["phase_ms"] for w in wins if "phase_ms" in w]
out = {"fps": fps, "windows": wins, "n_windows": len(ph)}
if len(ph) >= 3:
    diffs = np.diff(ph)
    # циркулярная развёртка скачков
    diffs = (diffs + period_ms / 2) % period_ms - period_ms / 2
    out["max_jump_ms"] = round(float(np.max(np.abs(diffs))), 3)
    out["verdict"] = ("СТАБИЛЬНА внутри записи (уход только дрейф)"
                      if np.max(np.abs(diffs)) < 2.0 else "НЕСТАБИЛЬНА (скачки внутри записи)")
p = REPO / "docs" / "experiments" / "exp12-within-take" / f"result-{dt.datetime.now():%Y%m%d_%H%M%S}.json"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps({k: v for k, v in out.items() if k != "windows"}, indent=2, ensure_ascii=False))
