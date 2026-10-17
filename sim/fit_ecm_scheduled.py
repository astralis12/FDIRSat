"""N4: SoC-scheduled 2RC ECM from pulsed-load data (DFN pulses and NASA RW
pulsed-load blocks), then EKF uncertainty recalibration on pooled DFN profiles
and a coverage check on test profiles (RUNBOOK 4.1, closes ISS-018).

Method
  1. Segments: a pulse train is split at the end of every long rest; each
     segment (pulse(s) + rest) is one SoC level. Rest-end voltages are relaxed
     OCV points and give a rest-corrected OCV next to the C/20 one.
  2. Fit: constant 2RC parameters per segment, fitted in sequence with the RC
     state carried over; these seed a joint fit of piecewise-linear SoC tables
     (one knot per segment centre) over the whole profile.
  3. EKF (DFN, true SoC known), per variant {constant, scheduled} parameters x
     {C/20, rest-corrected} OCV: r_v and the model-error sigma are calibrated on
     the pooled calibration seeds; the sharpest calibrated variant (smallest
     mean 2-sigma on those seeds) is selected; coverage is then measured on
     test seeds that no choice was made on.
  4. NASA RW17-20: tables per pulsed-load block (ageing trend), and a cross-cell
     check: tables from RW17-19 (first block) predict RW20's voltage open loop.

Gate: calibrated 2-sigma coverage >= 0.90 on every test seed (selected variant).
Writes results/ecm_scheduled.json.
"""
from __future__ import annotations

import argparse
import json
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares

from ecm_ekf import OCV, ECMParams, ScheduledECM, calibrate_model_sigma, fit, run_ekf, simulate_scheduled

ROOT = Path(__file__).resolve().parents[1]
# bounds as in ecm_ekf.fit: R0, R1, tau1, R2, tau2
LO = np.array([1e-4, 1e-4, 1.0, 1e-4, 60.0])
HI = np.array([0.2, 0.2, 60.0, 0.2, 3000.0])
Z0 = np.log([0.02, 0.01, 10.0, 0.01, 300.0])
Q_SOC = 1e-10                                     # SoC process noise, as eval_ekf_calibration.py
# Voltage noise grid [V]. r_v is not the sensor noise: it must also cover the ECM
# error, which is correlated in time. It is chosen on the calibration seeds only.
R_V_GRID = [0.005, 0.0075, 0.010, 0.015, 0.020, 0.030, 0.040]
CAL_TARGET = 0.95
SETTLE_S = 600.0                                  # coverage counted after 10 min
COVERAGE_GATE = 0.90
SMOOTH_MV = 2.0                                   # table smoothness weight, see fit_scheduled
MIN_REST_S = 1000.0                               # DFN rests are 1800 s, NASA 1200 s


# ---------------------------------------------------------------- segments
def long_rests(t, i, min_rest_s: float) -> list[tuple[int, int]]:
    """(start, end) indices of zero-current runs lasting at least min_rest_s."""
    rest = np.abs(i) < 1e-6
    d = np.diff(np.concatenate([[0], rest.astype(int), [0]]))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1
    return [(a, b) for a, b in zip(starts, ends) if t[b] - t[a] >= min_rest_s]


def segments(t, i, min_rest_s: float) -> list[tuple[int, int]]:
    """Index ranges [a, b] ending at the end of each long rest that follows load."""
    out, a = [], 0
    for _, b in long_rests(t, i, min_rest_s):
        if np.any(np.abs(i[a:b]) > 1e-6):
            out.append((a, b))
            a = b
    return out


def rest_points(t, i, v, soc, min_rest_s: float) -> tuple[np.ndarray, np.ndarray]:
    """Relaxed (SoC, voltage) at the end of every long rest."""
    ends = [b for _, b in long_rests(t, i, min_rest_s)]
    return soc[ends], v[ends]


# ---------------------------------------------------------------- fitting
def _table(q, knots, z) -> ScheduledECM:
    x = np.exp(z.reshape(len(knots), 5))
    return ScheduledECM(q, knots, *x.T)


def fit_scheduled(t, i, v, soc, ocv: OCV, q: float, min_rest_s: float = MIN_REST_S,
                  smooth_mv: float = SMOOTH_MV) -> dict:
    segs = segments(t, i, min_rest_s)
    knots, zs, rc = [], [], (0.0, 0.0)
    # 1. sequential constant-parameter fit per segment, RC state carried over
    for a, b in segs:
        tt, ii, vv = t[a:b + 1], i[a:b + 1], v[a:b + 1]
        s0, state = float(soc[a]), rc

        def resid(z, tt=tt, ii=ii, vv=vv, s0=s0, state=state):
            p = _table(q, [s0], z)
            return simulate_scheduled(p, ocv, tt, ii, s0, *state)[0] - vv

        sol = least_squares(resid, zs[-1] if zs else Z0, bounds=(np.log(LO), np.log(HI)),
                            x_scale="jac", max_nfev=200)
        zs.append(sol.x)
        knots.append(0.5 * (soc[a] + soc[b]))
        rc = simulate_scheduled(_table(q, [s0], sol.x), ocv, tt, ii, s0, *state)[2]
    seq = _table(q, knots, np.concatenate(zs))
    seq_rmse = float(np.sqrt(np.mean((simulate_scheduled(seq, ocv, t, i, float(soc[0]))[0] - v) ** 2)))

    # 2. joint fit of the SoC tables over the whole profile. A penalty on the
    # step between neighbouring log-parameters stops the two RC branches trading
    # places from knot to knot: an RMS log-step of 1 costs as much as smooth_mv
    # of RMS voltage error (added in quadrature).
    k = len(knots)
    w = smooth_mv * 1e-3 * np.sqrt(t.size / (5 * max(k - 1, 1)))

    def resid_joint(z):
        r = simulate_scheduled(_table(q, knots, z), ocv, t, i, float(soc[0]))[0] - v
        return np.concatenate([r, w * np.diff(z.reshape(k, 5), axis=0).ravel()])

    sol = least_squares(resid_joint, np.concatenate(zs), x_scale="jac", max_nfev=60,
                        bounds=(np.tile(np.log(LO), k), np.tile(np.log(HI), k)))
    tab = _table(q, knots, sol.x)
    vm = simulate_scheduled(tab, ocv, t, i, float(soc[0]))[0]
    return {"table": tab, "segments": len(segs), "seq_rmse_mV": 1e3 * seq_rmse,
            "fit_rmse_mV": 1e3 * float(np.sqrt(np.mean((vm - v) ** 2)))}


def as_constant(p: ECMParams) -> ScheduledECM:
    return ScheduledECM(p.q_ah, [0.0, 1.0], [p.r0] * 2, [p.r1] * 2, [p.r1 * p.c1] * 2, [p.r2] * 2, [p.r2 * p.c2] * 2)


# ---------------------------------------------------------------- DFN + EKF
def ekf_errors(model, ocv, va, soc_init_error: float, r_v: float):
    r = run_ekf(model, ocv, va["t"], va["i"], va["v"], float(va["soc"][0]) - soc_init_error,
                q_soc=Q_SOC, r_v=r_v ** 2)
    s = va["t"] > SETTLE_S
    return (r["soc_est"] - va["soc"])[s], r["soc_std"][s]


def calibrate_ekf(model, ocv, cal_vals: dict, soc_init_error: float) -> tuple[float, float, list]:
    """Two-stage calibration on the pooled calibration seeds: the smallest r_v on
    the grid whose raw 2-sigma coverage reaches CAL_TARGET, then the additive
    model-error sigma s_m (zero if r_v alone reaches the target)."""
    scan = []
    for r_v in R_V_GRID:
        err = [ekf_errors(model, ocv, va, soc_init_error, r_v) for va in cal_vals.values()]
        e, sd = np.concatenate([x[0] for x in err]), np.concatenate([x[1] for x in err])
        cov = float(np.mean(np.abs(e) <= 2 * sd))
        scan.append({"r_v_mV": 1e3 * r_v, "coverage_raw_2sigma": cov})
        if cov >= CAL_TARGET:
            break
    return r_v, calibrate_model_sigma(e, sd, CAL_TARGET), scan


def _variant(job) -> tuple[str, dict]:
    """Calibrate one model variant and evaluate it on every seed (one process)."""
    name, model, ocv, fit_mv, d, temp, cal_seeds, test_seeds, soc_init_error = job
    vals = {s: dict(np.load(d / f"valid_{temp}C_s{s}.npz")) for s in list(cal_seeds) + list(test_seeds)}
    r_v, s_m, scan = calibrate_ekf(model, ocv, {s: vals[s] for s in cal_seeds}, soc_init_error)
    seeds = {}
    for s, va in vals.items():
        e, sd = ekf_errors(model, ocv, va, soc_init_error, r_v)
        st = np.sqrt(sd ** 2 + s_m ** 2)
        vm = simulate_scheduled(model, ocv, va["t"], va["i"], float(va["soc"][0]))[0]
        seeds[str(s)] = {
            "role": "calibration" if s in cal_seeds else "test",
            "soc_final": float(va["soc"][-1]),
            "open_loop_rmse_mV": float(1e3 * np.sqrt(np.mean((vm - va["v"]) ** 2))),
            "soc_rmse_pct": float(100 * np.sqrt(np.mean(e ** 2))),
            "soc_max_abs_pct": float(100 * np.max(np.abs(e))),
            "soc_bias_pct": float(100 * np.mean(e)),
            "coverage_raw_2sigma": float(np.mean(np.abs(e) <= 2 * sd)),
            "coverage_calibrated_2sigma": float(np.mean(np.abs(e) <= 2 * st)),
            "two_sigma_mean_pct": float(200 * np.mean(st)),
        }

    def agg(role, key, f):
        return float(f([x[key] for x in seeds.values() if x["role"] == role]))

    return name, {
        "fit_rmse_mV": float(fit_mv), "r_v_mV": 1e3 * r_v, "r_v_scan": scan, "model_sigma_pct": 100 * s_m,
        "cal_two_sigma_mean_pct": agg("calibration", "two_sigma_mean_pct", np.mean),
        "test_coverage_min": agg("test", "coverage_calibrated_2sigma", np.min),
        "test_soc_rmse_pct_max": agg("test", "soc_rmse_pct", np.max),
        "test_two_sigma_mean_pct": agg("test", "two_sigma_mean_pct", np.mean),
        "seeds": seeds,
    }


def dfn_part(d: Path, temp: int, cal_seeds, test_seeds, soc_init_error: float) -> dict:
    for s in list(cal_seeds) + list(test_seeds):   # a DFN run can stop early at a voltage cut-off
        t_end = float(np.load(d / f"valid_{temp}C_s{s}.npz")["t"][-1])
        if t_end < 0.99 * 3 * 3600:
            raise SystemExit(f"validation seed {s} is truncated at {t_end:.0f} s; pick another seed")
    o = np.load(d / f"ocv_{temp}C.npz")
    ocv20 = OCV(o["soc"], o["ocv"])
    pu = np.load(d / f"pulse_{temp}C.npz")
    q = float(pu["q_nom"])
    t, i, v, soc = pu["t"], pu["i"], pu["v"], pu["soc"]
    sp, vp = rest_points(t, i, v, soc, MIN_REST_S)
    ocv_rc = ocv20.corrected(sp, vp)

    const, const_rmse = fit(t, i, v, float(soc[0]), ocv20, q)        # ISS-018 baseline
    const_rc, const_rc_rmse = fit(t, i, v, float(soc[0]), ocv_rc, q)
    sch20 = fit_scheduled(t, i, v, soc, ocv20, q)
    sch_rc = fit_scheduled(t, i, v, soc, ocv_rc, q)
    fits = {
        "constant_c20ocv": (as_constant(const), ocv20, 1e3 * const_rmse),
        "constant_restocv": (as_constant(const_rc), ocv_rc, 1e3 * const_rc_rmse),
        "scheduled_c20ocv": (sch20["table"], ocv20, sch20["fit_rmse_mV"]),
        "scheduled_restocv": (sch_rc["table"], ocv_rc, sch_rc["fit_rmse_mV"]),
    }
    jobs = [(n, m, oc, f, d, temp, cal_seeds, test_seeds, soc_init_error) for n, (m, oc, f) in fits.items()]
    with Pool(len(jobs)) as pool:
        out = dict(pool.map(_variant, jobs))

    # selection uses the calibration seeds only: the sharpest calibrated variant
    sel = min(out, key=lambda n: out[n]["cal_two_sigma_mean_pct"])
    return {
        "temp_c": temp, "q_ah": q, "soc_init_error_pct": 100 * soc_init_error,
        "ekf_noise": {"q_soc": Q_SOC, "r_v_grid_mV": [1e3 * x for x in R_V_GRID], "cal_target": CAL_TARGET},
        "settle_s": SETTLE_S, "smooth_mv": SMOOTH_MV,
        "calibration_seeds": list(cal_seeds), "test_seeds": list(test_seeds),
        "rest_ocv_points": {"soc": sp.tolist(), "v": vp.tolist(),
                            "offset_vs_c20_mV": (1e3 * (vp - ocv20(sp))).tolist()},
        "segments": sch_rc["segments"],
        "seq_fit_rmse_mV": {"scheduled_c20ocv": sch20["seq_rmse_mV"], "scheduled_restocv": sch_rc["seq_rmse_mV"]},
        "tables": {n: m.as_dict() for n, (m, _, _) in fits.items()},
        "variants": out,
        "selected": {"variant": sel, "rule": "smallest mean calibrated 2-sigma on the calibration seeds",
                     "table": fits[sel][0].as_dict(), "ocv": "c20" if "c20" in sel else "rest-corrected",
                     "r_v_mV": out[sel]["r_v_mV"], "model_sigma_pct": out[sel]["model_sigma_pct"]},
        "gate": {"metric": f"min calibrated 2-sigma coverage over test seeds, {sel}",
                 "threshold": COVERAGE_GATE, "value": out[sel]["test_coverage_min"],
                 "pass": bool(out[sel]["test_coverage_min"] >= COVERAGE_GATE)},
    }


# ---------------------------------------------------------------- NASA
def nasa_block(steps, idx, nasa_rw) -> dict:
    """One pulsed-load block as a time series with Coulomb-counted SoC.
    The block starts at full charge (4.2 V) and ends at 3.2 V, so the charge it
    delivers is the capacity at 1 A. The fit needs only V and I; temperature is
    metadata and is reported as None when the channel is implausible (RW18
    blocks 0-4 read about -4099 C throughout, ISS-021)."""
    d = nasa_rw.stitched(steps, idx)
    ok = np.isfinite(d["v"]) & np.isfinite(d["i"])
    t, i, v, T = d["t"][ok], d["i"][ok], d["v"][ok], d["temp"][ok]
    ah = np.concatenate([[0.0], np.cumsum(0.5 * (i[1:] + i[:-1]) * np.diff(t))]) / 3600.0
    q = float(ah[-1])
    T_ok = np.isfinite(T) & (T > -40) & (T < 100)
    return {"t": t, "i": i, "v": v, "soc": 1.0 - ah / q, "q_ah": q,
            "temp_mean": float(np.mean(T[T_ok])) if T_ok.mean() > 0.99 else None,
            "complete": bool(v.min() <= 3.25)}


def nasa_ocv(b) -> OCV:
    """OCV from the block's own rest-end points (20 min rests), pchip in SoC."""
    sp, vp = rest_points(b["t"], b["i"], b["v"], b["soc"], MIN_REST_S)
    sp, vp = np.concatenate([[1.0], sp]), np.concatenate([[b["v"][0]], vp])  # block starts relaxed at full
    sp, first = np.unique(sp, return_index=True)   # the first rest also ends at SoC 1
    vp = vp[first]
    grid = np.linspace(0.0, 1.0, 101)
    f = PchipInterpolator(sp, vp, extrapolate=True)
    return OCV(grid, f(np.clip(grid, sp[0] - 0.05, 1.0)))


def _nasa_cell(job) -> tuple[str, dict, tuple]:
    import nasa_rw

    cell, blocks_per_cell = job
    st = nasa_rw.load(cell)
    pb = nasa_rw.pulsed_load_blocks(st)
    sel = pb if blocks_per_cell is None else pb[:: max(1, len(pb) // blocks_per_cell)]
    rows, first, skipped = [], None, []
    for idx in sel:
        b = nasa_block(st, idx, nasa_rw)
        if not b["complete"]:          # aborted block: never reaches the 3.2 V end of discharge
            skipped.append({"block": int(pb.index(idx)), "samples": int(b["t"].size), "v_min": float(b["v"].min())})
            continue
        ocv = nasa_ocv(b)
        f = fit_scheduled(b["t"], b["i"], b["v"], b["soc"], ocv, b["q_ah"])
        mid = f["table"].at(0.5)
        rows.append({"block": int(pb.index(idx)), "date": st[idx[0]].date, "q_ah": b["q_ah"],
                     "temp_mean_C": b["temp_mean"], "segments": f["segments"],
                     "fit_rmse_mV": f["fit_rmse_mV"], "r0_soc50_mohm": 1e3 * mid.r0,
                     "r_total_soc50_mohm": 1e3 * (mid.r0 + mid.r1 + mid.r2)})
        first = first or (b, ocv, f["table"])
    return cell, {"table_first_block": first[2].as_dict(), "blocks": rows, "skipped_incomplete": skipped}, first


def nasa_part(cells, blocks_per_cell: int | None) -> dict:
    with Pool(len(cells)) as pool:
        res = pool.map(_nasa_cell, [(c, blocks_per_cell) for c in cells])
    out = {"cells": {c: r for c, r, _ in res}}
    first = {c: f for c, _, f in res}

    # cross-cell: average log-tables of the training cells on a common SoC grid,
    # predict the held-out cell open loop (its own OCV and capacity, as onboard)
    held, train = cells[-1], cells[:-1]
    grid = np.linspace(0.05, 0.95, 19)
    fields = ("r0", "r1", "tau1", "r2", "tau2")
    logs = np.mean([[np.log(np.interp(grid, first[c][2].soc, getattr(first[c][2], f))) for f in fields]
                    for c in train], axis=0)
    b, ocv, own = first[held]
    pooled = ScheduledECM(b["q_ah"], grid, *np.exp(logs))
    cross = {}
    for name, model in {"pooled_train_cells": pooled, "own_fit": own}.items():
        vm = simulate_scheduled(model, ocv, b["t"], b["i"], float(b["soc"][0]))[0]
        cross[name] = float(1e3 * np.sqrt(np.mean((vm - b["v"]) ** 2)))
    out["cross_cell"] = {"train": train, "held_out": held, "open_loop_rmse_mV": cross,
                         "pooled_table": pooled.as_dict()}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dfn", default=str(ROOT / "data" / "dfn"))
    ap.add_argument("--temp", type=int, default=25)
    # Seeds 0-4 and 6-9 were all looked at while the method was chosen (ISS-025),
    # so all of them calibrate; 10-13 were generated afterwards and are the test.
    # Seed 5 is excluded: its DFN run stops at 422 s (4.2 V cut-off, ISS-024).
    ap.add_argument("--cal-seeds", type=int, nargs="+", default=[0, 1, 2, 3, 4, 6, 7, 8, 9])
    ap.add_argument("--test-seeds", type=int, nargs="+", default=[10, 11, 12, 13])
    ap.add_argument("--soc-init-error", type=float, default=0.20)
    ap.add_argument("--nasa-cells", nargs="*", default=["RW17", "RW18", "RW19", "RW20"])
    ap.add_argument("--nasa-blocks", type=int, default=None, help="blocks per cell (default: all)")
    ap.add_argument("--out", default=str(ROOT / "results" / "ecm_scheduled.json"))
    a = ap.parse_args()

    res = {"dfn": dfn_part(Path(a.dfn), a.temp, a.cal_seeds, a.test_seeds, a.soc_init_error)}
    if a.nasa_cells:
        res["nasa"] = nasa_part(a.nasa_cells, a.nasa_blocks)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2))

    d = res["dfn"]
    print(f"DFN {d['temp_c']} C, {d['segments']} SoC segments, rest-OCV offset vs C/20 "
          f"{np.round(d['rest_ocv_points']['offset_vs_c20_mV'], 1).tolist()} mV")
    for name, r in d["variants"].items():
        cov = {s: round(x["coverage_calibrated_2sigma"], 3) for s, x in r["seeds"].items() if x["role"] == "test"}
        print(f"  {name:18s} fit {r['fit_rmse_mV']:5.2f} mV  r_v {r['r_v_mV']:4.1f} mV  s_m {r['model_sigma_pct']:.3f} %"
              f"  cal 2sig {r['cal_two_sigma_mean_pct']:.2f} %  test: SoC RMSE max {r['test_soc_rmse_pct_max']:.2f} %"
              f"  2sig {r['test_two_sigma_mean_pct']:.2f} %  coverage {cov}")
    print(f"selected: {d['selected']['variant']} ({d['selected']['rule']})")
    print(f"GATE {d['gate']['metric']}: {d['gate']['value']:.3f} (>= {COVERAGE_GATE}) "
          f"{'PASS' if d['gate']['pass'] else 'FAIL'}")
    if "nasa" in res:
        for cell, c in res["nasa"]["cells"].items():
            b0, b1 = c["blocks"][0], c["blocks"][-1]
            print(f"  NASA {cell}: {len(c['blocks'])} blocks, fit {b0['fit_rmse_mV']:.1f} mV, "
                  f"Q {b0['q_ah']:.3f}->{b1['q_ah']:.3f} Ah, R0@50% {b0['r0_soc50_mohm']:.1f}->{b1['r0_soc50_mohm']:.1f} mOhm")
        print("  cross-cell open loop:", res["nasa"]["cross_cell"]["open_loop_rmse_mV"])


if __name__ == "__main__":
    main()
