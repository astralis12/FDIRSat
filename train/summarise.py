"""Aggregate 4.5 / 4.6 runs over seeds and evaluate their gates.

results/defender_summary.json  gate 4.5: every seed converged
results/dagger_summary.json    gate 4.6: 95 % CI half-width of the closed-loop
                               fidelity (sw_dagger, over seeds) <= 1.5 pp;
                               ablations BC and plain DAgger side by side;
                               deploy = sw_dagger seed with the lowest closed-loop
                               severity-weighted disagreement (held-out seeds)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
CI_GATE_PP = 1.5


def ci95(x) -> dict:
    x = np.asarray(x, float)
    if x.size < 2:
        return {"mean": float(x.mean()) if x.size else None, "half_width": None, "n": int(x.size)}
    h = float(stats.t.ppf(0.975, x.size - 1) * x.std(ddof=1) / np.sqrt(x.size))
    return {"mean": float(x.mean()), "half_width": h, "n": int(x.size)}


def main() -> None:
    d = sorted((ROOT / "results" / "defender").glob("s*.json"))
    runs = [json.load(open(f)) for f in d]
    dres = {"seeds": [r["seed"] for r in runs], "converged": {r["seed"]: r["converged"] for r in runs},
            "val_loss": ci95([r["best_val_loss"] for r in runs]),
            "val_severity_weighted_error": ci95([r["final"]["severity_weighted_error"] for r in runs]),
            "val_acc_per_head": {h: ci95([r["final"]["acc_per_head"][k] for r in runs]) for k, h in enumerate("SBL")},
            "gate": {"metric": "all seeds converged", "pass": bool(runs) and all(r["converged"] for r in runs),
                     "n_seeds": len(runs)}}
    (ROOT / "results" / "defender_summary.json").write_text(json.dumps(dres, indent=2))
    print("defender", dres["gate"], "val sev-err", dres["val_severity_weighted_error"])

    out = {}
    for mode in ("sw_dagger", "dagger", "bc"):
        rs = [json.load(open(f)) for f in sorted((ROOT / "results" / "dagger").glob(f"{mode}_s*.json"))]
        if not rs:
            continue
        out[mode] = {"seeds": [r["seed"] for r in rs],
                     "fidelity_closed_loop_pp": ci95([100 * r["selected"]["fidelity_closed_loop"] for r in rs]),
                     "fidelity_uniform_pp": ci95([100 * r["selected"]["fidelity_uniform"] for r in rs]),
                     "sev_weighted_disagreement": ci95([r["selected"]["sev_weighted_disagreement"] for r in rs]),
                     "hazard_rate": ci95([r["selected"]["hazard_rate"] for r in rs]),
                     "leaves": ci95([r["selected"]["leaves"] for r in rs]),
                     "adversary_failures_found": int(sum(r["adversary_failures_found"] for r in rs)),
                     "fuzzing_failures_found": int(sum(r["fuzzing_failures_found"] for r in rs)),
                     "adversary_failure_clusters": ci95([r.get("adversary_failure_clusters", np.nan) for r in rs]),
                     "fuzzing_failure_clusters": ci95([r.get("fuzzing_failure_clusters", np.nan) for r in rs]),
                     "clusters_only_adversary": ci95([r.get("clusters_found_only_by_adversary", np.nan) for r in rs]),
                     "clusters_only_fuzzing": ci95([r.get("clusters_found_only_by_fuzzing", np.nan) for r in rs])}
    res = {"modes": out}
    if "sw_dagger" in out:
        rs = [json.load(open(f)) for f in sorted((ROOT / "results" / "dagger").glob("sw_dagger_s*.json"))]
        best = min(rs, key=lambda r: r["selected"]["sev_weighted_disagreement"])
        res["deploy"] = {"mode": "sw_dagger", "seed": best["seed"], "rule": "lowest closed-loop severity-weighted disagreement (held-out env seeds)",
                         "selected": best["selected"]}
        hw = out["sw_dagger"]["fidelity_closed_loop_pp"]["half_width"]
        res["gate"] = {"metric": "95 % CI half-width of closed-loop fidelity, sw_dagger, over seeds [pp]",
                       "threshold": CI_GATE_PP, "value": hw, "pass": hw is not None and hw <= CI_GATE_PP}
    (ROOT / "results" / "dagger_summary.json").write_text(json.dumps(res, indent=2))
    print(json.dumps({k: {m: v[m] for m in ("fidelity_closed_loop_pp", "sev_weighted_disagreement")} for k, v in out.items()}, indent=1))
    print("gate", res.get("gate"), "deploy", res.get("deploy", {}).get("seed"))


if __name__ == "__main__":
    main()
