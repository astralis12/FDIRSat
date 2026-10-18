"""Emit the distilled decision tree as C (RUNBOOK 4.7).

Input : checkpoints/tree/<mode>_s<seed>.pkl (sklearn DecisionTreeClassifier,
        3 outputs S, B, L in 0..3, 6 per-unit inputs), chosen by
        results/dagger_summary.json ("deploy") unless --seed is given.
Output: firmware/src/fdir_tree.c (GENERATED, never edit by hand) with
          void fdir_tree_eval(const float in[6], uint8_t out[3]);
        Flat const tables: feature, threshold, left, right, packed leaf action
        (S | B << 2 | L << 4). Thresholds are floor32 of sklearn's float64
        thresholds, so float32 comparisons give sklearn's decisions exactly.
        Traversal is bounded by FDIR_TREE_DEPTH + 1 and every index is checked, so
        any input (NaN included) and any table contents give outputs in 0..3
        (proved: verify/cbmc/harness_tree.c, harness_tree_any.c). Optional RAM copy
        with CRC scrub (FDIR_TREE_IN_RAM) for SEU tolerance (item 5).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))
from fdir_torch import floor32  # noqa: E402


def leaf_actions(clf) -> np.ndarray:
    t = clf.tree_
    act = np.zeros((t.node_count, 3), np.int64)
    for o, c in enumerate(clf.classes_):
        c = np.asarray(c)
        act[:, o] = c[np.argmax(t.value[:, o, :len(c)], axis=1)]
    return act


C_TAIL = r'''#ifdef FDIR_TREE_IN_RAM
static uint8_t feat_ram[FDIR_TREE_NODES];
static float   thr_ram[FDIR_TREE_NODES];
static int16_t left_ram[FDIR_TREE_NODES];
static int16_t right_ram[FDIR_TREE_NODES];
static uint8_t leaf_ram[FDIR_TREE_NODES];
static uint32_t crc_ref;
#define T_FEAT  feat_ram
#define T_THR   thr_ram
#define T_LEFT  left_ram
#define T_RIGHT right_ram
#define T_LEAF  leaf_ram

#define ROTL(x, k) (((x) << (k)) | ((x) >> (32U - (k))))   /* no overflow: combine without arithmetic */

static uint32_t tables_crc(void)
{
    uint32_t c1 = fdir_tree_crc32(thr_ram, sizeof thr_ram), c2 = fdir_tree_crc32(left_ram, sizeof left_ram);
    uint32_t c3 = fdir_tree_crc32(right_ram, sizeof right_ram), c4 = fdir_tree_crc32(leaf_ram, sizeof leaf_ram);
    return fdir_tree_crc32(feat_ram, sizeof feat_ram) ^ ROTL(c1, 5U) ^ ROTL(c2, 11U) ^ ROTL(c3, 17U) ^ ROTL(c4, 23U);
}

static void restore(void)
{
    memcpy(feat_ram, feat_rom, sizeof feat_ram);
    memcpy(thr_ram, thr_rom, sizeof thr_ram);
    memcpy(left_ram, left_rom, sizeof left_ram);
    memcpy(right_ram, right_rom, sizeof right_ram);
    memcpy(leaf_ram, leaf_rom, sizeof leaf_ram);
}

void fdir_tree_init(void)
{
    restore();
#ifdef FDIR_TREE_PROOF_HAVOC                 /* CBMC: any table contents */
    __CPROVER_havoc_object(feat_ram);
    __CPROVER_havoc_object(thr_ram);
    __CPROVER_havoc_object(left_ram);
    __CPROVER_havoc_object(right_ram);
    __CPROVER_havoc_object(leaf_ram);
#endif
    crc_ref = tables_crc();
}

int fdir_tree_scrub(void)
{
    if (tables_crc() != crc_ref) {
        restore();
        return 1;
    }
    return 0;
}
#else
#define T_FEAT  feat_rom
#define T_THR   thr_rom
#define T_LEFT  left_rom
#define T_RIGHT right_rom
#define T_LEAF  leaf_rom
void fdir_tree_init(void) {}
int fdir_tree_scrub(void) { return 0; }
#endif

int fdir_tree_eval(const float in[FDIR_TREE_INPUTS], uint8_t out[3])
{
    uint32_t node = 0U;
    uint8_t pk = (uint8_t)FDIR_TREE_SAFE_PACKED;
    int d, status = 1;                             /* 1 until a leaf is reached */
    for (d = 0; d <= FDIR_TREE_DEPTH; d++) {
        int16_t l, r;
        uint8_t f;
        if (node >= FDIR_TREE_NODES) {
            break;                                 /* corrupted child index */
        }
        l = T_LEFT[node];
        if (l < 0) {
            pk = T_LEAF[node];
            status = 0;
            break;
        }
        f = T_FEAT[node];
        if (f >= FDIR_TREE_INPUTS) {
            break;                                 /* corrupted feature index */
        }
        r = T_RIGHT[node];
        /* NaN compares false and goes right; a negative index fails the bound above */
        node = (in[f] <= T_THR[node]) ? (uint32_t)(uint16_t)l : (uint32_t)(uint16_t)r;
    }
    if (status != 0) {
        pk = (uint8_t)FDIR_TREE_SAFE_PACKED;
    }
    out[0] = (uint8_t)(pk & 0x3U);
    out[1] = (uint8_t)((pk >> 2) & 0x3U);
    out[2] = (uint8_t)((pk >> 4) & 0x3U);
    return status;
}
'''


def emit(clf, src_note: str) -> str:
    t = clf.tree_
    n = t.node_count
    leaf = t.children_left < 0
    feat = np.where(leaf, 0, t.feature).astype(int)
    thr = np.where(leaf, 0.0, floor32(np.where(leaf, 0.0, t.threshold))).astype(np.float32)
    left = np.where(leaf, -1, t.children_left).astype(int)
    right = np.where(leaf, -1, t.children_right).astype(int)
    act = leaf_actions(clf)
    packed = (act[:, 0] | (act[:, 1] << 2) | (act[:, 2] << 4)).astype(int)
    if n > 32767:
        raise SystemExit("tree too large for int16 child indices")
    if clf.n_features_in_ > 9:
        raise SystemExit(f"fdir_tree.h has 9 inputs, the tree has {clf.n_features_in_}")

    def arr(ctype, name, vals, fmt):
        rows = [", ".join(fmt(v) for v in vals[k:k + 8]) for k in range(0, len(vals), 8)]
        return f"static const {ctype} {name}[FDIR_TREE_NODES] = {{\n    " + ",\n    ".join(rows) + "\n};\n"

    def f32(v) -> str:
        """float32 literal with 9 significant digits (round-trips) and always a decimal point."""
        if not np.isfinite(v):
            return "0.0f"
        s = f"{float(np.float32(v)):.9g}"
        if not any(c in s for c in ".e"):
            s += ".0"
        return s + "f"
    names = ["v_cell", "c_rate", "t_bat", "soc_ekf", "i_sa_pu", "u_bus_pu", "dv60", "i_sa_sun", "dsoc600"]
    head = "\n".join([
        "/* fdir_tree.c — GENERATED by codegen/emit_tree.py. Do not edit.",
        f" * {src_note}",
        f" * nodes {n}, depth {t.max_depth}, leaves {int(leaf.sum())}; {clf.n_features_in_} inputs: " + ", ".join(names[:clf.n_features_in_]),
        " *",
        " * Tables live in flash (const *_rom). With FDIR_TREE_IN_RAM the evaluator works on a RAM",
        " * copy (an SEU target, as on a real MCU) that fdir_tree_scrub() checks by CRC-32 against the",
        " * value recorded at init and restores from flash on mismatch. The evaluator checks every",
        " * child and feature index before use, so ANY table contents give outputs in 0..3",
        " * (CBMC: verify/cbmc/harness_tree.c and harness_tree_any.c).",
        " */",
        "#include <stdint.h>",
        "#include <string.h>",
        '#include "fdir_tree.h"',
        "",
        f"#define FDIR_TREE_NODES {n}",
        f"#define FDIR_TREE_DEPTH {t.max_depth}",
        "#define FDIR_TREE_SAFE_PACKED (0U | (1U << 2) | (1U << 4))   /* S=0, B=1, L=1: SGO safe default */",
        "",
        arr("uint8_t", "feat_rom", feat, str),
        arr("float", "thr_rom", thr, f32),
        arr("int16_t", "left_rom", left, str),
        arr("int16_t", "right_rom", right, str),
        arr("uint8_t", "leaf_rom", packed, str),
    ])
    return head + C_TAIL


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="sw_dagger")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--ckpt", default="checkpoints/tree", help="tree checkpoint directory")
    ap.add_argument("--out", default=str(ROOT / "firmware" / "src" / "fdir_tree.c"))
    a = ap.parse_args()
    seed = a.seed
    if seed is None:
        seed = json.load(open(ROOT / "results" / "dagger_summary.json"))["deploy"]["seed"]
    pk = ROOT / a.ckpt / f"{a.mode}_s{seed}.pkl"
    clf = pickle.load(open(pk, "rb"))                 # our own checkpoint (train/dagger_adversary.py)
    note = f"source {pk.name} sha256 {hashlib.sha256(pk.read_bytes()).hexdigest()[:16]}"
    Path(a.out).write_text(emit(clf, note), encoding="utf-8", newline="\n")
    print("wrote", a.out, "nodes", clf.tree_.node_count, "depth", clf.tree_.max_depth)


if __name__ == "__main__":
    main()
