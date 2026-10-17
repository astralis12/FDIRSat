"""Roll out the 4.4 environment and store (6 inputs, expert action, weights)
for supervised defender training (RUNBOOK 4.5).

Behaviour policy, chosen per environment and re-drawn every ~10 min:
  expert (0.5), a random action held until the next re-draw (0.3), none (0.2).
So the data covers the states the defender's own actions lead to, not only
the expert's trajectory. Samples are taken every `--every` seconds.

Writes data/defender/<split>_s<seed>.npz (data/ is not committed).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim"))
from env_v2 import EPSEnv, EnvConfig  # noqa: E402

P_EXPERT, P_RANDOM = 0.5, 0.3
REDRAW_S = 600.0


def rollout(seed: int, n_env: int, orbits: float, every: int, orbit: str) -> dict:
    env = EPSEnv(EnvConfig(n_env=n_env, seed=seed, orbit=orbit, episode_orbits=orbits))
    g = torch.Generator(device=env.dev).manual_seed(10_000 + seed)
    steps = int(orbits * env.orbit.period_s / env.cfg.dt)
    mode = torch.zeros(n_env, dtype=torch.long, device=env.dev)
    rand_a = torch.zeros(n_env, 3, dtype=torch.long, device=env.dev)
    keep = {k: [] for k in ("obs", "expert", "fault", "severity", "invariant", "hazard", "ekf_std", "soc_true")}
    expert = env.expert_action()
    for k in range(steps):
        if k % int(REDRAW_S / env.cfg.dt) == 0:
            u = torch.rand(n_env, generator=g, device=env.dev)
            mode = (u >= P_EXPERT).long() + (u >= P_EXPERT + P_RANDOM).long()      # 0 expert, 1 random, 2 none
            rand_a = torch.randint(0, 4, (n_env, 3), generator=g, device=env.dev)
        act = torch.where((mode == 0)[:, None], expert, torch.where((mode == 1)[:, None], rand_a, torch.zeros_like(rand_a)))
        obs, expert, info = env.step(act)
        if k % every == 0:
            inv = (expert != env.resp[info["fault"]]).any(1)          # an SGO invariant changed the label
            haz = torch.stack(list(env.hazards().values()), 1).any(1)
            keep["obs"].append(obs.cpu())
            keep["expert"].append(expert.to(torch.int8).cpu())
            keep["fault"].append(info["fault"].to(torch.int8).cpu())
            keep["severity"].append(env.severity[info["fault"]].to(torch.int8).cpu())
            keep["invariant"].append(inv.cpu())
            keep["hazard"].append(haz.cpu())
            keep["ekf_std"].append(info["ekf_std"].cpu())
            keep["soc_true"].append(info["soc_true_mean"].cpu())
    return {k: torch.cat(v).numpy() for k, v in keep.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--split", default="train")
    ap.add_argument("--n-env", type=int, default=16384)
    ap.add_argument("--orbits", type=float, default=3.0)
    ap.add_argument("--every", type=int, default=30)
    ap.add_argument("--orbit", default="xjtu")
    ap.add_argument("--out", default=str(ROOT / "data" / "defender"))
    a = ap.parse_args()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    for s in a.seeds:
        t0 = time.time()
        d = rollout(s, a.n_env, a.orbits, a.every, a.orbit)
        np.savez_compressed(Path(a.out) / f"{a.split}_{a.orbit}_s{s}.npz", **d,
                            meta=np.array([s, a.n_env, a.orbits, a.every]))
        print(f"{a.split} seed {s}: {d['obs'].shape[0]} samples, fault share {np.mean(d['fault'] > 0):.2f}, "
              f"labelled action share {np.mean(d['expert'].sum(1) > 0):.2f}, {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
