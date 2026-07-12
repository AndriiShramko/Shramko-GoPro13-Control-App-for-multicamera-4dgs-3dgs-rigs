"""Пере-анализ записанных E9-дублей: sample_every=1, порог 20 точек.
Клипы не перезаписываются — только декод и фит.
"""
import glob
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

import cv2
import numpy as np

from exp_phase_determinism import load_stand, take_pairs

take_dir = Path(sorted(glob.glob(str(REPO / "captures" / "*E9-nocycle")))[-1])
meta = json.loads((take_dir / "meta.json").read_text(encoding="utf-8"))
idx2ns, _, _ = load_stand(Path(meta["stand_log"]))
fps = meta["fps"]
period_ns = 1e9 / fps

results = []
for clip in sorted(take_dir.glob("t*.MP4")):
    xs, ys = take_pairs(clip, idx2ns, sample_every=1)
    row = {"file": clip.name, "n": len(xs)}
    if len(xs) >= 20:
        A = np.vstack([np.ones_like(xs), xs]).T
        coef, *_ = np.linalg.lstsq(A, ys, rcond=None)
        a_ns = coef[0] * 1e9
        resid = ys - A @ coef
        row["phase_ms"] = float((a_ns % period_ns) / 1e6)
        row["resid_rms_ms"] = float(np.sqrt(np.mean(resid**2)) * 1000)
    results.append(row)
    print(row)

phases = [r["phase_ms"] for r in results if "phase_ms" in r]
out = {"n_measured": len(phases), "period_ms": period_ns / 1e6,
       "phases_ms": [round(p, 2) for p in phases]}
if len(phases) >= 3:
    pm = np.array(phases)
    ang = pm / (period_ns / 1e6) * 2 * np.pi
    R = np.sqrt(np.mean(np.cos(ang))**2 + np.mean(np.sin(ang))**2)
    circ = np.sqrt(-2 * np.log(R)) / (2 * np.pi) * (period_ns / 1e6) if R > 0 else None
    out["phase_circular_stdev_ms"] = round(float(circ), 3) if circ else None
    out["uniform_random_would_be_ms"] = round((period_ns / 1e6) / 12**0.5, 2)
    out["verdict"] = ("ДЕТЕРМИНИРОВАНА" if circ and circ < 2.0 else "ПЛАВАЕТ")
p = take_dir / "reanalysis.json"
p.write_text(json.dumps({"summary": out, "takes": results}, indent=2,
                        ensure_ascii=False), encoding="utf-8")
print(json.dumps(out, indent=2, ensure_ascii=False))
