"""On-target check of the deployed FDIR chain on the DUT (STM32F411).

  tree  'T' on inputs from three sources: uniform over the input box, near the
        SGO thresholds, and states visited in the 4.4 environment. The DUT
        tree must equal sklearn's predict exactly (float32 inputs).
  chain 'X' replays one environment measurement trajectory through the DUT's
        full 1 Hz loop (EKF -> tree -> SGO v2). Checks: DUT SoC within 1e-4 of
        the Python reference EKF, and the host tree + SGO on the DUT's own SoC
        gives exactly the DUT's actions and flags.
Reports cycles per call (tree, full loop). Writes results/hil_fdir.json.
"""
from __future__ import annotations

import argparse
import json
import pickle
import struct
import sys
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
for p in ("sim", "train", "codegen", "verify"):
    sys.path.insert(0, str(ROOT / p))
from hil_ekf import Dut  # noqa: E402
from dagger_adversary import BOX, THRESH  # noqa: E402
from fdir_torch import sgo_v2  # noqa: E402
from features import Features  # noqa: E402

TOL_SOC = 1e-4


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--n-tree", type=int, default=6000)
    ap.add_argument("--chain-steps", type=int, default=3000)
    ap.add_argument("--out", default=str(ROOT / "results" / "hil_fdir.json"))
    a = ap.parse_args()
    dep = json.load(open(ROOT / "results" / "dagger_summary.json"))["deploy"]
    pk = ROOT / "checkpoints" / "tree" / f"{dep['mode']}_s{dep['seed']}.pkl"
    clf = pickle.load(open(pk, "rb"))
    rng = np.random.default_rng(7)
    dut = Dut(a.port)
    _, ver = dut.call(b"P")
    res = {"firmware": ver.decode(), "tree": pk.name}

    # --- tree: uniform, boundary, environment states
    k_in = clf.n_features_in_
    xu = rng.uniform(BOX[:k_in, 0], BOX[:k_in, 1], size=(a.n_tree // 3, k_in)).astype(np.float32)
    xb = rng.uniform(BOX[:k_in, 0], BOX[:k_in, 1], size=(a.n_tree // 3, k_in)).astype(np.float32)
    for j in range(len(xb)):
        f = rng.integers(0, 4)
        th = THRESH[f][rng.integers(0, len(THRESH[f]))]
        xb[j, f] = np.float32(th + rng.normal(0, 0.01 * max(abs(th), 0.05)))
    val = np.load(ROOT / "data" / "defender9" / "val_xjtu_s100.npz")["obs"]
    xe = val[rng.choice(len(val), a.n_tree // 3, replace=False)].astype(np.float32)
    x = np.concatenate([xu, xb, xe])
    ref = clf.predict(x).astype(int)
    got, cyc = np.zeros_like(ref), []
    t0 = time.time()
    for k, row in enumerate(x):
        c, d = dut.call(b"T", struct.pack(f"<{k_in}f", *row))
        got[k] = list(d[:3])
        cyc.append(struct.unpack("<I", d[3:7])[0])
    mism = int((got != ref).any(1).sum())
    res["tree"] = {"n": int(len(x)), "mismatches": mism, "cycles_max": int(max(cyc)), "cycles_median": float(np.median(cyc)),
                   "wall_s": time.time() - t0}
    print("tree", res["tree"], flush=True)

    # --- full chain on one environment trajectory (open loop replay of its measurements)
    from env_v2 import EPSEnv, EnvConfig
    from ecm_ekf import run_ekf
    from emit_ekf_tables import reference_model
    env = EPSEnv(EnvConfig(n_env=1, seed=31, fault_prob=1.0, device="cpu"))
    env.fault[:] = 10                                  # bus short: exercises the SGO invariants
    env.t_onset[:] = 0.5 * a.chain_steps
    meas = []
    i_prev_c = 0.0
    for k in range(a.chain_steps):
        obs, _, info = env.step(env.expert_action())
        meas.append([i_prev_c * env.tab.q_ah, float(obs[0, 1]) * env.tab.q_ah, float(obs[0, 0]), 1.0,
                     float(obs[0, 1]), float(obs[0, 2]), float(obs[0, 4]), float(obs[0, 5])])
        i_prev_c = float(obs[0, 1])
    meas = np.asarray(meas, np.float32)
    soc0 = 0.6                                         # onboard EKF start (cold start, as after a reset)
    dut.call(b"I", struct.pack("<f", soc0))
    ecm = json.load(open(ROOT / "results" / "ecm_scheduled.json"))
    cv = json.load(open(ROOT / "results" / "ekf_sigma_cv.json"))
    tab, ocv, n = reference_model(ecm, cv, ROOT / "data" / "dfn")
    t = np.arange(len(meas) + 1, dtype=float)
    i_seq = np.concatenate([[0.0], meas[:, 1]])
    v_seq = np.concatenate([[3.8], meas[:, 2]])
    refe = run_ekf(tab, ocv, t, i_seq, v_seq, soc0, p0_soc=n["p0_soc"], q_soc=n["q_soc"], q_rc=n["q_rc"], r_v=n["r_v"])
    out = []
    ccyc = []
    for k, m in enumerate(meas):
        c, d = dut.call(b"X", struct.pack("<8f", *m))
        soc, sd = struct.unpack("<2f", d[:8])
        out.append([soc, sd, *d[8:13]])
        ccyc.append(struct.unpack("<I", d[13:17])[0])
    out = np.asarray(out, float)
    dsoc = np.abs(out[:, 0] - refe["soc_est"][1:])
    xin = np.stack([meas[:, 2], meas[:, 4], meas[:, 5], out[:, 0].astype(np.float32), meas[:, 6], meas[:, 7]], 1).astype(np.float32)
    hist = Features(1, "cpu")                          # host twin of the DUT's history features
    h = np.stack([hist.step(torch.tensor([r[0]]), torch.tensor([r[3]]), torch.tensor([r[4]]))[0].numpy() for r in xin])
    xin = np.concatenate([xin, h], 1).astype(np.float32)
    xt = torch.from_numpy(xin)
    tree_out = torch.from_numpy(clf.predict(xin).astype(np.int64))
    act, flags = sgo_v2(tree_out, xt[:, 0], xt[:, 1], xt[:, 2], xt[:, 3])
    act_mis = int(((act.numpy() != out[:, 2:5]).any(1)).sum())
    flag_mis = int((flags.numpy() != out[:, 5]).sum())
    res["chain"] = {"steps": int(len(meas)), "fault": 10, "max_abs_dsoc": float(dsoc.max()), "soc_mismatches": int((dsoc > TOL_SOC).sum()),
                    "action_mismatches": act_mis, "flag_mismatches": flag_mis, "ekf_guard_activations": int(np.count_nonzero(out[:, 6])),
                    "cycles_max": int(max(ccyc)), "cycles_median": float(np.median(ccyc)),
                    "max_us_at_16MHz": float(1e6 * max(ccyc) / 16e6), "actions_seen": sorted({tuple(r) for r in out[:, 2:5].astype(int).tolist()})}
    print("chain", res["chain"], flush=True)
    g = mism + res["chain"]["soc_mismatches"] + act_mis + flag_mis
    res["gate"] = {"metric": "tree + chain mismatches on target", "value": g, "pass": g == 0}
    Path(a.out).write_text(json.dumps(res, indent=2, default=str))
    print("GATE", res["gate"])
    sys.exit(0 if res["gate"]["pass"] else 1)


if __name__ == "__main__":
    main()
