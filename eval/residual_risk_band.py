"""Residual risk per orbit as a band, with M2 rates (item 6).

R = sum_i P(f_i) (1 - C_i) S_i over mitigable episodes, plus a separate
not-mitigable term sum_i P(f_i) u_i S_i (u_i: share of episodes where even the
expert cannot avoid the hazard).
  lambda_i   config/fmeca_rates.json (three layers, source per row)
  P(f_i)     1 - exp(-lambda_i T_orb), T_orb from each orbit's period
  C_i, u_i   results/compare_methods.json, deployed system; artefact modes from
             results/fault_injection.json (C = 1 - P(SDC | upset), rule-of-three
             upper bound when no SDC was seen)
  S_i        severity weight of the proposed ECSS category (as in training)
Uncertainty: every lambda_i log-uniform over +-1 decade, independently, 20 000 draws.
Ranking stability: Kendall tau between each draw's ranking of mode contributions and
the nominal ranking; how often the nominal top 3 stay the top 3; per-mode rank range.
Writes results/residual_risk_band.json.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.stats import kendalltau

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim"))
from env_v2 import ORBITS  # noqa: E402

W_SEV = [1.0, 4.0, 3.0, 2.0, 1.0]
N_DRAWS = 20_000


def rates(cfg: dict, fi: dict | None, fm: dict) -> dict:
    l1 = cfg["layer1_subsystem_prior"]
    w = l1["weibull"]
    h1 = (365.0 / w["theta_days"]) ** w["beta"]
    lam_eps = l1["eps_share"]["value"] * h1 / 8766.0
    out = {}
    l2 = cfg["layer2_parts_split"]["blocks"]
    total = sum(b["parts"] for b in l2.values())
    for name, b in l2.items():
        for m in b["modes"]:
            out[str(m)] = {"lambda_per_h": lam_eps * b["parts"] / total / len(b["modes"]), "layer": 2,
                           "block": name, "severity": fm["fault_classes"][str(m)]["severity"]}
    l3 = cfg["layer3_environment"]
    r = l3["r_bit_per_bit_day"]["value"] / 24.0
    for m, d in l3["modes"].items():
        regs = d["region"] if isinstance(d["region"], list) else [d["region"]]
        p_sdc = None
        if fi:
            n = sum(sum(fi["regions"][g].values()) for g in regs if g in fi["regions"])
            k = sum(fi["regions"][g]["sdc"] for g in regs if g in fi["regions"])
            p_sdc = (k / n if k else 3.0 / n) if n else None
        out[m] = {"lambda_per_h": r * d["bits"], "layer": 3, "p_sdc_per_upset": p_sdc,
                  "severity": fm["fdir_artefact_modes"][m]["severity"]}
    return {"lambda_eps_per_h": lam_eps, "modes": out}


def main() -> None:
    cfg = json.load(open(ROOT / "config" / "fmeca_rates.json", encoding="utf-8"))
    fm = json.load(open(ROOT / "config" / "fmeca_xjtu_sps.json", encoding="utf-8"))
    cmpm = json.load(open(ROOT / "results" / "compare_methods.json"))
    fip = ROOT / "results" / "fault_injection.json"
    fi = json.load(open(fip)) if fip.exists() else None
    rt = rates(cfg, fi, fm)
    modes = list(rt["modes"])
    rng = np.random.default_rng(17)
    res = {"lambda_eps_per_h": rt["lambda_eps_per_h"], "modes": rt["modes"], "draws": N_DRAWS, "orbits": {}}
    for orb, o in ORBITS.items():
        if orb not in cmpm["orbits"]:
            continue
        dep = cmpm["orbits"][orb]["deployed"]["per_fault"]
        t_orb_h = o.period_s / 3600.0
        lam = np.array([rt["modes"][m]["lambda_per_h"] for m in modes])
        cov, unm, sev = [], [], []
        for m in modes:
            d = rt["modes"][m]
            sev.append(W_SEV[d["severity"] or 0])
            if d["layer"] == 2:
                row = dep[m]
                cov.append(row["coverage_mitigable"] if row["coverage_mitigable"] is not None else 0.0)
                unm.append(row["not_mitigable_share"])
            else:
                cov.append(1.0 - (d["p_sdc_per_upset"] if d["p_sdc_per_upset"] is not None else 1.0))
                unm.append(0.0)
        cov, unm, sev = map(np.array, (cov, unm, sev))
        nominal_p = 1 - np.exp(-lam * t_orb_h)
        nominal_c = nominal_p * (1 - cov) * (1 - unm) * sev
        draws = lam[None, :] * 10.0 ** rng.uniform(-1, 1, size=(N_DRAWS, len(modes)))
        p = 1 - np.exp(-draws * t_orb_h)
        contrib = p * (1 - cov) * (1 - unm) * sev
        r_mit = contrib.sum(1)
        r_unm = (p * unm * sev).sum(1)
        nom_rank = np.argsort(np.argsort(-nominal_c))
        taus = [kendalltau(nom_rank, np.argsort(np.argsort(-c))).statistic for c in contrib[:2000]]
        top3 = set(np.argsort(-nominal_c)[:3])
        top3_kept = float(np.mean([set(np.argsort(-c)[:3]) == top3 for c in contrib]))
        ranks = np.argsort(np.argsort(-contrib, 1), 1) + 1
        res["orbits"][orb] = {
            "t_orb_h": t_orb_h,
            "residual_risk_nominal": float(nominal_c.sum()),
            "residual_risk_band": {"p5": float(np.percentile(r_mit, 5)), "p50": float(np.median(r_mit)), "p95": float(np.percentile(r_mit, 95))},
            "not_mitigable_band": {"p5": float(np.percentile(r_unm, 5)), "p50": float(np.median(r_unm)), "p95": float(np.percentile(r_unm, 95))},
            "kendall_tau_vs_nominal": {"median": float(np.median(taus)), "p5": float(np.percentile(taus, 5))},
            "nominal_top3": [modes[i] for i in np.argsort(-nominal_c)[:3]],
            "top3_unchanged_share": top3_kept,
            "per_mode": {m: {"p_nominal": float(nominal_p[k]), "coverage": float(cov[k]), "not_mitigable": float(unm[k]),
                             "contribution_nominal": float(nominal_c[k]), "rank_nominal": int(nom_rank[k] + 1),
                             "rank_p5": int(np.percentile(ranks[:, k], 5)), "rank_p95": int(np.percentile(ranks[:, k], 95))}
                         for k, m in enumerate(modes)},
        }
        print(orb, {k: v for k, v in res["orbits"][orb].items() if k != "per_mode"}, flush=True)
    (ROOT / "results" / "residual_risk_band.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
