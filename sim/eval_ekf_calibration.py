"""Calibrate the EKF model-error sigma on validation seed 0 and check 2-sigma
coverage on seeds 1 and 2 (held out). Writes results/ekf_calibration.json."""
import json
from pathlib import Path

import numpy as np

from ecm_ekf import OCV, ECMParams, calibrate_model_sigma, run_ekf

D = Path("../data/dfn")
p = ECMParams(**{k: float(v) for k, v in json.load(open("../results/ekf_eval.json"))["params"].items()})
o = np.load(D / "ocv_25C.npz")
ocv = OCV(o["soc"], o["ocv"])
KW = dict(q_soc=1e-10, r_v=0.005 ** 2)


def run(seed):
    va = np.load(D / f"valid_25C_s{seed}.npz")
    r = run_ekf(p, ocv, va["t"], va["i"], va["v"], float(va["soc"][0]) - 0.2, **KW)
    s = va["t"] > 600
    return (r["soc_est"] - va["soc"])[s], r["soc_std"][s]


e0, s0 = run(0)
s_m = calibrate_model_sigma(e0, s0)
out = {"model_sigma_pct": 100 * s_m, "ekf_noise": KW, "seeds": {}}
for seed in [0, 1, 2]:
    e, s = run(seed)
    se = np.sqrt(s ** 2 + s_m ** 2)
    out["seeds"][seed] = {
        "role": "calibration" if seed == 0 else "held-out",
        "soc_rmse_pct": float(100 * np.sqrt(np.mean(e ** 2))),
        "soc_max_abs_pct": float(100 * np.max(np.abs(e))),
        "coverage_raw_2sigma": float(np.mean(np.abs(e) <= 2 * s)),
        "coverage_calibrated_2sigma": float(np.mean(np.abs(e) <= 2 * se)),
    }
Path("../results/ekf_calibration.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out, indent=2))
