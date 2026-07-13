"""Доанализ дрейфа по УЖЕ скачанному длинному клипу + стенд-лог.
E19 упал на загрузке 9.5ГБ; клип восстановлен отдельно. Здесь — та же
аналитика (робастный фит + посегментный дрейф), что в E19.

  python src/analyze_drift_clip.py <clip.mp4> [stand_log.jsonl] [--sample 45]
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import cv2
import numpy as np

from exp_phase_determinism import load_stand, take_pairs
from exp_drift_clean import robust_fit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("stand", nargs="?", default=None)
    ap.add_argument("--sample", type=int, default=45)
    args = ap.parse_args()

    clip = Path(args.clip)
    if args.stand:
        stand_log = Path(args.stand)
    else:
        logs = sorted([f for f in glob.glob(str(
            REPO / "docs" / "session-logs" / "stand-counter-*.jsonl"))
            if "exp" not in Path(f).name])
        stand_log = Path(logs[-1])
    print(f"clip: {clip.name} ({clip.stat().st_size/1e9:.2f} ГБ)")
    print(f"stand: {stand_log.name}")

    idx2ns, _, _ = load_stand(stand_log)
    print(f"стенд гейт PASS, {len(idx2ns)} меток, "
          f"idx {min(idx2ns)}..{max(idx2ns)}")

    # take_pairs applies the temporal-model outlier rejection (drops ±stand-period
    # mis-decodes that otherwise poison a long-baseline fit — bug in the first
    # recovery version gave 21.8ms resid / nonsense segments, 2026-07-13)
    pts, host = take_pairs(clip, idx2ns, sample_every=args.sample)
    if len(pts) < 40:
        print(f"decode {len(pts)}<40 — мало"); return
    base = float(pts[-1] - pts[0])
    print(f"декодировано пар: {len(pts)}, база {base:.0f}с ({base/60:.1f} мин)")

    a, b, rms, r2 = robust_fit(pts, host)
    drift_ppm = (a - 1.0) * 1e6
    resolvable = rms * 1e3 / base * 1e3  # ppm floor from residual over baseline

    seg_len = 300.0
    segs = []
    t_lo = pts[0]
    while t_lo < pts[-1] - 60:
        m = (pts >= t_lo) & (pts < t_lo + seg_len)
        if m.sum() >= 15:
            sa = robust_fit(pts[m], host[m])[0]
            segs.append({"t_min": round(float(t_lo - pts[0]) / 60, 1),
                         "drift_ppm": round((sa - 1) * 1e6, 2), "n": int(m.sum())})
        t_lo += seg_len
    spread = (round(max(s["drift_ppm"] for s in segs)
                    - min(s["drift_ppm"] for s in segs), 2)
              if len(segs) >= 2 else None)

    out = {"clip": clip.name, "stand": stand_log.name,
           "baseline_s": round(base, 1), "n_pairs": int(len(pts)),
           "drift_ppm_full": round(drift_ppm, 3), "us_per_s": round(drift_ppm, 3),
           "resid_rms_ms": round(rms * 1e3, 3), "r2": round(r2, 7),
           "resolvable_ppm": round(resolvable, 2),
           "segments_5min": segs, "seg_spread_ppm": spread,
           "window_hold_s_1ms": round(1000.0 / abs(drift_ppm), 1) if drift_ppm else None,
           "linear": (spread is None or spread < 3),
           }
    p = clip.parent / "drift_result.json"
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "segments_5min"},
                     indent=2, ensure_ascii=False))
    print("СЕГМЕНТЫ (мин, ppm):", [(s["t_min"], s["drift_ppm"]) for s in segs])


if __name__ == "__main__":
    main()
