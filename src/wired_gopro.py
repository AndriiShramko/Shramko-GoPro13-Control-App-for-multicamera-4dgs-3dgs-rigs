"""Thin Open GoPro HTTP-over-USB client (stock firmware, HERO13).

Deliberately requests-only (no open-gopro SDK dependency) so it doubles as a
diagnostic tool. Every camera call is logged with both wall-clock and
QueryPerformanceCounter-grade timestamps (time.perf_counter_ns) because this
project's experiments (start-latency jitter, drift) need a stable host
timebase, not just responses.

Endpoints per Open GoPro HTTP API 2.0 (https://gopro.github.io/OpenGoPro/http/),
snapshot in docs/api/. Camera IP over USB is the host adapter's gateway
(172.2X.1YZ.51 where XYZ = last 3 digits of serial); we discover it from
`ipconfig /all` instead of deriving from the serial — more robust.
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

KEEP_ALIVE_PERIOD_S = 3.0  # community figure; validated empirically in E0 logs
HTTP_TIMEOUT_S = 5.0
# HERO13 status IDs we rely on (Open GoPro "state" statuses; verified on camera in E0):
STATUS_SYSTEM_HOT = "6"
STATUS_SYSTEM_BUSY = "8"
STATUS_ENCODING = "10"
STATUS_INTERNAL_BATTERY_PERCENT = "70"
STATUS_SD_STATUS = "33"


def _now() -> dict:
    return {"wall": time.time(), "perf_ns": time.perf_counter_ns()}


@dataclass
class CallLog:
    path: Path
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def write(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


def discover_camera_ips() -> list[str]:
    """Find GoPro IPs over USB NCM. Two clues, verified by a live HTTP probe:
    (a) adapter default gateway ending .51 (GoPro's fixed self-address), and
    (b) — Windows often omits the gateway for NCM — any host adapter address in
    172.16-31/12: the camera then sits at x.y.z.51 of the same subnet."""
    out = subprocess.run(
        ["ipconfig", "/all"], capture_output=True, text=True, check=True
    ).stdout
    candidates: list[str] = []
    for m in re.finditer(r"(Default Gateway|IPv4 Address)[ .:]*(\d+\.\d+\.\d+\.\d+)", out):
        ip = m.group(2)
        parts = ip.split(".")
        first, second = int(parts[0]), int(parts[1])
        if not (first == 172 and 16 <= second <= 31):
            continue
        cand = ip if ip.endswith(".51") else ".".join(parts[:3]) + ".51"
        if cand not in candidates:
            candidates.append(cand)
    verified = []
    for cand in candidates:
        try:
            r = requests.get(f"http://{cand}:8080/gopro/camera/info", timeout=2)
            if r.status_code == 200 and "model_name" in r.text:
                verified.append(cand)
        except requests.RequestException:
            continue
    return verified


class WiredGoPro:
    def __init__(self, ip: str, log_path: Path | None = None):
        self.ip = ip
        self.base = f"http://{ip}:8080"
        self.session = requests.Session()
        self.log = CallLog(log_path) if log_path else None
        self._keep_alive_stop = threading.Event()
        self._keep_alive_thread: threading.Thread | None = None

    # -- core ----------------------------------------------------------------
    def get(self, path: str, timeout: float = HTTP_TIMEOUT_S, log_body: bool = False):
        t0 = _now()
        url = self.base + path
        try:
            resp = self.session.get(url, timeout=timeout)
            t1 = _now()
            rec = {
                "path": path, "code": resp.status_code,
                "t_send": t0, "t_recv": t1,
                "rtt_ms": (t1["perf_ns"] - t0["perf_ns"]) / 1e6,
            }
            if log_body and len(resp.content) < 4096:
                rec["body"] = resp.text
            if self.log:
                self.log.write(rec)
            return resp
        except requests.RequestException as exc:
            if self.log:
                self.log.write({"path": path, "error": repr(exc), "t_send": t0})
            raise

    # -- lifecycle -----------------------------------------------------------
    def enable_wired_control(self):
        """Old-repo verified sequence: off -> 2 s -> on, then ~1 s to stabilize.
        HTTP 500 here means 'already in that state' and counts as success."""
        try:
            self.get("/gopro/camera/control/wired_usb?p=0")
        except requests.RequestException:
            pass
        time.sleep(2.0)
        resp = self.get("/gopro/camera/control/wired_usb?p=1")
        if resp.status_code not in (200, 500):
            raise RuntimeError(f"wired_usb enable failed: {resp.status_code}")
        time.sleep(1.0)
        return resp

    def start_keep_alive(self):
        # DEDICATED session: sharing self.session with the main thread corrupts
        # concurrent downloads (requests.Session is NOT thread-safe) -> silently
        # truncated media (found 2026-07-12: 4K60 clip arriving 1.7MB vs 37MB).
        ka_session = requests.Session()

        def loop():
            while not self._keep_alive_stop.wait(KEEP_ALIVE_PERIOD_S):
                try:
                    ka_session.get(f"{self.base}/gopro/camera/keep_alive", timeout=4)
                except requests.RequestException:
                    pass  # next tick retries

        self._keep_alive_stop.clear()
        self._keep_alive_thread = threading.Thread(target=loop, daemon=True)
        self._keep_alive_thread.start()

    def stop_keep_alive(self):
        self._keep_alive_stop.set()

    # -- info / state ---------------------------------------------------------
    def info(self) -> dict:
        return self.get("/gopro/camera/info", log_body=True).json()

    def state(self) -> dict:
        return self.get("/gopro/camera/state").json()

    def flags(self) -> dict:
        st = self.state().get("status", {})
        return {
            "hot": bool(st.get(STATUS_SYSTEM_HOT, 0)),
            "busy": bool(st.get(STATUS_SYSTEM_BUSY, 0)),
            "encoding": bool(st.get(STATUS_ENCODING, 0)),
            "battery_pct": st.get(STATUS_INTERNAL_BATTERY_PERCENT),
        }

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Wait for busy AND encoding flags to clear (camera finalizes clips
        after stop; media_list returns 'Camera is busy' until then)."""
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            f = self.flags()
            if not f["busy"] and not f["encoding"]:
                return True
            time.sleep(0.3)
        return False

    def require_cool_and_idle(self):
        f = self.flags()
        if f["hot"]:
            raise RuntimeError(f"camera reports System Hot: {f}")
        if f["busy"] or f["encoding"]:
            self.wait_idle(10)
        return self.flags()

    # -- recording -----------------------------------------------------------
    def shutter_start(self):
        return self.get("/gopro/camera/shutter/start")

    def shutter_stop(self):
        """503 = camera still finalizing; old repo's proven recipe: 3 retries, 1 s."""
        for attempt in range(4):
            resp = self.get("/gopro/camera/shutter/stop")
            if resp.status_code != 503:
                return resp
            time.sleep(1.0)
        return resp

    # -- settings ------------------------------------------------------------
    def set_setting(self, setting_id: int, option_id: int):
        return self.get(f"/gopro/camera/setting?setting={setting_id}&option={option_id}")

    def lock_exposure(self, shutter_opt: int = 22, iso_opt: int = 8,
                      wb_opt: int = 12) -> dict:
        """Full manual exposure via UNdocumented-but-working Protune IDs
        (verified on HERO13, OpenGoPro#903). Defaults: shutter 1/480 (opt 22),
        ISO 100 (opt 8, min==max), WB 5000K (opt 12). Prereqs included:
        Control Mode=Pro (175=1), Anti-Flicker 60Hz (134=2). No dedicated
        exposure-lock ID exists — shutter+ISO+WB pinned IS the lock.
        Returns {setting_id: http_status}."""
        seq = [(175, 1), (134, 2), (145, shutter_opt),
               (102, iso_opt), (13, iso_opt), (115, wb_opt)]
        out = {}
        for sid, opt in seq:
            out[sid] = self.set_setting(sid, opt).status_code
            time.sleep(0.8)
        return out

    # -- media ---------------------------------------------------------------
    def media_list(self) -> dict:
        return self.get("/gopro/media/list").json()

    def last_captured(self) -> dict:
        return self.get("/gopro/media/last_captured").json()

    def expected_size(self, directory: str, filename: str) -> int | None:
        """Size the camera reports for a file (from media list) — the download
        target to verify against (flaky USB truncates downloads silently)."""
        for m in self.media_list().get("media", []):
            if m.get("d") != directory:
                continue
            for f in m.get("fs", []):
                if f.get("n") == filename:
                    return int(f.get("s", 0)) or None
        return None

    def download(self, directory: str, filename: str, dest: Path,
                 chunk: int = 1 << 20, retries: int = 3) -> Path:
        """Download with size verification + retry. A degraded USB connection
        (observed after hours of use) silently truncates the stream, so we
        compare against the camera-reported size and retry (found: 4K60 clips
        arriving as 1.7MB instead of ~100MB)."""
        url = f"{self.base}/videos/DCIM/{directory}/{filename}"
        want = self.expected_size(directory, filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_got = -1
        for attempt in range(retries):
            t0 = _now()
            try:
                with self.session.get(url, stream=True, timeout=(10, 60)) as resp:
                    resp.raise_for_status()
                    with dest.open("wb") as fh:
                        for part in resp.iter_content(chunk):
                            fh.write(part)
            except requests.RequestException as exc:
                if self.log:
                    self.log.write({"download_error": f"{directory}/{filename}",
                                    "attempt": attempt, "error": repr(exc)})
                time.sleep(2)
                continue
            got = dest.stat().st_size
            last_got = got
            ok = want is None or got >= want * 0.999
            if self.log:
                self.log.write({"download": f"{directory}/{filename}",
                                "bytes": got, "expected": want, "ok": ok,
                                "attempt": attempt, "t_send": t0, "t_done": _now()})
            if ok:
                return dest
            time.sleep(2)  # truncated — retry
        raise RuntimeError(f"download truncated after {retries} tries: "
                           f"got {last_got} of {want} bytes ({directory}/{filename})")

    def gpmf(self, directory: str, filename: str) -> bytes:
        resp = self.get(f"/gopro/media/gpmf?path={directory}/{filename}", timeout=30)
        resp.raise_for_status()
        return resp.content

    def delete_file(self, directory: str, filename: str):
        # Open GoPro media delete endpoint; verified against docs snapshot in docs/api/
        return self.get(f"/gopro/media/delete/file?path={directory}/{filename}")
