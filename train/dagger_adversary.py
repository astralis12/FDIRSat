"""Closed-loop distillation of the defender into a decision tree (RUNBOOK 4.6):
severity-weighted DAgger with an adversarial stress tester; behaviour cloning
(BC) and plain (unweighted) DAgger as ablations.

  oracle    the 4.5 defender of the same seed (checkpoints/defender/s<seed>_best.pt)
  D0        uniform samples over the input box + samples near the SGO
            thresholds, labelled by the oracle (BC = fit on D0 only)
  round i   roll out beta_i * oracle + (1 - beta_i) * (tree + SGO) in the
            batched environment, label every visited state with the oracle,
            aggregate, refit the tree at fixed depth with sample weights
  weights   FMECA severity of the true fault x invariant factor (as 4.5);
            plain DAgger uses weight 1
  adversary each round, a cross-entropy-method search over scenario parameters
            (fault id, onset, magnitude, initial SoC, orbit phase) for runs
            where tree + SGO disagrees with the oracle on a severe action or
            reaches a hazard; its visited states join D. Random fuzzing with
            the same simulation budget is the comparison.
Rollouts are CUDA-graph captured (train/graph_rollout.py, checked equal to eager).
Selection: the final tree is the round with the lowest closed-loop
severity-weighted disagreement on HELD-OUT environment seeds (never on
training fidelity, plan step 5).

Reports closed-loop and uniform fidelity (agreement tree vs oracle), per seed.
Gate (over seeds, computed by train/summarise.py): 95 % CI half-width of the
closed-loop fidelity <= 1.5 pp.

Usage: python train/dagger_adversary.py --seed N --mode {sw_dagger, dagger, bc}
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.tree import DecisionTreeClassifier

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim"))
sys.path.insert(0, str(ROOT / "train"))
from env_v2 import EPSEnv, EnvConfig, N_FAULTS  # noqa: E402
from fdir_torch import GpuTree, sgo_v2  # noqa: E402
from train_defender import W_SEV, Defender  # noqa: E402
from graph_rollout import GraphStep  # noqa: E402

MAX_DEPTH = 12                     # plan: at most 12 gather steps on the GPU / MCU
ROUNDS = 8
BOX = np.array([[2.4, 4.4], [-3.5, 1.5], [-10.0, 90.0], [-0.1, 1.1], [0.0, 1.1], [0.0, 1.05]], np.float32)
THRESH = {0: (3.0, 3.6), 1: (1.5,), 2: (60.0,), 3: (0.05,)}      # SGO thresholds per input
D_CAP = 3_000_000                  # cap on the aggregated dataset (random subsample beyond)


def oracle_for(seed: int, dev: str) -> Defender:
    ck = torch.load(ROOT / "checkpoints" / "defender" / f"s{seed}_best.pt", map_location=dev, weights_only=False)
    m = Defender(ck["args"]["hidden"]).to(dev)
    m.load_state_dict(ck["state"])
    m.eval()
    return m


def d0(oracle, n: int, rng: np.random.Generator, dev: str) -> dict:
    x = rng.uniform(BOX[:, 0], BOX[:, 1], size=(n, 6)).astype(np.float32)
    nb = n // 4                                    # boundary samples around SGO thresholds
    xb = rng.uniform(BOX[:, 0], BOX[:, 1], size=(nb, 6)).astype(np.float32)
    for j in range(nb):
        f = rng.integers(0, 4)
        th = THRESH[f][rng.integers(0, len(THRESH[f]))]
        xb[j, f] = th + rng.normal(0, 0.02 * max(abs(th), 0.05))
    x = np.concatenate([x, xb])
    with torch.no_grad():
        y = oracle.act(torch.from_numpy(x).to(dev)).cpu().numpy().astype(np.int8)
    return {"x": x, "y": y, "w": np.ones(len(x), np.float32)}


def fit_tree(D: dict, weighted: bool, seed: int) -> DecisionTreeClassifier:
    clf = DecisionTreeClassifier(max_depth=MAX_DEPTH, random_state=seed, min_samples_leaf=20)
    clf.fit(D["x"], D["y"], sample_weight=D["w"] if weighted else None)
    return clf


def rollout(policy_tree, oracle, beta: float, cfg: EnvConfig, every: int, scen: dict | None = None) -> dict:
    """Closed loop: per step, the oracle acts with probability beta, else tree + SGO.
    Returns visited states (oracle labels, weights) and disagreement / hazard stats."""
    env = EPSEnv(cfg)
    if scen is not None:                           # adversary / fuzzing scenarios
        env.fault[:] = scen["fault"]
        env.t_onset[:] = scen["onset"]
        env.mag[:] = scen["mag"]
        env.soc[:] = (scen["soc0"][:, None] + 0.01 * torch.randn_like(env.soc)).clamp(0.02, 0.99)
        env.phase0[:] = scen["phase"]
        from env_v2 import BatchedEKF
        env.ekf = BatchedEKF(env.tab, (env.soc.mean(1) + 0.05 * torch.randn_like(env.soc[:, 0])).clamp(0.05, 0.98))
    g = torch.Generator(device=env.dev).manual_seed(cfg.seed + 7)
    steps = int(cfg.episode_orbits * env.orbit.period_s / cfg.dt)
    w_sev = W_SEV.to(env.dev)
    z = torch.zeros(cfg.n_env, device=env.dev)
    # accumulators live on the env so the graph step advances them in place
    env.acc_hazard = z.bool()
    env.acc_dis, env.acc_wdis, env.acc_w, env.acc_severe = z.clone(), z.clone(), z.clone(), z.clone()

    def fn(e):
        obs = e.obs.clone()
        with torch.no_grad():
            a_or = oracle.act(obs)
        a_tr = policy_tree(obs) if policy_tree is not None else a_or
        a_dep, _ = sgo_v2(a_tr, obs[:, 0], obs[:, 1], obs[:, 2], obs[:, 3])
        use_or = torch.rand(cfg.n_env, generator=g, device=e.dev) < beta
        act = torch.where(use_or[:, None], a_or, a_dep)
        _, _, info = e.step(act)
        sev = e.severity[info["fault"]]
        w = w_sev[sev.long()]
        mis = (a_tr != a_or).any(1).float()
        e.acc_hazard = e.acc_hazard | torch.stack(list(e.hazards().values()), 1).any(1)
        e.acc_dis = e.acc_dis + mis
        e.acc_wdis = e.acc_wdis + w * mis
        e.acc_w = e.acc_w + w
        e.acc_severe = e.acc_severe + mis * ((sev == 1) | (sev == 2)).float()
        return {"x": obs, "y": a_or, "w": w}

    step = GraphStep(env, fn, generators=(g,)) if env.dev == "cuda" else (lambda: fn(env))
    xs, ys, ws = [], [], []
    for k in range(steps):
        o = step()
        if k % every == 0:
            xs.append(o["x"].cpu())
            ys.append(o["y"].to(torch.int8).cpu())
            ws.append(o["w"].cpu())
    n_eval = steps
    return {"x": torch.cat(xs).numpy(), "y": torch.cat(ys).numpy(), "w": torch.cat(ws).numpy(),
            "fidelity": float(1 - env.acc_dis.sum() / (n_eval * cfg.n_env)),
            "sev_weighted_disagreement": float(env.acc_wdis.sum() / env.acc_w.sum()),
            "per_env_score": (env.acc_wdis / env.acc_w.clamp_min(1e-9) + env.acc_severe / n_eval + env.acc_hazard.float()).cpu(),
            "hazard_rate": float(env.acc_hazard.float().mean()), "fault": env.fault.cpu()}


def uniform_fidelity(tree, oracle, rng, dev, n=500_000) -> float:
    x = torch.from_numpy(rng.uniform(BOX[:, 0], BOX[:, 1], size=(n, 6)).astype(np.float32)).to(dev)
    with torch.no_grad():
        return float((tree(x) == oracle.act(x)).all(1).float().mean())


def scenarios(n: int, mean: dict | None, gen: torch.Generator, dev: str, horizon_s: float) -> dict:
    """Sample scenario parameters: random (mean None) or around a CEM elite distribution."""
    u = lambda: torch.rand(n, generator=gen, device=dev)
    if mean is None:
        return {"fault": torch.randint(0, N_FAULTS, (n,), generator=gen, device=dev),
                "onset": (0.05 + 0.6 * u()) * horizon_s, "mag": 0.3 + 0.7 * u(),
                "soc0": 0.3 + 0.65 * u(), "phase": u() * 5700.0}
    probs = mean["fault_p"]
    f = torch.multinomial(probs, n, replacement=True, generator=gen)
    clip = lambda x, lo, hi: x.clamp(lo, hi)
    rn = lambda: torch.randn(n, generator=gen, device=dev)
    return {"fault": f, "onset": clip(mean["onset"][0] + mean["onset"][1] * rn(), 0.02 * horizon_s, 0.8 * horizon_s),
            "mag": clip(mean["mag"][0] + mean["mag"][1] * rn(), 0.3, 1.0),
            "soc0": clip(mean["soc0"][0] + mean["soc0"][1] * rn(), 0.1, 0.97), "phase": u() * 5700.0}


def cem_update(s: dict, score: torch.Tensor, dev: str, elite_frac=0.1) -> dict:
    k = max(8, int(elite_frac * score.numel()))
    idx = torch.topk(score.to(dev), k).indices
    fp = torch.bincount(s["fault"][idx], minlength=N_FAULTS).float() + 0.5
    st = lambda v: (float(v[idx].mean()), float(v[idx].std()) + 1e-3)
    return {"fault_p": fp / fp.sum(), "onset": st(s["onset"]), "mag": st(s["mag"]), "soc0": st(s["soc0"])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--mode", default="sw_dagger", choices=["sw_dagger", "dagger", "bc"])
    ap.add_argument("--n-env", type=int, default=16384)
    ap.add_argument("--orbits", type=float, default=1.0)
    ap.add_argument("--every", type=int, default=60)
    ap.add_argument("--rounds", type=int, default=ROUNDS)
    ap.add_argument("--adv-env", type=int, default=4096)
    ap.add_argument("--eval-seeds", type=int, nargs="+", default=[900, 901])
    ap.add_argument("--out", default=str(ROOT / "results" / "dagger"))
    a = ap.parse_args()
    torch.manual_seed(a.seed)
    rng = np.random.default_rng(a.seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    oracle = oracle_for(a.seed, dev)
    weighted = a.mode == "sw_dagger"
    D = d0(oracle, 400_000, rng, dev)
    gen = torch.Generator(device=dev).manual_seed(20_000 + a.seed)
    base = dict(n_env=a.n_env, episode_orbits=a.orbits, fault_prob=0.9)
    horizon = a.orbits * 5700.0
    hist, best = [], None
    adv_mean, adv_found, fuzz_found = None, 0, 0
    t0 = time.time()

    def evaluate(tree):
        """Closed-loop fidelity on held-out environment seeds (oracle never acts)."""
        r = [rollout(tree, oracle, 0.0, EnvConfig(**base, seed=s), a.every) for s in a.eval_seeds]
        return {"fidelity_closed_loop": float(np.mean([x["fidelity"] for x in r])),
                "sev_weighted_disagreement": float(np.mean([x["sev_weighted_disagreement"] for x in r])),
                "hazard_rate": float(np.mean([x["hazard_rate"] for x in r]))}

    rounds = 1 if a.mode == "bc" else a.rounds
    for i in range(rounds):
        clf = fit_tree(D, weighted, a.seed)
        tree = GpuTree(clf, dev)
        ev = evaluate(tree)
        ev.update({"round": i, "n_data": int(len(D["x"])), "depth": int(clf.get_depth()), "leaves": int(clf.get_n_leaves()),
                   "fidelity_uniform": uniform_fidelity(tree, oracle, rng, dev)})
        hist.append(ev)
        if best is None or ev["sev_weighted_disagreement"] < best[0]["sev_weighted_disagreement"]:
            best = (ev, clf)
        print(f"seed {a.seed} {a.mode} round {i}: {ev}", flush=True)
        if a.mode == "bc" or i == rounds - 1:
            break
        beta = 0.5 ** (i + 1)
        r = rollout(tree, oracle, beta, EnvConfig(**base, seed=1000 * a.seed + i), a.every)
        parts = [D, {"x": r["x"], "y": r["y"], "w": r["w"] if weighted else np.ones_like(r["w"])}]
        # adversary (CEM over scenarios) and random fuzzing, same budget
        if a.mode == "sw_dagger":
            sc = scenarios(a.adv_env, adv_mean, gen, dev, horizon)
            ra = rollout(tree, oracle, 0.0, EnvConfig(**{**base, "n_env": a.adv_env}, seed=5000 + 100 * a.seed + i), a.every, sc)
            adv_mean = cem_update(sc, ra["per_env_score"], dev)
            adv_found += int((ra["per_env_score"] >= 1.0).sum())
            sf = scenarios(a.adv_env, None, gen, dev, horizon)
            rf = rollout(tree, oracle, 0.0, EnvConfig(**{**base, "n_env": a.adv_env}, seed=7000 + 100 * a.seed + i), a.every, sf)
            fuzz_found += int((rf["per_env_score"] >= 1.0).sum())
            parts.append({"x": ra["x"], "y": ra["y"], "w": ra["w"]})
        D = {k: np.concatenate([p[k] for p in parts]) for k in ("x", "y", "w")}
        if len(D["x"]) > D_CAP:                     # keep D0 (uniform part) + a random subsample of the rest
            keep = np.concatenate([np.arange(400_000 + 100_000), rng.choice(np.arange(500_000, len(D["x"])), D_CAP - 500_000, replace=False)])
            D = {k: v[keep] for k, v in D.items()}

    ev_best, clf_best = best
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (ROOT / "checkpoints" / "tree").mkdir(parents=True, exist_ok=True)
    with open(ROOT / "checkpoints" / "tree" / f"{a.mode}_s{a.seed}.pkl", "wb") as f:
        pickle.dump(clf_best, f)
    res = {"seed": a.seed, "mode": a.mode, "max_depth": MAX_DEPTH, "rounds": rounds, "wall_s": time.time() - t0,
           "selected_round": ev_best["round"], "selected": ev_best, "history": hist,
           "adversary_failures_found": adv_found, "fuzzing_failures_found": fuzz_found,
           "adversary_budget_envs": a.adv_env * (rounds - 1) if a.mode == "sw_dagger" else 0}
    (out / f"{a.mode}_s{a.seed}.json").write_text(json.dumps(res, indent=2))
    print("selected", ev_best)


if __name__ == "__main__":
    main()
