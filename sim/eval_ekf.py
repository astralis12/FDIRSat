"""Fit the 2RC ECM on the pulse data and evaluate the EKF on the validation
profile. Writes results/ekf_eval.json and prints a short report.

Metrics:
  fit_rmse_mV         : ECM voltage error on the fitting (pulse) profile
  valid_rmse_mV       : ECM open-loop voltage error on the validation profile
  soc_rmse_pct        : EKF SoC error after convergence (t > 10 min)
  soc_max_abs_pct     : worst EKF SoC error after convergence
  nis_mean            : mean normalised innovation squared (~1 if consistent)
  coverage_2sigma     : fraction of time the true SoC lies within +/-2 sigma
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ecm_ekf import OCV, fit, run_ekf, simulate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/dfn")
    ap.add_argument("--temp", type=int, default=25)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--soc-init-error", type=float, default=0.20)
    ap.add_argument("--out", default="results/ekf_eval.json")
    a = ap.parse_args()
    d = Path(a.data)

    o = np.load(d / f"ocv_{a.temp}C.npz")
    ocv = OCV(o["soc"], o["ocv"])
    pu = np.load(d / f"pulse_{a.temp}C.npz")
    va = np.load(d / f"valid_{a.temp}C_s{a.seed}.npz")
    q = float(pu["q_nom"])

    params, fit_rmse = fit(pu["t"], pu["i"], pu["v"], float(pu["soc"][0]), ocv, q)
    v_open, _ = simulate(params, ocv, va["t"], va["i"], float(va["soc"][0]))
    valid_rmse = float(np.sqrt(np.mean((v_open - va["v"]) ** 2)))

    soc_true = va["soc"]
    soc0 = float(soc_true[0]) - a.soc_init_error
    r = run_ekf(params, ocv, va["t"], va["i"], va["v"], soc0)
    settled = va["t"] > 600.0
    err = r["soc_est"] - soc_true
    nis = (r["innov"][1:] ** 2) / r["innov_var"][1:]
    cover = np.abs(err) <= 2.0 * r["soc_std"]

    res = {
        "params": params.as_dict(),
        "fit_rmse_mV": 1e3 * fit_rmse,
        "valid_rmse_mV": 1e3 * valid_rmse,
        "soc_init_error_pct": 100 * a.soc_init_error,
        "soc_rmse_pct": float(100 * np.sqrt(np.mean(err[settled] ** 2))),
        "soc_max_abs_pct": float(100 * np.max(np.abs(err[settled]))),
        "time_to_2pct_s": float(va["t"][np.argmax(np.abs(err) < 0.02)]),
        "nis_mean": float(np.nanmean(nis)),
        "coverage_2sigma": float(np.mean(cover[settled])),
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2))
    for k, v in res.items():
        print(f"{k:22s} {v if isinstance(v, dict) else round(v, 4)}")


if __name__ == "__main__":
    main()
