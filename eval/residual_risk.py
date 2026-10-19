"""Coverage per FMECA mode and residual risk per orbit (RUNBOOK 4.8).

For each orbit and fault class i, held-out scenarios run in closed loop three
times with the same seed: deployed (tree + SGO), expert, and no FDIR at all.
  acceptable episode = no hazard that the expert closed loop avoids, and, when
                       the expert acts on a channel after onset, the deployed
                       system acts on that channel within RESPONSE_S
  C_i   = share of acceptable episodes (detected and correctly recovered)
  R_res = sum_i P(f_i) (1 - C_i) S_i            (plan doc, "RAMS and FDIR")
P(f_i): ASSUMED equal per-orbit rate for every mode until the M2 FMECA gives
occurrence rates; S_i: severity weight of the proposed category (same weights
as training). False-action rate: share of pre-onset steps with any action > 0
(reported next to coverage: frequent actions make the response criterion easier).
Writes results/residual_risk.json.
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
sys.path.insert(0, str(ROOT / "sim"))
sys.path.insert(0, str(ROOT / "train"))
from env_v2 import EPSEnv, EnvConfig, N_FAULTS, ORBITS  # noqa: E402
from fdir_torch import GpuTree, SgoState, sgo_v2  # noqa: E402
from train_defender import W_SEV  # noqa: E402
from graph_rollout import GraphStep  # noqa: E402

RESPONSE_S = 600.0
P_FAULT_ASSUMED = 1e-4                 # per orbit, every mode (ASSUMED, M2)


def closed_loop(policy, cfg: EnvConfig, per_fault: int):
    """All fault classes in one batch: env block f (per_fault envs) has fault f.
    CUDA-graph captured (train/graph_rollout.py)."""
    env = EPSEnv(cfg)
    env.fault[:] = torch.arange(N_FAULTS, device=env.dev).repeat_interleave(per_fault)
    steps = int(cfg.episode_orbits * env.orbit.period_s / cfg.dt)
    z = torch.zeros(cfg.n_env, dtype=torch.bool, device=env.dev)
    env.acc_hazard = z.clone()
    env.acc_nom_act = torch.zeros(cfg.n_env, device=env.dev)
    env.acc_nom_steps = torch.zeros(cfg.n_env, device=env.dev)
    env.acc_needed = torch.zeros(cfg.n_env, 3, dtype=torch.bool, device=env.dev)
    env.acc_resp = env.acc_needed.clone()
    env.acc_oe = torch.zeros(cfg.n_env, device=env.dev)              # out-of-envelope steps (ADR-0024)
    env.sgo_st = SgoState(cfg.n_env, env.dev)                         # SGO v3.2 state, one per episode

    def fn(e):
        obs = e.obs.clone()
        act = policy(obs, e.expert_action(), e.sgo_st)
        _, expert, info = e.step(act)
        e.acc_oe = e.acc_oe + info["out_of_envelope"].float()
        after = e.t >= e.t_onset
        within = after & (e.t <= e.t_onset + RESPONSE_S)
        e.acc_hazard = e.acc_hazard | torch.stack(list(e.hazards().values()), 1).any(1)
        e.acc_needed = e.acc_needed | (after[:, None] & (expert > 0))
        e.acc_resp = e.acc_resp | (within[:, None] & (act > 0))
        e.acc_nom_act = e.acc_nom_act + (~after & (act > 0).any(1)).float()
        e.acc_nom_steps = e.acc_nom_steps + (~after).float()
        return {}

    step = GraphStep(env, fn) if env.dev == "cuda" else (lambda: fn(env))
    for _ in range(steps):
        step()
    return (env.acc_hazard.cpu(), env.acc_needed.cpu(), env.acc_resp.cpu(),
            (env.acc_nom_act / env.acc_nom_steps.clamp_min(1)).cpu(), (env.acc_oe / steps).cpu())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=None, help="tree pickle (default: deploy seed from results/dagger_summary.json)")
    ap.add_argument("--orbits", nargs="+", default=list(ORBITS))
    ap.add_argument("--per-fault", type=int, default=1024)
    ap.add_argument("--episode-orbits", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--sgo", default="v3.2", choices=["v2", "v3", "v3.2"], help="safety layer version (v3.2 = deployed, ADR-0024)")
    ap.add_argument("--out", default=str(ROOT / "results" / "residual_risk.json"))
    a = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    if a.tree is None:
        dep = json.load(open(ROOT / "results" / "dagger_summary.json"))["deploy"]
        a.tree = str(ROOT / "checkpoints" / "tree" / f"{dep['mode']}_s{dep['seed']}.pkl")
    tree = GpuTree(pickle.load(open(a.tree, "rb")), dev)

    def deployed(obs, expert, st):
        return sgo_v2(tree(obs), obs[:, 0], obs[:, 1], obs[:, 2], obs[:, 3], ov_invariant=a.sgo != "v2",
                      state=st if a.sgo == "v3.2" else None)[0]

    def expert_pol(obs, expert, st):
        return expert

    def no_fdir(obs, expert, st):
        return torch.zeros_like(expert)

    sev = json.load(open(ROOT / "config" / "fmeca_xjtu_sps.json", encoding="utf-8"))["fault_classes"]
    res = {"tree": Path(a.tree).name, "sgo": a.sgo, "response_s": RESPONSE_S, "p_fault_per_orbit_ASSUMED": P_FAULT_ASSUMED,
           "severity_weights": W_SEV.tolist(), "orbits": {}}
    for orb in a.orbits:
        cfg = EnvConfig(n_env=a.per_fault * N_FAULTS, orbit=orb, episode_orbits=a.episode_orbits, seed=a.seed, fault_prob=1.0)
        hd, need_d, resp_d, nom_d, oe_d = closed_loop(deployed, cfg, a.per_fault)
        he, need_e, _, _, oe_e = closed_loop(expert_pol, cfg, a.per_fault)
        hn, _, _, _, oe_n = closed_loop(no_fdir, cfg, a.per_fault)
        rows, rr = {}, 0.0
        for f in range(N_FAULTS):
            sl = slice(f * a.per_fault, (f + 1) * a.per_fault)
            avoidable = hd[sl] & ~he[sl]
            missed = (need_e[sl] & ~resp_d[sl]).any(1)
            ok = ~avoidable & ~missed
            sv = sev[str(f)]["severity"] or 0
            row = {"coverage": float(ok.float().mean()), "hazard_deployed": float(hd[sl].float().mean()),
                   "hazard_expert": float(he[sl].float().mean()), "hazard_no_fdir": float(hn[sl].float().mean()),
                   "avoidable_hazard": float(avoidable.float().mean()),
                   "missed_response": float(missed.float().mean()),
                   "false_action_rate_before_onset": float(nom_d[sl].mean()), "severity": sv,
                   "out_of_envelope_time_deployed": float(oe_d[sl].mean()), "out_of_envelope_time_expert": float(oe_e[sl].mean()),
                   "out_of_envelope_time_no_fdir": float(oe_n[sl].mean())}
            if f > 0:
                rr += P_FAULT_ASSUMED * (1 - row["coverage"]) * float(W_SEV[sv])
            rows[f] = row
            print(orb, f, {k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()}, flush=True)
        res["orbits"][orb] = {"per_fault": rows, "residual_risk_per_orbit": rr,
                              "coverage_mean_faults": float(np.mean([rows[f]["coverage"] for f in range(1, N_FAULTS)])),
                              "out_of_envelope_time_deployed": float(oe_d.mean()), "out_of_envelope_time_no_fdir": float(oe_n.mean())}
    Path(a.out).write_text(json.dumps(res, indent=2))
    print({o: (v["residual_risk_per_orbit"], v["coverage_mean_faults"]) for o, v in res["orbits"].items()})


if __name__ == "__main__":
    main()
