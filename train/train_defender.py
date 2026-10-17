"""Supervised severity-weighted defender (RUNBOOK 4.5).

Model: the plan's "supervised multi-head" network: 6 per-unit inputs -> shared
MLP backbone -> three heads (S, B, L), 4 classes each.
Loss: cross-entropy per head, weighted per sample by the FMECA severity of the
true fault (config/fmeca_xjtu_sps.json, PROPOSED) and raised where an SGO
invariant is active on the true state:
    w = W_SEV[severity] * (W_INVARIANT if invariant else 1)
Data: train/gen_data.py rollouts (train seeds) and a held-out environment seed.

Logging: W&B (offline unless WANDB_MODE says otherwise), plus results JSON.
Checkpoints: final and best (validation loss) per seed only (CLAUDE.md).
Convergence (defined before the runs): final validation loss within 2 % of the
best and changed by < 1 % over the last 10 % of epochs.

Usage: python train/train_defender.py --seed N
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim"))
from env_v2 import INPUT_NAMES, N_INPUTS  # noqa: E402

# input scaling: per-unit inputs are O(1) already; temperature in units of 60 C
IN_SCALE = torch.tensor([4.2, 1.0, 60.0, 1.0, 1.0, 1.0])
IN_SHIFT = torch.tensor([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
W_SEV = torch.tensor([1.0, 4.0, 3.0, 2.0, 1.0])     # index = severity 0 (normal) .. 4
W_INVARIANT = 4.0
HEADS = ("S", "B", "L")


class Defender(nn.Module):
    def __init__(self, hidden: int = 64, depth: int = 2):
        super().__init__()
        layers, d = [], N_INPUTS
        for _ in range(depth):
            layers += [nn.Linear(d, hidden), nn.ReLU()]
            d = hidden
        self.backbone = nn.Sequential(*layers)
        self.heads = nn.ModuleList(nn.Linear(hidden, 4) for _ in HEADS)
        self.register_buffer("scale", IN_SCALE.clone())
        self.register_buffer("shift", IN_SHIFT.clone())

    def forward(self, x):
        h = self.backbone((x - self.shift) / self.scale)
        return torch.stack([hd(h) for hd in self.heads], 1)            # (N, 3, 4)

    @torch.no_grad()
    def act(self, x):
        return self.forward(x).argmax(-1)


def load_split(files) -> dict:
    parts = [np.load(f) for f in files]
    keys = ("obs", "expert", "fault", "severity", "invariant", "hazard")
    return {k: np.concatenate([p[k] for p in parts]) for k in keys}


def weights(sev: torch.Tensor, inv: torch.Tensor) -> torch.Tensor:
    w = W_SEV.to(sev.device)[sev.long()]
    return w * torch.where(inv, torch.full_like(w, W_INVARIANT), torch.ones_like(w))


def evaluate(model, d, dev, batch=1 << 16) -> dict:
    model.eval()
    ce = nn.CrossEntropyLoss(reduction="none")
    tot_l, tot_w, correct, n, sev_err, sev_w = 0.0, 0.0, torch.zeros(3), 0, 0.0, 0.0
    per_fault_ok = torch.zeros(18)
    per_fault_n = torch.zeros(18)
    with torch.no_grad():
        for i in range(0, d["obs"].shape[0], batch):
            x = d["obs"][i:i + batch].to(dev)
            y = d["expert"][i:i + batch].to(dev).long()
            w = weights(d["severity"][i:i + batch].to(dev), d["invariant"][i:i + batch].to(dev))
            out = model(x)
            l = sum(ce(out[:, h], y[:, h]) for h in range(3))
            tot_l += float((l * w).sum())
            tot_w += float(w.sum())
            pred = out.argmax(-1)
            ok = pred == y
            correct += ok.float().sum(0).cpu()
            n += x.shape[0]
            allok = ok.all(1)
            sev_err += float((w * (~allok).float()).sum())
            sev_w += float(w.sum())
            f = d["fault"][i:i + batch].long()
            per_fault_ok.index_add_(0, f, allok.float().cpu())
            per_fault_n.index_add_(0, f, torch.ones_like(f, dtype=torch.float))
    model.train()
    return {"loss": tot_l / tot_w, "acc_per_head": (correct / n).tolist(),
            "severity_weighted_error": sev_err / sev_w,
            "exact_acc_per_fault": {int(k): float(per_fault_ok[k] / per_fault_n[k]) for k in range(18) if per_fault_n[k] > 0}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--data", default=str(ROOT / "data" / "defender"))
    ap.add_argument("--orbit", default="xjtu")
    ap.add_argument("--train-seeds", type=int, nargs="+", default=[0, 1, 2, 3])
    ap.add_argument("--val-seeds", type=int, nargs="+", default=[100])
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=8192)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--hidden", type=int, default=64)
    ap.add_argument("--ckpt", default=str(ROOT / "checkpoints" / "defender"))
    ap.add_argument("--out", default=str(ROOT / "results" / "defender"))
    a = ap.parse_args()

    torch.manual_seed(a.seed)
    np.random.seed(a.seed)
    torch.backends.cudnn.benchmark = False
    torch.use_deterministic_algorithms(True, warn_only=True)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    tr = {k: torch.from_numpy(v) for k, v in load_split([Path(a.data) / f"train_{a.orbit}_s{s}.npz" for s in a.train_seeds]).items()}
    va = {k: torch.from_numpy(v) for k, v in load_split([Path(a.data) / f"val_{a.orbit}_s{s}.npz" for s in a.val_seeds]).items()}
    X, Y = tr["obs"].to(dev), tr["expert"].to(dev).long()
    Wt = weights(tr["severity"].to(dev), tr["invariant"].to(dev))
    n = X.shape[0]

    os.environ.setdefault("WANDB_MODE", "offline")
    os.environ.setdefault("WANDB_DIR", str(ROOT / "wandb_runs"))
    Path(os.environ["WANDB_DIR"]).mkdir(exist_ok=True)
    import wandb
    run = wandb.init(project="fdir-v2-defender", name=f"defender_s{a.seed}", config={**vars(a), "n_train": n,
                     "W_SEV": W_SEV.tolist(), "W_INVARIANT": W_INVARIANT, "inputs": INPUT_NAMES})

    model = Defender(a.hidden).to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=a.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=a.epochs)
    ce = nn.CrossEntropyLoss(reduction="none")
    g = torch.Generator(device=dev).manual_seed(a.seed)
    ck = Path(a.ckpt)
    ck.mkdir(parents=True, exist_ok=True)
    hist, best = [], float("inf")
    t0 = time.time()
    for ep in range(a.epochs):
        perm = torch.randperm(n, generator=g, device=dev)
        tl = 0.0
        for i in range(0, n, a.batch):
            idx = perm[i:i + a.batch]
            out = model(X[idx])
            l = sum(ce(out[:, h], Y[idx, h]) for h in range(3))
            loss = (l * Wt[idx]).sum() / Wt[idx].sum()
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tl += float(loss) * idx.numel()
        sched.step()
        ev = evaluate(model, va, dev)
        hist.append({"epoch": ep, "train_loss": tl / n, "val_loss": ev["loss"],
                     "val_sev_err": ev["severity_weighted_error"], "val_acc": ev["acc_per_head"]})
        wandb.log({"train_loss": tl / n, "val_loss": ev["loss"], "val_sev_err": ev["severity_weighted_error"],
                   **{f"val_acc_{h}": v for h, v in zip(HEADS, ev["acc_per_head"])}}, step=ep)
        if ev["loss"] < best:
            best = ev["loss"]
            torch.save({"state": model.state_dict(), "epoch": ep, "val": ev, "args": vars(a)}, ck / f"s{a.seed}_best.pt")
        print(f"seed {a.seed} ep {ep:2d} train {tl / n:.4f} val {ev['loss']:.4f} sev-err {ev['severity_weighted_error']:.4f}", flush=True)
    torch.save({"state": model.state_dict(), "epoch": a.epochs - 1, "val": ev, "args": vars(a)}, ck / f"s{a.seed}_final.pt")
    vl = [h["val_loss"] for h in hist]
    tail = max(1, a.epochs // 10)
    conv = {"final_within_2pct_of_best": vl[-1] <= 1.02 * min(vl),
            "last_10pct_change_below_1pct": abs(vl[-1] - vl[-1 - tail]) <= 0.01 * vl[-1 - tail]}
    res = {"seed": a.seed, "device": dev, "wall_s": time.time() - t0, "n_train": n, "n_val": int(va["obs"].shape[0]),
           "final": ev, "best_val_loss": min(vl), "history": hist, "converged": all(conv.values()), "convergence": conv}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    (Path(a.out) / f"s{a.seed}.json").write_text(json.dumps(res, indent=2))
    run.summary.update({"converged": res["converged"], "best_val_loss": min(vl)})
    run.finish()
    print("converged", res["converged"], conv)


if __name__ == "__main__":
    main()
