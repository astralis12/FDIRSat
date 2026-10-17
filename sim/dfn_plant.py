"""DFN truth plant (PyBaMM) for one cell.

Generates the three data sets the ECM/EKF pipeline needs:
  ocv    : C/20 discharge, used as the OCV(SoC) curve
  pulse  : HPPC-style pulse train, used to identify R0, R1, C1, R2, C2
  valid  : random-walk current profile (NASA PCoE style), used for validation

Stand-in parameter set: Chen2020 (LG M50, NMC811, 5 Ah). Replace with the
XJTU / own-pack data when available; nothing downstream depends on the set.
Sign convention: current > 0 = discharge (PyBaMM convention).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pybamm

PARAM_SET = "Chen2020"


def _params(temp_c: float) -> pybamm.ParameterValues:
    p = pybamm.ParameterValues(PARAM_SET)
    p["Ambient temperature [K]"] = 273.15 + temp_c
    p["Initial temperature [K]"] = 273.15 + temp_c
    return p


def _run_current(t: np.ndarray, i: np.ndarray, soc0: float, temp_c: float) -> dict:
    """Solve the DFN for a sampled current profile (A, discharge > 0)."""
    p = _params(temp_c)
    p["Current function [A]"] = pybamm.Interpolant(t, i, pybamm.t, interpolator="linear")
    model = pybamm.lithium_ion.DFN()
    sim = pybamm.Simulation(model, parameter_values=p)
    sol = sim.solve(t_eval=t, initial_soc=soc0)
    q_nom = float(p["Nominal cell capacity [A.h]"])
    tt = sol["Time [s]"].entries
    ii = sol["Current [A]"].entries
    vv = sol["Terminal voltage [V]"].entries
    # Coulomb-counted true SoC (the EKF's ground truth)
    soc = soc0 - np.concatenate([[0.0], np.cumsum(0.5 * (ii[1:] + ii[:-1]) * np.diff(tt))]) / (3600.0 * q_nom)
    return {"t": tt, "i": ii, "v": vv, "soc": soc, "q_nom": q_nom}


def ocv_curve(temp_c: float = 25.0, n_points: int = 101) -> dict:
    p = _params(temp_c)
    q = float(p["Nominal cell capacity [A.h]"])
    exp = pybamm.Experiment([f"Discharge at C/20 until {p['Lower voltage cut-off [V]']} V"],
                            period="60 seconds")
    sol = pybamm.Simulation(pybamm.lithium_ion.DFN(), parameter_values=p, experiment=exp).solve(initial_soc=1.0)
    dis_ah = sol["Discharge capacity [A.h]"].entries
    v = sol["Terminal voltage [V]"].entries
    soc = 1.0 - dis_ah / q
    grid = np.linspace(soc.min(), 1.0, n_points)
    v_grid = np.interp(grid, soc[::-1], v[::-1])
    return {"soc": grid, "ocv": v_grid, "q_nom": q}


def pulse_profile(q_nom: float, dt: float = 1.0, soc_steps: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """HPPC-like: at each SoC step, 1C 10 s discharge, 40 s rest, 0.5C 10 s charge,
    40 s rest, then 0.5C discharge to move ~10 % SoC, then 30 min rest."""
    seg = []
    c = q_nom
    for _ in range(soc_steps):
        seg += [(c, 10), (0.0, 40), (-0.5 * c, 10), (0.0, 40), (0.5 * c, 720), (0.0, 1800)]
    t, i, now = [], [], 0.0
    for amp, dur in seg:
        n = int(dur / dt)
        t.extend(now + dt * np.arange(n))
        i.extend([amp] * n)
        now += dur
    return np.asarray(t), np.asarray(i)


def random_walk_profile(q_nom: float, hours: float = 3.0, dt: float = 1.0, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Random-walk load: new set-point every 60 s, 0.1C..0.8C discharge with
    30 % of segments at 0.5C charge (sunlit periods)."""
    rng = np.random.default_rng(seed)
    n_seg = int(hours * 60)
    amps = rng.uniform(0.1, 0.8, n_seg) * q_nom
    amps[rng.random(n_seg) < 0.30] = -0.5 * q_nom
    i = np.repeat(amps, int(60 / dt))
    t = dt * np.arange(i.size)
    return t, i


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/dfn")
    ap.add_argument("--temp", type=float, default=25.0)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    ocv = ocv_curve(a.temp)
    np.savez(out / f"ocv_{int(a.temp)}C.npz", **ocv)
    t, i = pulse_profile(ocv["q_nom"])
    np.savez(out / f"pulse_{int(a.temp)}C.npz", **_run_current(t, i, 0.90, a.temp))
    t, i = random_walk_profile(ocv["q_nom"], seed=a.seed)
    np.savez(out / f"valid_{int(a.temp)}C_s{a.seed}.npz", **_run_current(t, i, 0.85, a.temp))
    (out / "meta.json").write_text(json.dumps({"param_set": PARAM_SET, "pybamm": pybamm.__version__,
                                               "temp_c": a.temp, "seed": a.seed}, indent=2))
    print("wrote", sorted(p.name for p in out.iterdir()))


if __name__ == "__main__":
    main()
