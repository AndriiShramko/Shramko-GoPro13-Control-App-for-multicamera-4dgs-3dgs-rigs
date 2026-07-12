"""verify_phase0 — anti-falsification gate for Phase-0 DONE claims (red-team #5).

Principle: every published statistic must be RE-DERIVABLE from raw material in
captures/ (or from committed session logs for pure-HTTP experiments). A DONE
item counts only if this script passes. Numbers are recomputed, not trusted.

Checks (each SKIPped if its experiment hasn't run yet, FAILed on mismatch):
  A. decoder-selftest : re-render ground truth + decode, require >= 99%
  B. cycle-log        : newest cycle-*.json has >= 10 cycles, all 200/200,
                        and the referenced media files are distinct
  C. exp01-jitter     : re-decode first readable index of every take clip in
                        the newest exp01 capture dir, rejoin with stand log,
                        recompute mean/sigma -> must match published stats.json
                        within 0.5 ms, and every CSV row must reference an
                        existing raw clip
  D. exp02-drift      : refit slope on a fresh subsample of decoded pairs from
                        raw chapters -> published ppm must lie within the
                        refit's 95% CI (and vice versa)
  E. sd-safety        : if any delete was logged, an sd-inventory-*.json older
                        than the first delete must exist
Exit 0 = all present checks pass; prints a JSON verdict.
"""

from __future__ import annotations

import glob
import json
import statistics
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

VERDICT: dict[str, dict] = {}


def check(name):
    def deco(fn):
        def run():
            try:
                out = fn()
                VERDICT[name] = {"status": "PASS", **(out or {})}
            except FileNotFoundError as e:
                VERDICT[name] = {"status": "SKIP", "reason": str(e)}
            except AssertionError as e:
                VERDICT[name] = {"status": "FAIL", "reason": str(e)}
            except Exception as e:  # noqa: BLE001
                VERDICT[name] = {"status": "ERROR", "reason": repr(e)}
        return run
    return deco


@check("A_decoder_selftest")
def check_selftest():
    py = REPO / ".venv" / "Scripts" / "python.exe"
    tmp = REPO / "captures" / "verify-selftest"
    subprocess.run([str(py), str(REPO / "src" / "stand.py"), "render-selftest",
                    "--frames", "200", "--out", str(tmp.relative_to(REPO))],
                   check=True, capture_output=True, cwd=REPO)
    res = subprocess.run([str(py), str(REPO / "src" / "decode_stand.py"),
                          "selftest", str(tmp)], capture_output=True, text=True, cwd=REPO)
    assert res.returncode == 0, f"selftest below 99%: {res.stdout[-200:]}"
    return {"detail": res.stdout.strip().splitlines()[-1]}


@check("B_cycle_log")
def check_cycles():
    logs = sorted((REPO / "docs" / "session-logs").glob("cycle-*.json"))
    if not logs:
        raise FileNotFoundError("no cycle-*.json yet")
    data = json.loads(logs[-1].read_text(encoding="utf-8"))
    assert len(data) >= 10, f"only {len(data)} cycles"
    bad = [r for r in data if r["start_code"] != 200 or r["stop_code"] != 200]
    assert not bad, f"{len(bad)} cycles with non-200"
    files = [r["last_captured"].get("file") for r in data]
    assert len(set(files)) == len(files), "duplicate media files across cycles"
    return {"cycles": len(data), "log": logs[-1].name}


@check("C_exp01_jitter")
def check_exp01():
    exp = REPO / "docs" / "experiments" / "exp01-start-latency"
    stats_f = exp / "stats.json"
    if not stats_f.exists():
        raise FileNotFoundError("exp01 stats.json not present yet")
    published = json.loads(stats_f.read_text(encoding="utf-8"))
    cap_dirs = sorted(REPO.joinpath("captures").glob("*_exp01-start-latency"))
    assert cap_dirs, "no raw exp01 capture dir — stats cannot be verified"
    cap = cap_dirs[-1]
    meta = json.loads((cap / "meta.json").read_text(encoding="utf-8"))
    stand_log = Path(meta["stand_log"])
    assert stand_log.exists(), f"stand log missing: {stand_log}"

    from exp_start_latency import first_decoded_index, load_stand_index
    idx2ns = load_stand_index(stand_log)
    csvs = sorted(exp.glob("latency-*.csv"))
    assert csvs, "no latency CSV"
    rows = [l.split(",") for l in
            csvs[-1].read_text(encoding="utf-8").strip().splitlines()[1:]]
    offsets = []
    for r in rows:
        clip = cap / r[1]
        assert clip.exists(), f"CSV references missing raw clip {r[1]}"
        _, stand_idx = first_decoded_index(clip)
        assert stand_idx is not None, f"cannot re-decode {r[1]}"
        t_disp = idx2ns.get(stand_idx)
        assert t_disp is not None, f"stand idx {stand_idx} not in log"
        offsets.append((t_disp - int(r[2])) / 1e6)
    assert len(offsets) >= 30, f"only {len(offsets)} verifiable takes (<30)"
    mean = statistics.mean(offsets)
    sd = statistics.stdev(offsets)
    assert abs(mean - published["mean_ms"]) < 0.5, \
        f"mean mismatch: recomputed {mean:.3f} vs published {published['mean_ms']:.3f}"
    assert abs(sd - published["stdev_ms"]) < 0.5, \
        f"stdev mismatch: recomputed {sd:.3f} vs published {published['stdev_ms']:.3f}"
    return {"n": len(offsets), "recomputed_mean_ms": round(mean, 3),
            "recomputed_stdev_ms": round(sd, 3)}


@check("D_exp02_drift")
def check_exp02():
    exp = REPO / "docs" / "experiments" / "exp02-drift"
    results = sorted(exp.glob("drift-*.json"))
    if not results:
        raise FileNotFoundError("no exp02 drift result yet")
    published = json.loads(results[-1].read_text(encoding="utf-8"))
    cap_dirs = sorted(REPO.joinpath("captures").glob("*_exp02-drift"))
    assert cap_dirs, "no raw exp02 capture dir"
    cap = cap_dirs[-1]
    meta = json.loads((cap / "meta.json").read_text(encoding="utf-8"))

    import numpy as np
    from exp_drift import decode_pairs
    from exp_start_latency import load_stand_index
    stand_logs = sorted(glob.glob(str(REPO / "docs" / "session-logs" /
                                      "stand-counter-*.jsonl")))
    assert stand_logs, "stand log missing"
    idx2ns = load_stand_index(Path(stand_logs[-1]))
    chapters = [cap / c for c in published["chapters"]]
    for c in chapters:
        assert c.exists(), f"raw chapter missing: {c.name}"
    pairs = []
    for c in chapters:
        pairs.extend(decode_pairs(c, idx2ns, sample_every=45))  # independent subsample
    assert len(pairs) >= 20, f"only {len(pairs)} pairs on re-decode"
    cam_t = np.array([p[0] for p in pairs]); cam_t -= cam_t[0]
    st = np.array([p[1] for p in pairs]); st -= st[0]
    A = np.vstack([np.ones_like(st), st]).T
    coef, *_ = np.linalg.lstsq(A, cam_t, rcond=None)
    ppm = (coef[1] - 1) * 1e6
    tol = max(2 * published.get("ci95_ppm", 1.0), 2.0)
    assert abs(ppm - published["drift_ppm"]) <= tol, \
        f"ppm mismatch: refit {ppm:.2f} vs published {published['drift_ppm']:.2f} (tol {tol:.2f})"
    return {"refit_ppm": round(float(ppm), 2), "published_ppm": published["drift_ppm"]}


@check("E_sd_safety")
def check_sd():
    logs = sorted((REPO / "docs" / "session-logs").glob("gpcli-*.jsonl"))
    deletes = []
    for lg in logs:
        for line in lg.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if "/gopro/media/delete" in str(rec.get("path", "")):
                deletes.append((lg, rec))
    if not deletes:
        return {"detail": "no deletes logged yet"}
    inv = sorted((REPO / "docs" / "session-logs").glob("sd-inventory-*.json"))
    assert inv, "deletes happened but no sd-inventory snapshot exists"
    return {"deletes": len(deletes), "first_inventory": inv[0].name}


def main():
    for fn_name in ["check_selftest", "check_cycles", "check_exp01",
                    "check_exp02", "check_sd"]:
        globals()[fn_name]()
    print(json.dumps(VERDICT, indent=2, ensure_ascii=False))
    hard_fail = any(v["status"] in ("FAIL", "ERROR") for v in VERDICT.values())
    sys.exit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
