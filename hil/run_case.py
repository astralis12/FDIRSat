"""Processor-in-the-loop test cases (RUNBOOK 5.6, before the real pack exists).

The 4.4 plant runs on the laptop; the DUT (STM32F411, firmware/pio/dut) runs
the flight code - EKF -> tree -> SGO v2 - and its actions drive the plant,
closing the loop over the UART at one frame per simulated second. Up to 7
spacecraft share a frame (one EKF instance each on the DUT). Every case also
runs on the host (GPU tree + torch SGO + torch EKF) with the same plant seed,
so PIL and software-in-the-loop (SIL) outcomes can be compared.

Cases
  C1  nominal: no fault, 1.5 orbits
  C2  each FMECA fault class 1..17, onset at 30 % of 1 orbit
  C3  corrupted telemetry and memory upsets, injected at 30 % of the run:
        C3a  NaN cell voltage for 60 s           C3d  all-zero sensor dropout, 30 s
        C3b  stuck temperature -4099 C, 10 min   C3e  EKF state upset in DUT RAM via
        C3c  one-sample current spike +50 C           ST-Link (x[0] = 3.0, P00 = NaN)
  C5  cold battery, no fault (SGO v3 invariant 6, ISS-050; C4 is reserved for the
      capacity mis-estimate case on the bench build):
        C5a  warming: start -6 degC, ambient 8 / 4 degC (sun / eclipse) -> inhibit, then release above 2 degC
        C5b  cold soak: start -4 degC, ambient -3 / -8 degC -> inhibit held, SGO floor keeps the bus up
  C6  cold charge (SGO v3.2 invariant 7, ADR-0024): battery near 10 degC (M50 band 0.3 C),
        C6a  charger derating lost (BCR charges up to 0.5 C) -> invariant 7 must stop the charge
        C6b  charger derating working (control) -> no invariant 7 action expected
Metrics per case: hazard share (PIL and SIL), action agreement PIL vs SIL,
SGO flags seen, EKF guard activations, max cycles per spacecraft-step.

Usage: python hil/run_case.py --case C1 --repeat 5  (or --case all)
Writes results/hil_cases.json (merged per case).
"""
from __future__ import annotations

import argparse
import json
import pickle
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
for p in ("sim", "train", "verify"):
    sys.path.insert(0, str(ROOT / p))
from env_v2 import EPSEnv, EnvConfig  # noqa: E402
from fdir_torch import GpuTree, SgoState, sgo_v2  # noqa: E402
from hil_ekf import Dut  # noqa: E402
from features import Features  # noqa: E402

MULTI = 7
OPENOCD = Path.home() / ".platformio" / "packages" / "tool-openocd"
ELF = ROOT / "firmware" / "pio" / "dut" / ".pio" / "build" / "dut" / "firmware.elf"
NM = Path("C:/arm-gnu-toolchain/bin/arm-none-eabi-nm.exe")


def symbol_addr(name: str) -> int:
    out = subprocess.run([str(NM), str(ELF)], capture_output=True, text=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] == name:
            return int(parts[0], 16)
    raise KeyError(name)


def openocd_write(writes: list[tuple[int, int]]) -> str:
    """Write 32-bit words into the running DUT's RAM over ST-Link (no halt)."""
    cmds = ["init"] + [f"mww 0x{a:08x} 0x{v:08x}" for a, v in writes] + ["shutdown"]
    args = [str(OPENOCD / "bin" / "openocd.exe"), "-s", str(OPENOCD / "share" / "openocd" / "scripts"),
            "-f", "interface/stlink.cfg", "-f", "target/stm32f4x.cfg"]
    for c in cmds:
        args += ["-c", c]
    r = subprocess.run(args, capture_output=True, text=True, timeout=30)
    return r.stderr[-300:]


def f32_bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def case_list(case: str):
    if case == "C1":
        return [("C1", 0, None)], 1.5
    if case == "C2":
        return [("C2", f, None) for f in range(1, 18)], 1.0
    if case == "C3":
        return [(f"C3{k}", 0, k) for k in "abcde"], 1.0
    if case == "C5":
        return [("C5a", 0, "cold_a"), ("C5b", 0, "cold_b")], 1.0
    if case == "C6":
        return [("C6a", 0, "cool_noderate"), ("C6b", 0, "cool_derate")], 1.0
    raise ValueError(case)


def corrupt(meas: torch.Tensor, kind: str | None, t: float, t0: float) -> torch.Tensor:
    """meas columns: v_cell, c_rate, t_bat, i_sa_pu, u_bus_pu (per spacecraft)."""
    if kind is None or kind.startswith("cold") or t < t0:
        return meas
    m = meas.clone()
    if kind == "a" and t < t0 + 60:
        m[:, 0] = float("nan")
    elif kind == "b" and t < t0 + 600:
        m[:, 2] = -4099.4                          # ISS-021 replay
    elif kind == "c" and t < t0 + 1:
        m[:, 1] = m[:, 1] + 50.0
    elif kind == "d" and t < t0 + 30:
        m[:] = 0.0
    return m


def run_batch(dut: Dut, tree, specs, orbits: float, seed: int, mode: str) -> dict:
    """mode 'pil': DUT closes the loop; 'sil': host tree + SGO + env EKF."""
    n = len(specs)
    cfg = EnvConfig(n_env=n, seed=seed, device="cpu", episode_orbits=orbits, fault_prob=1.0)
    cold = {"cold_a": (-6.0, 8.0, 4.0), "cold_b": (-4.0, -3.0, -8.0),   # start, ambient sun, ambient eclipse [degC]
            "cool_noderate": (10.0, 11.0, 9.0), "cool_derate": (10.0, 11.0, 9.0)}
    if any(s[2] == "cool_noderate" for s in specs):
        cfg.charge_derate = torch.tensor([s[2] != "cool_noderate" for s in specs])
    if any(s[2] in cold for s in specs):
        cfg.t_amb_sun = torch.tensor([cold.get(s[2], (0, cfg.t_amb_sun, 0))[1] for s in specs])
        cfg.t_amb_ecl = torch.tensor([cold.get(s[2], (0, 0, cfg.t_amb_ecl))[2] for s in specs])
    env = EPSEnv(cfg)
    for j, s in enumerate(specs):
        if s[2] in cold:
            env.temp[j] = cold[s[2]][0]
    env.fault[:] = torch.tensor([s[1] for s in specs])
    t_inj = 0.3 * orbits * env.orbit.period_s
    env.t_onset[:] = t_inj
    steps = int(orbits * env.orbit.period_s)
    soc0 = 0.6
    if mode == "pil":
        dut.call(b"J", bytes([n]) + struct.pack(f"<{n}f", *([soc0] * n)))
    else:                                          # host EKF fed with the same (corrupted) measurements
        from env_v2 import BatchedEKF
        sil_ekf = BatchedEKF(env.tab, torch.full((n,), soc0))
        sil_feat = Features(n, "cpu")
        sil_state = SgoState(n, "cpu")                    # SGO v3.2 state (latches, counters)
    hazard = torch.zeros(n, dtype=torch.bool)
    oe = torch.zeros(n)
    actions, flags_seen, guards, cyc = [], torch.zeros(n, dtype=torch.long), torch.zeros(n, dtype=torch.long), 0
    i_prev = torch.zeros(n)
    act = torch.zeros(n, 3, dtype=torch.long)
    injected = False
    q = env.tab.q_ah
    for k in range(steps):
        obs, _, info = env.step(act)
        meas = torch.stack([obs[:, 0], obs[:, 1], obs[:, 2], obs[:, 4], obs[:, 5]], 1)
        meas = torch.stack([corrupt(meas[j:j + 1], specs[j][2], k, t_inj)[0] for j in range(n)])
        if mode == "pil":
            if not injected and k >= t_inj and any(s[2] == "e" for s in specs):
                base = symbol_addr("gstate")          # FdirGuardedState[7], 388 B each (ISS-057, v3.2)
                writes = []
                for j, s in enumerate(specs):
                    if s[2] == "e":                 # SocEkf (offset 0) = x[3] + P[3][3] floats
                        writes += [(base + 388 * j, f32_bits(3.0)), (base + 388 * j + 12, 0x7FC00000)]
                openocd_write(writes)
                injected = True
            pay = bytes([n])
            for j in range(n):
                pay += struct.pack("<8f", float(i_prev[j]) * q, float(meas[j, 1]) * q, float(meas[j, 0]), 1.0,
                                   float(meas[j, 1]), float(meas[j, 2]), float(meas[j, 3]), float(meas[j, 4]))
            c, d = dut.call(b"Y", pay)
            if c != b"y":
                raise IOError(f"DUT error {d!r}")
            a = torch.zeros(n, 3, dtype=torch.long)
            for j in range(n):
                r = d[17 * j:17 * j + 17]
                a[j] = torch.tensor(list(r[8:11]))
                flags_seen[j] |= r[11]
                guards[j] += int(r[12] != 0)
                cyc = max(cyc, struct.unpack("<I", r[13:17])[0])
        else:
            sil_ekf.step(i_prev * q, meas[:, 1] * q, torch.nan_to_num(meas[:, 0], nan=0.0), 1.0)
            x = torch.stack([meas[:, 0], meas[:, 1], meas[:, 2], sil_ekf.soc, meas[:, 3], meas[:, 4]], 1)
            x = torch.cat([x, sil_feat.step(x[:, 0], x[:, 3], x[:, 4])], 1)
            a, fl = sgo_v2(tree(x), x[:, 0], x[:, 1], x[:, 2], x[:, 3], state=sil_state)
            flags_seen |= fl
        i_prev = torch.nan_to_num(meas[:, 1], nan=0.0)
        act = a
        actions.append(a.clone())
        hazard |= torch.stack(list(env.hazards().values()), 1).any(1)
        oe += info["out_of_envelope"].float()
    return {"actions": torch.stack(actions), "hazard": hazard, "flags": flags_seen, "guards": guards, "cycles": cyc,
            "oe_time": oe / steps}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="all", choices=["C1", "C2", "C3", "C5", "C6", "all"])
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--faults", type=int, nargs="*", default=None, help="C2: only these fault ids")
    ap.add_argument("--tag", default="", help="suffix for the case key in the results file (e.g. _sgo_v3)")
    ap.add_argument("--tree", nargs=2, default=["results/dagger_summary.json", "checkpoints/tree"], metavar=("SUMMARY", "CKPT_DIR"),
                    help="host (SIL) tree; must match the tree flashed on the DUT")
    ap.add_argument("--out", default=str(ROOT / "results" / "hil_cases.json"))
    a = ap.parse_args()
    dep = json.load(open(ROOT / a.tree[0]))["deploy"]
    tree = GpuTree(pickle.load(open(ROOT / a.tree[1] / f"{dep['mode']}_s{dep['seed']}.pkl", "rb")), "cpu")
    dut = Dut(a.port)
    _, ver = dut.call(b"P")
    out_p = Path(a.out)
    res = json.load(open(out_p)) if out_p.exists() else {}
    res["firmware"] = ver.decode()
    for case in (["C1", "C2", "C3", "C5", "C6"] if a.case == "all" else [a.case]):
        specs_all, orbits = case_list(case)
        if case == "C2" and a.faults:
            specs_all = [s for s in specs_all if s[1] in a.faults]
        specs_all = [s for s in specs_all for _ in range(a.repeat)]
        rows = {}
        t0 = time.time()
        for b in range(0, len(specs_all), MULTI):
            specs = specs_all[b:b + MULTI]
            seed = 7000 + b
            pil = run_batch(dut, tree, specs, orbits, seed, "pil")
            sil = run_batch(dut, tree, specs, orbits, seed, "sil")
            agree = (pil["actions"] == sil["actions"]).all(-1).float().mean(0)
            for j, s in enumerate(specs):
                r = rows.setdefault(s[0] + (f"_f{s[1]}" if case == "C2" else ""), {"runs": 0, "hazard_pil": 0, "hazard_sil": 0,
                                    "agreement": [], "flags_pil": 0, "flags_sil": 0, "guard_steps": 0, "cycles_max": 0,
                                    "oe_time_pil": [], "oe_time_sil": []})
                r["runs"] += 1
                r["hazard_pil"] += int(pil["hazard"][j])
                r["hazard_sil"] += int(sil["hazard"][j])
                r["agreement"].append(float(agree[j]))
                r["flags_pil"] |= int(pil["flags"][j])
                r["flags_sil"] |= int(sil["flags"][j])
                r["guard_steps"] += int(pil["guards"][j])
                r["cycles_max"] = max(r["cycles_max"], int(pil["cycles"]))
                r["oe_time_pil"].append(float(pil["oe_time"][j]))
                r["oe_time_sil"].append(float(sil["oe_time"][j]))
            print(case, f"batch {b // MULTI}: {len(specs)} spacecraft, {time.time() - t0:.0f} s", flush=True)
        for r in rows.values():
            r["agreement_mean"] = float(np.mean(r["agreement"]))
            r["agreement_min"] = float(np.min(r["agreement"]))
            del r["agreement"]
            r["oe_time_pil"] = float(np.mean(r["oe_time_pil"]))
            r["oe_time_sil"] = float(np.mean(r["oe_time_sil"]))
        res[case + a.tag] = {"orbits": orbits, "repeat": a.repeat, "wall_s": time.time() - t0, "rows": rows}
        out_p.write_text(json.dumps(res, indent=2))
        print(case, json.dumps(rows, indent=1)[:2000])


if __name__ == "__main__":
    main()
