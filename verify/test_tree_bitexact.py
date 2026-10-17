"""Generated C tree (firmware/src/fdir_tree.c) vs sklearn predict and the GPU tree.

Inputs: uniform over the input box, near the SGO thresholds, environment
states (held-out validation rollout), and special values (NaN, +/-Inf, 0, -0).
For finite inputs the C tree must equal sklearn exactly; for every input the
C tree must equal the GPU tree (train/fdir_torch.GpuTree) and stay in 0..3.
gcc: native if on PATH, else the Docker image fdir-toolchain:24.04.
Writes results/tree_bitexact.json.
"""
from __future__ import annotations

import json
import pickle
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
for p in ("train", "verify"):
    sys.path.insert(0, str(ROOT / p))
from dagger_adversary import BOX, THRESH  # noqa: E402
from fdir_torch import GpuTree  # noqa: E402

IMAGE = "fdir-toolchain:24.04"
CFLAGS = ["-std=c99", "-Wall", "-Wextra", "-Werror", "-Wdouble-promotion", "-O2", "-ffp-contract=off", "-Ifirmware/include"]


def main() -> None:
    dep = json.load(open(ROOT / "results" / "dagger_summary.json"))["deploy"]
    pk = ROOT / "checkpoints" / "tree" / f"{dep['mode']}_s{dep['seed']}.pkl"
    clf = pickle.load(open(pk, "rb"))
    rng = np.random.default_rng(11)
    n = 200_000
    xu = rng.uniform(BOX[:, 0], BOX[:, 1], size=(n, 6)).astype(np.float32)
    xb = rng.uniform(BOX[:, 0], BOX[:, 1], size=(n, 6)).astype(np.float32)
    f = rng.integers(0, 4, n)
    for j in range(n):
        th = THRESH[f[j]][rng.integers(0, len(THRESH[f[j]]))]
        xb[j, f[j]] = np.float32(th + rng.normal(0, 0.01 * max(abs(th), 0.05)))
    # exact sklearn thresholds and their float32 neighbours: the hardest cases
    t = clf.tree_
    inner = np.flatnonzero(t.children_left >= 0)
    xt = rng.uniform(BOX[:, 0], BOX[:, 1], size=(3 * inner.size, 6)).astype(np.float32)
    for k, node in enumerate(np.repeat(inner, 3)):
        v = np.float32(t.threshold[node])
        xt[k, t.feature[node]] = [np.nextafter(v, np.float32(-np.inf)), v, np.nextafter(v, np.float32(np.inf))][k % 3]
    val = np.load(ROOT / "data" / "defender" / "val_xjtu_s100.npz")["obs"]
    xe = val[rng.choice(len(val), n, replace=False)].astype(np.float32)
    finite = np.concatenate([xu, xb, xt, xe])
    special = rng.uniform(BOX[:, 0], BOX[:, 1], size=(4000, 6)).astype(np.float32)
    sv = np.array([np.nan, np.inf, -np.inf, 0.0, -0.0], np.float32)
    special[np.arange(4000), rng.integers(0, 6, 4000)] = sv[rng.integers(0, 5, 4000)]
    x = np.concatenate([finite, special])

    work = Path(tempfile.mkdtemp(prefix="tree_bitexact_", dir=ROOT / "verify"))
    try:
        fin = work / "in.bin"
        with open(fin, "wb") as fh:
            np.array([len(x)], "<i4").tofile(fh)
            x.astype("<f4").tofile(fh)
        native = shutil.which("gcc") is not None
        build = ["gcc", *CFLAGS, "firmware/src/fdir_tree.c", "verify/tree_driver.c", "-o"]
        if native:
            cmds = [build + [str(work / "tree")], [str(work / "tree"), str(fin), str(work / "out.bin")]]
            for c in cmds:
                r = subprocess.run(c, cwd=ROOT, capture_output=True, text=True)
                if r.returncode or r.stderr.strip():
                    raise SystemExit(r.stderr)
        else:
            sh = " && ".join([" ".join(build + ["/work/tree"]), "/work/tree /work/in.bin /work/out.bin"])
            r = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT}:/src", "-v", f"{work}:/work", "-w", "/src", IMAGE,
                                "bash", "-c", sh], capture_output=True, text=True)
            if r.returncode or r.stderr.strip():
                raise SystemExit(r.stderr)
        c_out = np.fromfile(work / "out.bin", np.uint8).reshape(-1, 3).astype(int)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    sk = clf.predict(finite).astype(int)
    gpu = GpuTree(clf, "cpu")(torch.from_numpy(x)).numpy()
    res = {"tree": pk.name, "n_finite": int(len(finite)), "n_special": int(len(special)),
           "n_threshold_neighbours": int(len(xt)),
           "c_vs_sklearn_mismatches": int((c_out[:len(finite)] != sk).any(1).sum()),
           "c_vs_gpu_mismatches": int((c_out != gpu).any(1).sum()),
           "c_outputs_in_range": bool((c_out >= 0).all() and (c_out <= 3).all())}
    res["gate"] = {"pass": res["c_vs_sklearn_mismatches"] == 0 and res["c_vs_gpu_mismatches"] == 0 and res["c_outputs_in_range"]}
    (ROOT / "results" / "tree_bitexact.json").write_text(json.dumps(res, indent=2))
    print(res)
    sys.exit(0 if res["gate"]["pass"] else 1)


if __name__ == "__main__":
    main()
