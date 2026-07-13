"""Авто-вахта канала: раз в 30 мин пробный клип + декод; когда канал
оживёт (стемнеет) — автоматически запускает E15 (rejection-to-target).
"""
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
sys.path.insert(0, str(REPO / "src"))

import cv2  # noqa: E402

import decode_stand as D  # noqa: E402
from wired_gopro import WiredGoPro  # noqa: E402

IP = "172.25.139.51"


def channel_ok() -> bool:
    try:
        cam = WiredGoPro(IP)
        cam.enable_wired_control()
        cam.start_keep_alive()
        stand = subprocess.Popen(
            [PY, str(REPO / "src" / "stand.py"), "counter", "--minutes", "0.4"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(8)
        cam.wait_idle()
        cam.shutter_start()
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 8:
            try:
                if cam.flags()["encoding"]:
                    break
            except Exception:
                pass
            time.sleep(0.1)
        time.sleep(2)
        for _ in range(5):
            try:
                cam.shutter_stop()
                break
            except Exception:
                time.sleep(1.5)
        cam.wait_idle()
        lc = cam.last_captured()
        dest = REPO / "captures" / "channel-watch" / lc["file"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        cam.download(lc["folder"], lc["file"], dest)
        cam.delete_file(lc["folder"], lc["file"])
        cam.stop_keep_alive()
        stand.wait(timeout=60)
        cap = cv2.VideoCapture(str(dest))
        loc = None
        dec = 0
        tot = 0
        n = 0
        while n < 90:
            ret, img = cap.read()
            if not ret:
                break
            if n % 3 == 0:
                out = D.decode_frame(img, loc)
                loc = out["loc"] or loc
                tot += 1
                if out["idx"] is not None:
                    dec += 1
            n += 1
        cap.release()
        dest.unlink(missing_ok=True)
        rate = dec / max(1, tot)
        print(f"{time.strftime('%H:%M')} channel probe: {dec}/{tot}", flush=True)
        return rate >= 0.3
    except Exception as exc:
        print(f"{time.strftime('%H:%M')} probe error: {type(exc).__name__}", flush=True)
        return False


def main():
    while True:
        if channel_ok():
            print("CHANNEL OK -> starting E15", flush=True)
            subprocess.run([PY, str(REPO / "src" / "exp_rejection_target.py")])
            print("E15 FINISHED", flush=True)
            return
        time.sleep(1800)


if __name__ == "__main__":
    main()
