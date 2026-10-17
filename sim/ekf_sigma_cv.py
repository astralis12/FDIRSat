"""EKF uncertainty calibration for the selected 4.1 model (ISS-025).

Under the 4.1 settings, seed 6's coverage misses are all in the cold-start tail
(600-2700 s), at normal OCV slope: the RC states, with process noise q_rc,
drift and absorb the SoC error, so the error decays slowly while P is small.
This script searches, on calibration seeds only:
  q_rc    RC-state process noise (smaller = RC states follow the current)
  r_v     voltage noise
  infl    sigma inflation, one of
            none   : raw EKF sigma
            const  : sqrt(P_soc + s_m^2)                      (4.1)
            slope  : sqrt(P_soc + (k * sigma_vm / |OCV'(soc_est)|)^2)
                     SoC-dependent floor: the voltage model error sigma_vm
                     (open-loop RMSE on the calibration seeds) mapped to SoC
                     through the local OCV slope, scaled by k
Selection: smallest mean 2-sigma such that pooled coverage >= 0.95 and every
calibration seed >= CAL_SEED_MIN (a margin above the 0.90 gate). Checked by leave-one-seed-out over the calibration
seeds, then once on test seeds that no choice was made on.

Also writes the slow-third-RC data check: the C/20 vs rest-OCV offset divided
by the C/20 current is the DC resistance the curve implies; a slow branch can
only add the part not already explained by R0 + R1 + R2.
Writes results/ekf_sigma_cv.json.
"""
from __future__ import annotations

import argparse
import itertools
import json
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from ecm_ekf import OCV, ScheduledECM, run_ekf, simulate_scheduled
from fit_ecm_scheduled import SETTLE_S, COVERAGE_GATE, CAL_TARGET

CAL_SEED_MIN = 0.95                     # per calibration seed: margin over the 0.90 gate, chosen by LOSO (ISS-025)

ROOT = Path(__file__).resolve().parents[1]
Q_SOC = 1e-10
Q_RC_GRID = [1e-7, 1e-8, 1e-9, 1e-10]
R_V_GRID = [0.005, 0.0075, 0.010, 0.015, 0.020, 0.030, 0.040]
S_M_GRID = np.round(np.arange(0.0, 0.0301, 0.0005), 4)      # SoC fraction
K_GRID = np.round(np.arange(0.0, 4.001, 0.05), 3)
SLOPE_MIN = 0.05                                            # V per unit SoC, guards the floor


def _ekf(job):
    """One EKF run; returns the settled error, raw sigma and the SoC estimate."""
    s, q_rc, r_v, d, temp, soc_init_error, tab_d, ocv_soc, ocv_v = job
    tab, ocv = ScheduledECM.from_dict(tab_d), OCV(np.asarray(ocv_soc), np.asarray(ocv_v))
    va = np.load(Path(d) / f"valid_{temp}C_s{s}.npz")
    r = run_ekf(tab, ocv, va["t"], va["i"], va["v"], float(va["soc"][0]) - soc_init_error,
                q_soc=Q_SOC, q_rc=q_rc, r_v=r_v ** 2)
    k = va["t"] > SETTLE_S
    vm = simulate_scheduled(tab, ocv, va["t"], va["i"], float(va["soc"][0]))[0]
    ol = float(np.sqrt(np.mean((vm - va["v"]) ** 2)))
    return (s, q_rc, r_v), ((r["soc_est"] - va["soc"])[k], r["soc_std"][k], r["soc_est"][k], ol)


def sigma(kind, par, sd, est, ocv, sigma_vm):
    if kind == "none":
        return sd
    if kind == "const":
        return np.sqrt(sd ** 2 + par ** 2)
    floor = par * sigma_vm / np.maximum(np.abs(ocv.slope(est)), SLOPE_MIN)
    return np.sqrt(sd ** 2 + floor ** 2)


GRIDS = {"none": [0.0], "const": S_M_GRID, "slope": K_GRID}


def select(runs, seeds, ocv, kinds=("none", "const", "slope")) -> dict | None:
    """Sharpest setting meeting the calibration criterion on `seeds`."""
    subs = [select_kind(runs, seeds, ocv, k, GRIDS[k], q, r)
            for k in kinds for q, r in itertools.product(Q_RC_GRID, R_V_GRID)]
    subs = [x for x in subs if x]
    return min(subs, key=lambda x: x["two_sigma_mean_pct"]) if subs else None


def evaluate(runs, sel, s, ocv) -> dict:
    e, sd, est, ol = runs[(s, sel["q_rc"], sel["r_v"])]
    st = sigma(sel["inflation"], sel["param"], sd, est, ocv, sel["sigma_vm_mV"] / 1e3)
    return {"coverage_2sigma": float(np.mean(np.abs(e) <= 2 * st)), "two_sigma_mean_pct": float(200 * np.mean(st)),
            "soc_rmse_pct": float(100 * np.sqrt(np.mean(e ** 2))), "soc_max_abs_pct": float(100 * np.max(np.abs(e)))}


def slow_rc_check(d: Path, temp: int, sel4: dict) -> dict:
    """DC resistance implied by the C/20 curve vs R0+R1+R2 of the selected table."""
    pu = np.load(d / f"pulse_{temp}C.npz")
    q = float(pu["q_nom"])
    rp = sel4["rest_ocv_points"]
    sp, off = np.asarray(rp["soc"]), np.asarray(rp["offset_vs_c20_mV"]) / 1e3
    tab = ScheduledECM.from_dict(sel4["selected"]["table"])
    r0, r1, _, r2, _ = tab.arrays(sp)
    i20 = q / 20.0
    r_dc = off / i20
    return {"c20_current_A": i20, "soc": sp.tolist(), "r_dc_from_c20_mohm": (1e3 * r_dc).tolist(),
            "r0_r1_r2_mohm": (1e3 * (r0 + r1 + r2)).tolist(),
            "room_for_r3_mohm": (1e3 * (r_dc - (r0 + r1 + r2))).tolist()}


def main() -> None:
    global CAL_SEED_MIN
    ap = argparse.ArgumentParser()
    ap.add_argument("--dfn", default=str(ROOT / "data" / "dfn"))
    ap.add_argument("--ecm", default=str(ROOT / "results" / "ecm_scheduled.json"))
    ap.add_argument("--cal-seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13])
    # 14-19 generated after 4.1 was frozen; 16 stops at 400 s (ISS-024)
    ap.add_argument("--test-seeds", type=int, nargs="*", default=[14, 15, 17, 18, 19])
    ap.add_argument("--soc-init-error", type=float, default=0.20)
    ap.add_argument("--workers", type=int, default=15)
    ap.add_argument("--cal-seed-min", type=float, default=CAL_SEED_MIN)
    ap.add_argument("--cache", default=None, help="pickle of EKF runs, reused if present")
    ap.add_argument("--out", default=str(ROOT / "results" / "ekf_sigma_cv.json"))
    a = ap.parse_args()

    e4 = json.load(open(a.ecm))["dfn"]
    temp, d = int(e4["temp_c"]), Path(a.dfn)
    o = np.load(d / f"ocv_{temp}C.npz")
    ocv = OCV(o["soc"], o["ocv"])
    if e4["selected"]["ocv"] != "c20":
        rp = e4["rest_ocv_points"]
        ocv = ocv.corrected(np.asarray(rp["soc"]), np.asarray(rp["v"]))
    seeds = list(a.cal_seeds) + list(a.test_seeds)
    for s in seeds:
        t_end = float(np.load(d / f"valid_{temp}C_s{s}.npz")["t"][-1])
        if t_end < 0.99 * 3 * 3600:
            raise SystemExit(f"validation seed {s} is truncated at {t_end:.0f} s; pick another seed")
    jobs = [(s, q, r, str(d), temp, a.soc_init_error, e4["selected"]["table"], ocv.soc.tolist(), ocv.v.tolist())
            for s, q, r in itertools.product(seeds, Q_RC_GRID, R_V_GRID)]
    CAL_SEED_MIN = a.cal_seed_min
    import pickle
    if a.cache and Path(a.cache).exists():
        runs = pickle.load(open(a.cache, "rb"))
        jobs = [j for j in jobs if (j[0], j[1], j[2]) not in runs]
    else:
        runs = {}
    if jobs:
        with Pool(a.workers) as pool:
            runs.update(pool.map(_ekf, jobs, chunksize=1))
        if a.cache:
            pickle.dump(runs, open(a.cache, "wb"))

    # leave-one-seed-out over the calibration seeds
    loso = {}
    for s in a.cal_seeds:
        sel = select(runs, [x for x in a.cal_seeds if x != s], ocv)
        loso[str(s)] = {"selected": sel, **evaluate(runs, sel, s, ocv)}
    final = select(runs, a.cal_seeds, ocv)
    # the same rule restricted to each inflation kind, for the ablation
    per_kind = {k: select(runs, a.cal_seeds, ocv, (k,)) for k in GRIDS}
    test = {str(s): evaluate(runs, final, s, ocv) for s in a.test_seeds}
    r_v41 = min(R_V_GRID, key=lambda r: abs(1e3 * r - e4["selected"]["r_v_mV"]))
    baseline = {"q_rc": 1e-7, "r_v": r_v41, "r_v_mV": 1e3 * r_v41, "inflation": "const",
                "param": e4["selected"]["model_sigma_pct"] / 100, "sigma_vm_mV": 0.0}
    res = {
        "model": e4["selected"]["variant"], "q_soc": Q_SOC, "settle_s": SETTLE_S,
        "grids": {"q_rc": Q_RC_GRID, "r_v_mV": [1e3 * x for x in R_V_GRID],
                  "s_m_pct": [100 * x for x in S_M_GRID.tolist()], "k": K_GRID.tolist()},
        "criterion": {"pooled": CAL_TARGET, "per_seed_min": CAL_SEED_MIN, "objective": "smallest mean 2-sigma"},
        "calibration_seeds": list(a.cal_seeds), "test_seeds": list(a.test_seeds),
        "baseline_4_1": {"setting": baseline,
                         "seeds": {str(s): evaluate(runs, baseline, s, ocv) for s in seeds}},
        "loso": loso,
        "loso_min_coverage": float(min(x["coverage_2sigma"] for x in loso.values())),
        "per_inflation_kind": per_kind,
        "selected": final,
        "test": test,
        "gate": {"metric": "min 2-sigma coverage over test seeds", "threshold": COVERAGE_GATE,
                 "value": float(min(x["coverage_2sigma"] for x in test.values())) if test else None},
        "slow_rc_check": slow_rc_check(d, temp, e4),
    }
    res["gate"]["pass"] = bool(res["gate"]["value"] is not None and res["gate"]["value"] >= COVERAGE_GATE)
    Path(a.out).write_text(json.dumps(res, indent=2))

    b = res["baseline_4_1"]["seeds"]
    print("4.1 baseline coverage:", {s: round(x["coverage_2sigma"], 3) for s, x in b.items()})
    print("per inflation kind:", {k: (v["q_rc"], v["r_v_mV"], v["param"], round(v["two_sigma_mean_pct"], 2)) if v else None
                                   for k, v in per_kind.items()})
    print("selected:", final)
    print("LOSO coverage:", {s: (round(x["coverage_2sigma"], 3), x["selected"]["inflation"]) for s, x in loso.items()})
    print("test:", {s: (round(x["coverage_2sigma"], 3), round(x["soc_rmse_pct"], 2), round(x["two_sigma_mean_pct"], 2))
                   for s, x in test.items()})
    print("GATE", res["gate"])
    print("slow RC check, room for R3 [mOhm]:", np.round(res["slow_rc_check"]["room_for_r3_mohm"], 1).tolist())


def select_kind(runs, seeds, ocv, kind, grid, q_rc, r_v):
    """`select` restricted to one inflation kind and one (q_rc, r_v)."""
    sigma_vm = float(np.sqrt(np.mean([runs[(s, Q_RC_GRID[0], R_V_GRID[0])][3] ** 2 for s in seeds])))
    data = [runs[(s, q_rc, r_v)] for s in seeds]
    n = np.array([len(x[0]) for x in data])
    for par in grid:
        cov, width = [], []
        for e, sd, est, _ in data:
            st = sigma(kind, par, sd, est, ocv, sigma_vm)
            cov.append(np.mean(np.abs(e) <= 2 * st))
            width.append(np.mean(2 * st))
        pooled = float(np.sum(np.array(cov) * n) / n.sum())
        if pooled >= CAL_TARGET and min(cov) >= CAL_SEED_MIN:
            return {"q_rc": q_rc, "r_v": r_v, "r_v_mV": 1e3 * r_v, "inflation": kind, "param": float(par),
                    "sigma_vm_mV": 1e3 * sigma_vm, "two_sigma_mean_pct": 100 * float(np.mean(width)),
                    "cal_pooled_coverage": pooled, "cal_min_coverage": float(min(cov))}
    return None


if __name__ == "__main__":
    main()
