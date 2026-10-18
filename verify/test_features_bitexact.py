"""History features: C (firmware/src/fdir_features.c) vs torch twin (sim/features.py).

A 20 000-step stream with plausible readings, sunrise / eclipse transitions,
and injected NaN, +/-Inf and out-of-range values. Both outputs must be equal
bit for bit (float32). gcc: native or the Docker image fdir-toolchain:24.04.
Writes results/features_bitexact.json.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim"))
from features import Features  # noqa: E402

IMAGE = "fdir-toolchain:24.04"
CFLAGS = "-std=c99 -Wall -Wextra -Werror -Wdouble-promotion -O2 -ffp-contract=off -Ifirmware/include"


def stream(n: int, rng) -> np.ndarray:
    t = np.arange(n)
    v = 3.9 + 0.2 * np.sin(t / 900.0) + 0.01 * rng.standard_normal(n)
    soc = 0.7 + 0.2 * np.sin(t / 2000.0) + 0.002 * rng.standard_normal(n)
    sun = (t % 5700) >= 2100
    isa = np.where(sun, 1.0 - 0.3 * (t > n // 2) + 0.01 * rng.standard_normal(n), 0.0)
    x = np.stack([v, soc, isa], 1).astype(np.float32)
    bad = rng.choice(n, n // 50, replace=False)                 # 2 % corrupted samples
    vals = np.array([np.nan, np.inf, -np.inf, 9.0, -5.0, 0.0], np.float32)
    x[bad, rng.integers(0, 3, bad.size)] = vals[rng.integers(0, vals.size, bad.size)]
    return x


def main() -> None:
    rng = np.random.default_rng(5)
    x = stream(20_000, rng)
    f = Features(1, "cpu")
    ref = torch.stack([f.step(*(torch.from_numpy(r[j:j + 1]) for j in range(3)))[0] for r in x]).numpy()
    work = Path(tempfile.mkdtemp(prefix="feat_bitexact_", dir=ROOT / "verify"))
    try:
        with open(work / "in.bin", "wb") as fh:
            np.array([len(x)], "<i4").tofile(fh)
            x.astype("<f4").tofile(fh)
        sh = f"gcc {CFLAGS} firmware/src/fdir_features.c verify/features_driver.c -o /work/feat && /work/feat /work/in.bin /work/out.bin"
        if shutil.which("gcc"):
            r = subprocess.run(["bash", "-c", sh.replace("/work", str(work))], cwd=ROOT, capture_output=True, text=True)
        else:
            r = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:/src", "-v", f"{work}:/work", "-w", "/src", IMAGE, "bash", "-c", sh],
                               capture_output=True, text=True)
        if r.returncode or r.stderr.strip():
            raise SystemExit(r.stderr)
        c = np.fromfile(work / "out.bin", np.float32).reshape(-1, 3)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    mism = int((c.view(np.uint32) != ref.astype(np.float32).view(np.uint32)).any(1).sum())
    res = {"steps": int(len(x)), "corrupted_samples": int(len(x) // 50), "bit_mismatches": mism,
           "all_finite_c": bool(np.isfinite(c).all()), "gate": {"pass": mism == 0 and bool(np.isfinite(c).all())}}
    (ROOT / "results" / "features_bitexact.json").write_text(json.dumps(res, indent=2))
    print(res)
    sys.exit(0 if res["gate"]["pass"] else 1)


if __name__ == "__main__":
    main()
