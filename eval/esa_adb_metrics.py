"""Event-wise detection metrics of the deployed tree + SGO on XJTU-SPS AD Test
(RUNBOOK 4.8), in the spirit of ESA-ADB event-wise scoring.

The tree was trained only in the 4.4 environment, so this is a transfer test
on a different (4S) power system, not a tuned result.
  inputs  xjtu_sps.per_unit (BAT2), SoC from the onboard EKF run over each
          AD segment (EKF current in A of the table cell: c_rate * Q_table)
  alarm   any tree + SGO action > 0 or any SGO fault flag
  events  true: contiguous AnomalyLabel = 1 runs; predicted: alarm runs, gaps
          < MERGE_S merged
  scores  event-wise precision (predicted events touching a true event),
          recall (true events touched), F0.5; recall per fault class; false
          alarm share of the normal segment
ISS-029: every metric is computed twice, with measured temperature and with
temperature fixed at T_FIXED (removes the per-recording temperature offset).
Writes results/esa_adb_metrics.json.
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
import xjtu_sps as X  # noqa: E402
from env_v2 import BatchedEKF, Tables  # noqa: E402
from features import Features  # noqa: E402
from fdir_torch import GpuTree, sgo_v2  # noqa: E402

MERGE_S = 60
T_FIXED = 25.0


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    d = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def merge(ev, gap):
    out = []
    for a, b in ev:
        if out and a - out[-1][1] < gap:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def ekf_soc(pu, seg, tab, dev) -> np.ndarray:
    """Onboard EKF over every AD segment in parallel (segments padded)."""
    groups = [np.flatnonzero(seg == s) for s in np.unique(seg)]
    n = max(len(g) for g in groups)
    V = np.full((len(groups), n), np.nan, np.float32)
    I = np.zeros((len(groups), n), np.float32)
    DT = np.ones((len(groups), n), np.float32)
    for k, g in enumerate(groups):
        V[k, :len(g)] = pu["v_cell"].values[g]
        I[k, :len(g)] = pu["c_rate"].values[g] * tab.q_ah
        DT[k, 1:len(g)] = np.clip(np.diff(pu["t_s"].values[g]), 0, 60)
    v, i, dt = (torch.from_numpy(x).to(dev) for x in (V, I, DT))
    # start from the OCV-consistent SoC of the first sample (rested start)
    grid = torch.linspace(0, 1, 2001, device=dev)
    ocv = tab.ocv(grid)
    soc0 = grid[torch.argmin((ocv[None, :] - v[:, :1]).abs(), 1)]
    ekf = BatchedEKF(tab, soc0)
    out = torch.zeros_like(v)
    out[:, 0] = soc0
    for k in range(1, n):
        # XJTU-SPS is sampled at 1 Hz (97.7 % of steps are 1 s, results/xjtu_sps_ranges.json);
        # the batched EKF takes one dt for all segments. Padding (NaN -> 0 V) skips the update.
        ekf.step(i[:, k - 1], i[:, k], torch.nan_to_num(v[:, k], nan=0.0), 1.0)
        out[:, k] = ekf.soc
    soc = np.empty(len(seg), np.float32)
    o = out.cpu().numpy()
    for k, g in enumerate(groups):
        soc[g] = o[k, :len(g)]
    return soc


def history(x: np.ndarray, seg: np.ndarray) -> np.ndarray:
    """ADR-0019 features, restarted at every AD segment (a separate recording)."""
    out = np.zeros((len(x), 3), np.float32)
    for s in np.unique(seg):
        g = np.flatnonzero(seg == s)
        f = Features(1, "cpu")
        xs = torch.from_numpy(x[g].astype(np.float32))
        out[g] = torch.stack([f.step(xs[k:k + 1, 0], xs[k:k + 1, 3], xs[k:k + 1, 4])[0] for k in range(len(g))]).numpy()
    return out


def score(alarm: np.ndarray, ad) -> dict:
    true_ev = []
    for s in range(1, 18):
        m = (ad["segment"].values == s) & (ad["anomaly"].values == 1)
        true_ev += [(a, b, s) for a, b in runs(m)]
    pred_ev = merge(runs(alarm), MERGE_S)
    hit_true = [any(pa < b and pb > a for pa, pb in pred_ev) for a, b, _ in true_ev]
    anom = ad["anomaly"].values == 1
    pred_ok = [bool(anom[pa:pb].any()) for pa, pb in pred_ev]
    P = float(np.mean(pred_ok)) if pred_ev else 0.0
    R = float(np.mean(hit_true)) if true_ev else 0.0
    f05 = 1.25 * P * R / (0.25 * P + R) if P + R > 0 else 0.0
    per_class = {}
    for s in range(1, 18):
        h = [hit for (a, b, c), hit in zip(true_ev, hit_true) if c == s]
        per_class[s] = float(np.mean(h)) if h else None
    normal = ad["segment"].values == 0
    return {"precision_event": P, "recall_event": R, "f0_5_event": f05, "n_true_events": len(true_ev),
            "n_pred_events": len(pred_ev), "recall_per_fault_class": per_class,
            "false_alarm_share_normal_segment": float(alarm[normal].mean())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=None)
    ap.add_argument("--out", default=str(ROOT / "results" / "esa_adb_metrics.json"))
    a = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    if a.tree is None:
        dep = json.load(open(ROOT / "results" / "dagger_summary.json"))["deploy"]
        a.tree = str(ROOT / "checkpoints" / "tree" / f"{dep['mode']}_s{dep['seed']}.pkl")
    tree = GpuTree(pickle.load(open(a.tree, "rb")), dev)
    tab = Tables(dev)
    ad = X.load_ad("Test")
    pu = X.per_unit(ad, 2)
    soc = ekf_soc(pu, ad["segment"].values, tab, dev)
    res = {"tree": Path(a.tree).name, "dataset": "XJTU-SPS AD Test", "battery": "BAT2", "merge_s": MERGE_S,
           "q_string_ah_assumed": X.Q_STRING_AH, "variants": {}}
    for name, t_in in (("measured_temperature", pu["t_bat"].values), (f"temperature_fixed_{T_FIXED:g}C", np.full(len(pu), T_FIXED))):
        x = np.stack([pu["v_cell"].values, pu["c_rate"].values, t_in, soc, pu["i_sa_pu"].values, pu["u_bus_pu"].values], 1)
        x = np.concatenate([x, history(x, ad["segment"].values)], 1)
        xt = torch.from_numpy(x.astype(np.float32)).to(dev)
        act, flags = sgo_v2(tree(xt), xt[:, 0], xt[:, 1], xt[:, 2], xt[:, 3])
        alarm = ((act > 0).any(1) | (flags > 0)).cpu().numpy()
        res["variants"][name] = score(alarm, ad)
        print(name, {k: v for k, v in res["variants"][name].items() if k != "recall_per_fault_class"})
    Path(a.out).write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
