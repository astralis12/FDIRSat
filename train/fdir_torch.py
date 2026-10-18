"""Torch versions of the deployed FDIR pieces, for closed-loop rollouts:
  sgo_v2   the safety layer, same logic and order as firmware/src/fdir_sgo_v2.c
           (thresholds from fdir_config.h, per cell / per unit)
  GpuTree  a fitted sklearn tree stored as flat arrays (feature, threshold,
           left, right, leaf action) and traversed with gathers on the GPU
"""
from __future__ import annotations

import numpy as np
import torch

# fdir_config.h, per cell / per unit (pack values divide by N_S, currents by Q * nP)
V_CRIT, V_NOM, C_CRIT, T_CRIT, SOC_CRIT_PCT = 3.0, 3.6, 1.5, 55.0, 5.0   # ref config (fdir_config_ref.h, ADR-0023)
V_PL = (0.5, 4.5)
C_PL = 10.0
T_PL = (-60.0, 150.0)
SOC_PL = (-5.0, 105.0)
SAFE = (0, 1, 1)                        # SAFE_S, SAFE_B, SAFE_L


V_OV = 4.20                             # SGO v3 invariant 5 (fdir_config_ref.h FDIR_VCELL_OV_MAX, ADR-0018)
# SGO v3 invariant 6 (ISS-050): charge-temperature window and release band (fdir_config_ref.h)
T_CHG = (0.0, 45.0)
T_REL = (2.0, 42.0)


def temp_vote3(t: torch.Tensor, pl=T_PL, disagree: float = 5.0, invalid: float = 1000.0):
    """Twin of fdir_temp_vote3 (fdir_sgo_v3.c): t (N, 3) -> t_hi, t_lo (N,), single_fault (N,) bool."""
    ok = (t >= pl[0]) & (t <= pl[1])
    n = ok.sum(1)
    big = torch.full_like(t, float("inf"))
    srt = torch.sort(torch.where(ok, t, big), 1).values          # valid readings first, ascending
    med = srt[:, 1]
    two_hi, two_lo = srt[:, 1], srt[:, 0]
    t_hi = torch.where(n == 3, med, torch.where(n == 2, two_hi, torch.full_like(med, invalid)))
    t_lo = torch.where(n == 3, med, torch.where(n == 2, two_lo, torch.full_like(med, invalid)))
    dis = ((srt[:, 1] - srt[:, 0]) > disagree) | ((srt[:, 2] - srt[:, 1]) > disagree)
    single = (n < 3) | ((n == 3) & dis)
    return t_hi, t_lo, single


def sgo_v2(act: torch.Tensor, v_cell, c_rate, temp, soc, ov_invariant: bool = True,
           ct_latch: torch.Tensor | None = None, t_lo: torch.Tensor | None = None) -> tuple[torch.Tensor, torch.Tensor]:
    """act (N, 3) tree output in 0..255; inputs per cell / per unit, soc in 0..1.
    Returns the actuator command (N, 3) and the fault-flag byte (N,).
    ov_invariant=True (default since 17 Oct) is SGO v3 (fdir_sgo_v3.c, ADR-0018): invariant 5,
    pack-average cell overvoltage > V_OV -> arrays isolated (S=2), charge limited (B>=2), flag bit 5.
    ov_invariant=False reproduces SGO v2.
    With ov_invariant=True, invariant 6 (charge-temperature window, ISS-050) also runs: temp
    outside T_CHG -> charge inhibited (B>=2), flag bit 6, latched until temp is inside T_REL.
    ct_latch (N,) bool carries the latch between steps and is updated in place; None = a fresh
    latch every call (correct except inside the hysteresis band). One sensor here, so
    t_lo defaults to temp (one sensor); with temp_vote3 pass temp = t_hi and t_lo."""
    a = act.long().clone()
    flags = torch.zeros(a.shape[0], dtype=torch.long, device=a.device)
    safe = torch.zeros_like(a)                     # no host->device copy: CUDA-graph safe
    safe[:, 1] = SAFE[1]
    safe[:, 2] = SAFE[2]
    # C1: invalid tree output -> safe default
    bad_tree = (a > 3).any(1)
    a = torch.where(bad_tree[:, None], safe, a)
    soc_pct = 100.0 * soc
    # dropout: all channels exactly zero (V here is per cell; the C code uses the pack value)
    dropout = (v_cell == 0) & (c_rate == 0) & (temp == 0) & (soc_pct == 0)
    plaus = ((v_cell >= V_PL[0]) & (v_cell <= V_PL[1]) & (c_rate.abs() <= C_PL) &
             (temp >= T_PL[0]) & (temp <= T_PL[1]) & (soc_pct >= SOC_PL[0]) & (soc_pct <= SOC_PL[1]))
    flags = flags | torch.where(dropout | ~plaus, 16, 0)
    a = torch.where((~dropout & ~plaus)[:, None], safe, a)
    live = ~dropout & plaus
    dd = live & ((soc_pct < SOC_CRIT_PCT) | (v_cell < V_CRIT))
    uv = live & (v_cell < V_NOM)
    oc = live & (c_rate > C_CRIT)
    ot = live & (temp > T_CRIT)
    flags = flags | torch.where(dd, 8, 0) | torch.where(uv, 1, 0) | torch.where(oc, 2, 0) | torch.where(ot, 4, 0)
    S, B, L = a[:, 0], a[:, 1], a[:, 2]
    # invariant 1 (deep discharge) else 2 (undervoltage)
    L = torch.where(dd, 3, torch.where(uv, L.clamp_min(1), L))
    S = torch.where(dd, 1, S)
    B = torch.where(dd, 1, B)
    # 3 overcurrent
    B = torch.where(oc, B.clamp_min(2), B)
    L = torch.where(oc & (L == 0), 1, L)
    # 4 over-temperature
    S = torch.where(ot, 2, S)
    L = torch.where(ot, L.clamp_min(2), L)
    if ov_invariant:
        ov = live & (v_cell > V_OV)
        flags = flags | torch.where(ov, 32, 0)
        S = torch.where(ov, 2, S)
        B = torch.where(ov, B.clamp_min(2), B)
        # invariant 6; the C code returns before it for dropout / implausible input
        prev = ct_latch if ct_latch is not None else torch.zeros_like(live)
        tl = temp if t_lo is None else t_lo
        out_win = (temp > T_CHG[1]) | (tl < T_CHG[0])
        in_rel = (temp < T_REL[1]) & (tl > T_REL[0])
        lat = torch.where(live, out_win | (prev & ~in_rel), prev)
        if ct_latch is not None:
            ct_latch.copy_(lat)
        inh = live & lat
        flags = flags | torch.where(inh, 64, 0)
        B = torch.where(inh, B.clamp_min(2), B)
    return torch.stack([S, B, L], 1), flags


def floor32(t: np.ndarray) -> np.ndarray:
    """Largest float32 <= each float64 threshold. sklearn tests float32 x <= float64 t;
    no float32 lies in (floor32(t), t], so x <= floor32(t) gives the same decision in
    single precision (used by the GPU tree and the generated C tree)."""
    f = np.asarray(t, dtype=np.float64).astype(np.float32)
    up = f.astype(np.float64) > t
    f[up] = np.nextafter(f[up], np.float32(-np.inf))
    return f


class GpuTree:
    """sklearn multi-output DecisionTreeClassifier (3 outputs, classes 0..3)."""

    def __init__(self, clf, device):
        t = clf.tree_
        self.depth = int(t.max_depth)
        self.feature = torch.tensor(np.maximum(t.feature, 0), device=device)
        self.threshold = torch.tensor(floor32(t.threshold), device=device)
        self.left = torch.tensor(t.children_left, device=device)
        self.right = torch.tensor(t.children_right, device=device)
        self.is_leaf = self.left < 0
        # leaf action per output: class with the largest weight, mapped to the class label
        val = t.value                                    # (nodes, outputs, max_classes)
        cls = [np.asarray(c) for c in clf.classes_]
        act = np.zeros((t.node_count, len(cls)), np.int64)
        for o, c in enumerate(cls):
            act[:, o] = c[np.argmax(val[:, o, :len(c)], axis=1)]
        self.leaf_action = torch.tensor(act, device=device)

    def __call__(self, x: torch.Tensor) -> torch.Tensor:
        node = torch.zeros(x.shape[0], dtype=torch.long, device=x.device)
        for _ in range(self.depth):                      # at most depth gather steps
            f = self.feature[node]
            go_left = x.gather(1, f[:, None])[:, 0] <= self.threshold[node]
            nxt = torch.where(go_left, self.left[node], self.right[node])
            node = torch.where(self.is_leaf[node], node, nxt)
        return self.leaf_action[node]
