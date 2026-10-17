"""C EKF (firmware/src/soc_ekf.c) against the Python reference (sim/ecm_ekf.py).

Builds the C driver twice with gcc (-std=c99 -Wall -Wextra -Werror
-ffp-contract=off): the double build checks that the equations are the same,
the float build is the flight build. Both run the DFN validation profiles with
the same table, OCV, noise and initial error as sim/ekf_sigma_cv.py.

Tolerances (fixed before the first run):
  double build  |dSoC|, |dSigma| <= 1e-9          (rounding only)
  float  build  |dSoC|, |dSigma| <= 1e-4          (0.01 % SoC, 30x below the EKF RMSE)
  both          no guard activation (status bits) on nominal profiles
A sample beyond tolerance is a mismatch; the gate is 0 mismatches.

gcc: uses gcc on PATH, else the Docker image fdir-toolchain:24.04
(ubuntu:24.04 + apt cbmc gcc; see RUNBOOK step 0).
Writes results/ekf_bitexact.json.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim"))
sys.path.insert(0, str(ROOT / "codegen"))
from ecm_ekf import run_ekf  # noqa: E402
from emit_ekf_tables import reference_model  # noqa: E402

TOL = {"double": 1e-9, "float": 1e-4}
IMAGE = "fdir-toolchain:24.04"
CFLAGS = ["-std=c99", "-Wall", "-Wextra", "-Werror", "-O2", "-ffp-contract=off", "-Ifirmware/include"]
SRC = ["firmware/src/soc_ekf.c", "verify/ekf_driver.c"]


def _run(cmd: list[str], work: Path) -> subprocess.CompletedProcess:
    """Run a command in the repo root, natively or in the toolchain container.
    `work` is a scratch dir mounted as /work in the container."""
    if shutil.which("gcc"):
        return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    full = ["docker", "run", "--rm", "-v", f"{ROOT}:/src", "-v", f"{work}:/work", "-w", "/src", IMAGE] + cmd
    return subprocess.run(full, capture_output=True, text=True)


def build(work: Path, native: bool) -> dict:
    out = {}
    for prec in ("double", "float"):
        exe = (work if native else Path("/work")) / f"ekf_{prec}"
        defs = ["-DSOC_EKF_DOUBLE"] if prec == "double" else []
        r = _run(["gcc", *CFLAGS, *defs, *SRC, "-lm", "-o", exe.as_posix()], work)
        if r.returncode != 0 or r.stderr.strip():
            raise SystemExit(f"{prec} build failed or warned:\n{r.stderr}")
        out[prec] = exe.as_posix()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dfn", default=str(ROOT / "data" / "dfn"))
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 6, 14, 15, 17, 18, 19])
    ap.add_argument("--soc-init-error", type=float, default=0.20)
    ap.add_argument("--out", default=str(ROOT / "results" / "ekf_bitexact.json"))
    a = ap.parse_args()
    ecm = json.load(open(ROOT / "results" / "ecm_scheduled.json"))
    cv = json.load(open(ROOT / "results" / "ekf_sigma_cv.json"))
    tab, ocv, n = reference_model(ecm, cv, Path(a.dfn))
    native = shutil.which("gcc") is not None
    work = Path(tempfile.mkdtemp(prefix="ekf_bitexact_", dir=ROOT / "verify"))
    try:
        exes = build(work, native)
        res = {"runner": "native gcc" if native else f"docker {IMAGE}", "cflags": CFLAGS, "tolerance": TOL,
               "soc_init_error": a.soc_init_error, "noise": n, "seeds": {}}
        mismatches = {p: 0 for p in TOL}
        for s in a.seeds:
            va = np.load(Path(a.dfn) / f"valid_25C_s{s}.npz")
            t, i, v = va["t"], va["i"], va["v"]
            soc0 = float(va["soc"][0]) - a.soc_init_error
            ref = run_ekf(tab, ocv, t, i, v, soc0, p0_soc=n["p0_soc"], q_soc=n["q_soc"], q_rc=n["q_rc"], r_v=n["r_v"])
            fin = work / f"in_{s}.bin"
            with open(fin, "wb") as f:
                np.array([t.size], "<i4").tofile(f)
                np.array([soc0], "<f8").tofile(f)
                np.stack([t, i, v], axis=1).astype("<f8").tofile(f)
            row = {"samples": int(t.size)}
            for prec, exe in exes.items():
                fout = work / f"out_{s}_{prec}.bin"
                cin = fin.as_posix() if native else f"/work/{fin.name}"
                cout = fout.as_posix() if native else f"/work/{fout.name}"
                r = _run([exe, cin, cout], work)
                if r.returncode != 0:
                    raise SystemExit(f"driver failed ({prec}, seed {s}): {r.stderr}")
                c = np.fromfile(fout, "<f8").reshape(-1, 5)
                ds, dsd = np.abs(c[:, 0] - ref["soc_est"]), np.abs(c[:, 1] - ref["soc_std"])
                bad = int(np.sum((ds > TOL[prec]) | (dsd > TOL[prec])))
                mismatches[prec] += bad
                err = c[:, 0] - va["soc"]
                k = t > 600.0
                row[prec] = {"max_abs_dsoc": float(ds.max()), "max_abs_dsigma": float(dsd.max()),
                             "mismatches": bad, "guard_activations": int(np.count_nonzero(c[:, 4])),
                             "coverage_2sigma_c": float(np.mean(np.abs(err[k]) <= 2 * c[k, 1])),
                             "soc_rmse_pct_c": float(100 * np.sqrt(np.mean(err[k] ** 2)))}
            res["seeds"][str(s)] = row
            print(s, {p: (f"{row[p]['max_abs_dsoc']:.2e}", f"{row[p]['max_abs_dsigma']:.2e}", row[p]["mismatches"],
                          row[p]["guard_activations"]) for p in TOL})
        guards = sum(r[p]["guard_activations"] for r in res["seeds"].values() for p in TOL)
        res["mismatches"] = mismatches
        res["gate"] = {"metric": "mismatches (both builds) + guard activations", "value": sum(mismatches.values()) + guards,
                       "pass": sum(mismatches.values()) + guards == 0}
        Path(a.out).write_text(json.dumps(res, indent=2))
        print("GATE", res["gate"])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(0 if res["gate"]["pass"] else 1)


if __name__ == "__main__":
    main()
