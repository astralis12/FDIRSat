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
# SGO v3.2 invariant 7 (ISS-059, ADR-0024): fdir_config_ref.h (LG M50) + fdir_config.h
Q_CELL, N_PAR = 5.0, 4.0
CHG_EDGES, CHG_C = (0.0, 25.0, 45.0), (0.3, 0.7)
DIS_EDGES, DIS_C = (10.0, 25.0), (0.5, 3.0, 1.5)
ILIM_MARGIN, ILIM_T_MARGIN, ILIM_PERSIST, ILIM_RELEASE, ILIM_REL_FRAC = 0.05, 1.0, 5, 60, 0.8


class SgoState:
    """Twin of FdirSgoState (fdir_sgo_v3.h): one per environment, updated in place by sgo_v2
    (CUDA-graph safe). Create one per episode and pass it as sgo_v2(..., state=st)."""

    def __init__(self, n: int, device):
        self.ct = torch.zeros(n, dtype=torch.bool, device=device)
        z = lambda: torch.zeros(n, dtype=torch.long, device=device)  # noqa: E731
        self.chg_on, self.chg_cnt, self.chg_rel = z(), z(), z()
        self.dis_on, self.dis_cnt, self.dis_rel = z(), z(), z()

    def reset(self):
        for t in (self.ct, self.chg_on, self.chg_cnt, self.chg_rel, self.dis_on, self.dis_cnt, self.dis_rel):
            t.zero_()


def _f32(x, like):
    return torch.full_like(like, float(np.float32(x)))


def _chg_band_at(t):
    lim = torch.zeros_like(t)
    n = len(CHG_C)
    for k in range(n):
        lo, hi = CHG_EDGES[k], CHG_EDGES[k + 1]
        inb = (t >= lo) & ((t < hi) | ((k == n - 1) & (t <= hi)))
        lim = torch.where(inb, _f32(CHG_C[k], t), lim)
    return lim


def _dis_band_at(t):
    lim = _f32(DIS_C[0], t)
    go = torch.ones_like(t, dtype=torch.bool)
    for k, e in enumerate(DIS_EDGES):
        go = go & (t >= e)
        lim = torch.where(go, _f32(DIS_C[k + 1], t), lim)
    return lim


def ilim_charge_c(a, b):
    """Twin of fdir_ilim_charge_c: lowest charge limit over [a, b]; 0 for NaN / inverted."""
    m = torch.minimum(_chg_band_at(a), _chg_band_at(b))
    for e in CHG_EDGES:
        inside = (a < e) & (b >= e)
        m = torch.where(inside, torch.minimum(m, _chg_band_at(torch.full_like(a, e))), m)
    return torch.where(a <= b, m, torch.zeros_like(m))


def ilim_discharge_c(a, b):
    m = torch.minimum(_dis_band_at(a), _dis_band_at(b))
    for e in DIS_EDGES:
        inside = (a < e) & (b >= e)
        m = torch.where(inside, torch.minimum(m, _dis_band_at(torch.full_like(a, e))), m)
    return torch.where(a <= b, m, _f32(DIS_C[0], m))


def _ilim_update(on, cnt, rel, over, below, live):
    """Twin of ilim_update (persistence + hysteresis); only rows with live=True change."""
    cnt2 = torch.where(over, (cnt + 1).clamp(max=ILIM_PERSIST), torch.zeros_like(cnt))
    trip = cnt2 >= ILIM_PERSIST
    held = (on != 0) & ~trip
    rel_inc = torch.where(below, (rel + 1).clamp(max=ILIM_RELEASE), torch.zeros_like(rel))
    release = held & (rel_inc >= ILIM_RELEASE)
    on2 = torch.where(trip, torch.ones_like(on), torch.where(release, torch.zeros_like(on), on))
    rel2 = torch.where(held & ~release, rel_inc, torch.zeros_like(rel))
    on.copy_(torch.where(live, on2, on))
    cnt.copy_(torch.where(live, cnt2, cnt))
    rel.copy_(torch.where(live, rel2, rel))


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
           ct_latch: torch.Tensor | None = None, t_lo: torch.Tensor | None = None,
           state: SgoState | None = None) -> tuple[torch.Tensor, torch.Tensor]:
    """act (N, 3) tree output in 0..255; inputs per cell / per unit, soc in 0..1.
    Returns the actuator command (N, 3) and the fault-flag byte (N,).
    ov_invariant=True (default since 17 Oct) is SGO v3 (fdir_sgo_v3.c, ADR-0018): invariant 5,
    pack-average cell overvoltage > V_OV -> arrays isolated (S=2), charge limited (B>=2), flag bit 5.
    ov_invariant=False reproduces SGO v2.
    With ov_invariant=True, invariant 6 (charge-temperature window, ISS-050) also runs: temp
    outside T_CHG -> charge inhibited (B>=2), flag bit 6, latched until temp is inside T_REL.
    ct_latch (N,) bool carries the latch between steps and is updated in place; None = a fresh
    latch every call (correct except inside the hysteresis band). One sensor here, so
    t_lo defaults to temp (one sensor); with temp_vote3 pass temp = t_hi and t_lo.
    state (SgoState) = SGO v3.2: carries the invariant 6 latch AND runs invariant 7 (temperature-
    band current limits with persistence and hysteresis). Without state, invariant 7 does not run
    (it needs history), so evaluation of v3.2 must pass a state per episode (ADR-0024)."""
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
        S = torch.where(ov, S.clamp_min(2), S)                 # raise-only (v3.2, P14)
        B = torch.where(ov, B.clamp_min(2), B)
        # invariant 6; the C code returns before it for dropout / implausible input
        if state is not None:
            ct_latch = state.ct
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
        if state is not None:
            # invariant 7 (v3.2): same float32 operation order as fdir_sgo_v3.c / fdir_onboard.c
            one = torch.ones_like(temp)
            q = _f32(Q_CELL, temp) * _f32(N_PAR, temp)
            i_pack = (c_rate * _f32(Q_CELL, temp)) * _f32(N_PAR, temp)
            a_t = tl - _f32(ILIM_T_MARGIN, temp)
            b_t = temp + _f32(ILIM_T_MARGIN, temp)
            k = one - _f32(ILIM_MARGIN, temp)
            thr_c = (k * ilim_charge_c(a_t, b_t)) * q
            thr_d = (k * ilim_discharge_c(a_t, b_t)) * q
            rf = _f32(ILIM_REL_FRAC, temp)
            _ilim_update(state.chg_on, state.chg_cnt, state.chg_rel, -i_pack > thr_c, -i_pack < rf * thr_c, live)
            _ilim_update(state.dis_on, state.dis_cnt, state.dis_rel, i_pack > thr_d, i_pack < rf * thr_d, live)
            con = live & (state.chg_on != 0)
            don = live & (state.dis_on != 0)
            flags = flags | torch.where(con | don, 128, 0)
            B = torch.where(con, B.clamp_min(2), B)
            L = torch.where(don, L.clamp_min(2), L)
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
