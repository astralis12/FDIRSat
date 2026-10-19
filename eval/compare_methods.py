"""Methods on the same scenarios and tables (items 3 and 4 of the 17 Oct list).

Policies (all with the same plant seeds; tree inputs as each tree was trained):
  deployed      9-input tree (ADR-0019) + SGO v3                 (results/dagger_summary.json)
  tree6         6-input tree + SGO v3 (ablation of the history)  (results/tree6/dagger_summary.json)
  M1            point thresholds, SGO action table, no plausibility gate (v1 Phase 5 baseline)
  M2            M1 + debounce: confirm 3 samples, clear 5 (v1 Phase 5 baseline)
  M6            SGO v3 alone: tree output forced to 0 (v1 Phase 5 baseline)
  nofm_N_M      deployed tree behind an N-of-M persistence filter (per channel and level)
  expert, none  references
Metric fixes:
  not mitigable  an episode whose hazard also occurs under the expert is counted in a
                 separate class; coverage is computed over the mitigable episodes only
  alarm precision share of steps with any action at which a fault is active and the
                 expert also acts (precision of acting)
Also: false-action rate (share of pre-onset steps with an action) for the coverage vs
false-action curve. Writes results/compare_methods.json.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
for p in ("sim", "train"):
    sys.path.insert(0, str(ROOT / p))
from env_v2 import EPSEnv, EnvConfig, N_FAULTS, ORBITS  # noqa: E402
from fdir_torch import GpuTree, SgoState, sgo_v2, V_CRIT, V_NOM, C_CRIT, T_CRIT, SOC_CRIT_PCT  # noqa: E402
from graph_rollout import GraphStep  # noqa: E402
from train_defender import W_SEV  # noqa: E402

RESPONSE_S = 600.0
NOFM = [(2, 3), (3, 5), (5, 8), (8, 10), (15, 20)]


def m1_table(v, c, t, soc):
    """v1 M1: SGO invariant table on raw telemetry (no plausibility gate, no OV)."""
    z = torch.zeros_like(v, dtype=torch.long)
    dd = (100 * soc < SOC_CRIT_PCT) | (v < V_CRIT)
    uv = ~dd & (v < V_NOM)
    oc, ot = c > C_CRIT, t > T_CRIT
    S = torch.where(dd, 1, z)
    B = torch.where(dd, 1, z)
    L = torch.where(dd, 3, torch.where(uv, 1, z))
    B = torch.where(oc, 2, B)
    L = torch.where(oc & (L == 0), 1, L)
    S = torch.where(ot, 2, S)
    L = torch.where(ot, L.clamp_min(2), L)
    return torch.stack([S, B, L], 1), torch.stack([dd, uv, oc, ot], 1)


def make_policy(kind: str, env, trees: dict):
    n = env.cfg.n_env
    dev = env.dev
    env.sgo_st = SgoState(n, dev)                     # SGO v3.2 state per episode (ADR-0024)
    st = env.sgo_st
    if kind in ("deployed", "tree6"):
        tree, k = trees[kind]
        return lambda obs, ex: sgo_v2(tree(obs[:, :k]), obs[:, 0], obs[:, 1], obs[:, 2], obs[:, 3], state=st)[0]
    if kind == "M6":
        return lambda obs, ex: sgo_v2(torch.zeros(n, 3, dtype=torch.long, device=dev), obs[:, 0], obs[:, 1], obs[:, 2], obs[:, 3], state=st)[0]
    if kind == "M1":
        return lambda obs, ex: m1_table(obs[:, 0], obs[:, 1], obs[:, 2], obs[:, 3])[0]
    if kind == "M2":
        env.p_cnt = torch.zeros(n, 4, device=dev)            # debounce counters per bit, state lives on env
        env.p_on = torch.zeros(n, 4, dtype=torch.bool, device=dev)

        def m2(obs, ex):
            _, raw = m1_table(obs[:, 0], obs[:, 1], obs[:, 2], obs[:, 3])
            cnt = torch.where(raw, (env.p_cnt + 1).clamp(max=3), (env.p_cnt - 1).clamp(min=-5))
            on = torch.where(cnt >= 3, True, torch.where(cnt <= -5, False, env.p_on))
            env.p_cnt, env.p_on = cnt, on
            dd, uv, oc, ot = on.unbind(1)
            z = torch.zeros(n, dtype=torch.long, device=dev)
            S = torch.where(dd, 1, z)
            B = torch.where(dd, 1, z)
            L = torch.where(dd, 3, torch.where(uv, 1, z))
            B = torch.where(oc, 2, B)
            L = torch.where(oc & (L == 0), 1, L)
            S = torch.where(ot, 2, S)
            L = torch.where(ot, L.clamp_min(2), L)
            return torch.stack([S, B, L], 1)
        return m2
    if kind.startswith("nofm_"):
        N, M = map(int, kind.split("_")[1:])
        tree, k = trees["deployed"]
        env.p_hist = torch.zeros(n, M, 3, dtype=torch.long, device=dev)
        env.p_pos = torch.zeros(n, dtype=torch.long, device=dev)

        def nofm(obs, ex):
            raw = tree(obs[:, :k])
            hist = env.p_hist.scatter(1, env.p_pos[:, None, None].expand(n, 1, 3), raw[:, None, :])
            env.p_hist, env.p_pos = hist, (env.p_pos + 1) % M
            filt = sum(((hist >= lvl).sum(1) >= N).long() for lvl in (1, 2, 3))   # highest level seen N of M times
            return sgo_v2(filt, obs[:, 0], obs[:, 1], obs[:, 2], obs[:, 3], state=st)[0]
        return nofm
    if kind == "expert":
        return lambda obs, ex: ex
    if kind == "none":
        return lambda obs, ex: torch.zeros(n, 3, dtype=torch.long, device=dev)
    raise ValueError(kind)


def closed_loop(kind, cfg: EnvConfig, per_fault: int, trees: dict):
    env = EPSEnv(cfg)
    env.fault[:] = torch.arange(N_FAULTS, device=env.dev).repeat_interleave(per_fault)
    pol = make_policy(kind, env, trees)
    steps = int(cfg.episode_orbits * env.orbit.period_s / cfg.dt)
    z = torch.zeros(cfg.n_env, device=env.dev)
    env.a_haz = z.bool()
    env.a_oe = torch.zeros(cfg.n_env, device=env.dev)
    env.a_need = torch.zeros(cfg.n_env, 3, dtype=torch.bool, device=env.dev)
    env.a_resp = env.a_need.clone()
    env.a_nom_act, env.a_nom, env.a_act, env.a_act_ok = z.clone(), z.clone(), z.clone(), z.clone()

    def fn(e):
        obs = e.obs.clone()
        act = pol(obs, e.expert_action())
        _, expert, info = e.step(act)
        after = e.t >= e.t_onset
        within = after & (e.t <= e.t_onset + RESPONSE_S)
        acting = (act > 0).any(1)
        e.a_haz = e.a_haz | torch.stack(list(e.hazards().values()), 1).any(1)
        e.a_oe = e.a_oe + info["out_of_envelope"].float()
        e.a_need = e.a_need | (after[:, None] & (expert > 0))
        e.a_resp = e.a_resp | (within[:, None] & (act > 0))
        e.a_nom_act = e.a_nom_act + (~after & acting).float()
        e.a_nom = e.a_nom + (~after).float()
        e.a_act = e.a_act + acting.float()
        e.a_act_ok = e.a_act_ok + (acting & after & (info["fault"] > 0) & (expert > 0).any(1)).float()
        return {}

    step = GraphStep(env, fn) if env.dev == "cuda" else (lambda: fn(env))
    for _ in range(steps):
        step()
    out = {k: getattr(env, k).cpu() for k in ("a_haz", "a_need", "a_resp", "a_nom_act", "a_nom", "a_act", "a_act_ok")}
    out["oe_time"] = (env.a_oe / steps).cpu()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orbits", nargs="+", default=list(ORBITS))
    ap.add_argument("--per-fault", type=int, default=512)
    ap.add_argument("--episode-orbits", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--deployed", nargs=2, default=["results/dagger_summary.json", "checkpoints/tree"],
                    metavar=("SUMMARY", "CKPT_DIR"), help="tree used as 'deployed' (default: the 9-input tree)")
    ap.add_argument("--kinds", nargs="*", default=None, help="subset of policies (default: all)")
    ap.add_argument("--out", default=str(ROOT / "results" / "compare_methods.json"))
    a = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    trees = {}
    for name, summ, ck in (("deployed", *a.deployed),
                           ("tree6", "results/tree6/dagger_summary.json", "checkpoints/tree6/tree")):
        dep = json.load(open(ROOT / summ))["deploy"]
        clf = pickle.load(open(ROOT / ck / f"{dep['mode']}_s{dep['seed']}.pkl", "rb"))
        trees[name] = (GpuTree(clf, dev), clf.n_features_in_)
    sev = json.load(open(ROOT / "config" / "fmeca_xjtu_sps.json", encoding="utf-8"))["fault_classes"]
    kinds = a.kinds or ["deployed", "tree6", "M1", "M2", "M6", *[f"nofm_{n}_{m}" for n, m in NOFM], "expert", "none"]
    if "expert" not in kinds:
        kinds.append("expert")                     # needed for the not-mitigable class
    res = {"response_s": RESPONSE_S, "per_fault": a.per_fault, "episode_orbits": a.episode_orbits, "seed": a.seed, "orbits": {}}
    for orb in a.orbits:
        cfg = EnvConfig(n_env=a.per_fault * N_FAULTS, orbit=orb, episode_orbits=a.episode_orbits, seed=a.seed, fault_prob=1.0)
        runs = {k: closed_loop(k, cfg, a.per_fault, trees) for k in kinds}
        he = runs["expert"]["a_haz"]
        out = {}
        for k in kinds:
            r = runs[k]
            rows, cov_m = {}, []
            for f in range(N_FAULTS):
                sl = slice(f * a.per_fault, (f + 1) * a.per_fault)
                unmit = he[sl]                                       # hazard also under the expert
                avoidable = r["a_haz"][sl] & ~he[sl]
                missed = (runs["expert"]["a_need"][sl] & ~r["a_resp"][sl]).any(1)
                ok = ~avoidable & ~missed
                mit = ~unmit
                row = {"not_mitigable_share": float(unmit.float().mean()),
                       "coverage_mitigable": float(ok[mit].float().mean()) if mit.any() else None,
                       "hazard": float(r["a_haz"][sl].float().mean()),
                       "false_action_rate": float((r["a_nom_act"][sl] / r["a_nom"][sl].clamp_min(1)).mean()),
                       "severity": sev[str(f)]["severity"] or 0, "out_of_envelope_time": float(r["oe_time"][sl].mean())}
                rows[f] = row
                if f > 0 and row["coverage_mitigable"] is not None:
                    cov_m.append(row["coverage_mitigable"])
            act, ok_act = r["a_act"].sum(), r["a_act_ok"].sum()
            fa = float(np.mean([rows[f]["false_action_rate"] for f in range(N_FAULTS)]))
            rr = sum(1e-4 * (1 - rows[f]["coverage_mitigable"]) * float(W_SEV[rows[f]["severity"]])
                     for f in range(1, N_FAULTS) if rows[f]["coverage_mitigable"] is not None)
            out[k] = {"per_fault": rows, "coverage_mitigable_mean": float(np.mean(cov_m)), "false_action_rate_mean": fa,
                      "alarm_precision": float(ok_act / act) if act > 0 else None,
                      "hazard_mean_faults": float(np.mean([rows[f]["hazard"] for f in range(1, N_FAULTS)])),
                      "residual_risk_relative": rr, "out_of_envelope_time_mean": float(r["oe_time"].mean())}
            print(orb, k, {x: (round(y, 4) if isinstance(y, float) else y) for x, y in out[k].items() if x != "per_fault"}, flush=True)
        res["orbits"][orb] = out
    Path(a.out).write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
