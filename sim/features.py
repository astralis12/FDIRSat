"""Batched torch twin of firmware/src/fdir_features.c (ADR-0019).

Same float32 operations in the same order as the C code, one row per spacecraft.
State tensors are re-bound (not modified in place) at each step, so the CUDA-graph
wrapper (train/graph_rollout.py) can copy them back into their buffers.
"""
from __future__ import annotations

import torch

V_LEN, SOC_LEN, SOC_DEC, SUN_TH, SUN_CAP = 60, 10, 60, 0.02, 86400
FEATURE_NAMES = ("dv60", "i_sa_sun", "dsoc600")


def _plaus(x, lo, hi):
    return (x >= lo) & (x <= hi)


class Features:
    def __init__(self, n: int, device):
        f = lambda v: torch.full((n,), v, dtype=torch.float32, device=device)
        z = lambda: torch.zeros(n, dtype=torch.long, device=device)
        self.v_ring = torch.full((n, V_LEN), 3.7, dtype=torch.float32, device=device)
        self.soc_ring = torch.full((n, SOC_LEN), 0.5, dtype=torch.float32, device=device)
        self.v_idx, self.v_cnt, self.soc_idx, self.soc_cnt, self.tick, self.sun_cnt = z(), z(), z(), z(), z(), z()
        self.v_last, self.soc_last, self.isa_last, self.sun_mean = f(3.7), f(0.5), f(0.0), f(1.0)
        self.was_sun = torch.zeros(n, dtype=torch.bool, device=device)

    def step(self, v_cell, soc, i_sa_pu) -> torch.Tensor:
        vs = torch.where(_plaus(v_cell, 0.5, 4.5), v_cell, self.v_last)
        ss = torch.where(_plaus(soc, -0.2, 1.2), soc, self.soc_last)
        ia = torch.where(_plaus(i_sa_pu, 0.0, 2.0), i_sa_pu, self.isa_last)
        self.v_last, self.soc_last, self.isa_last = vs, ss, ia
        # dv60
        oldest = torch.where(self.v_cnt < V_LEN, torch.zeros_like(self.v_idx), self.v_idx)
        old = self.v_ring.gather(1, oldest[:, None])[:, 0]
        dv = torch.where(self.v_cnt == 0, torch.zeros_like(vs), vs - old)
        self.v_ring = self.v_ring.scatter(1, self.v_idx[:, None], vs[:, None])
        self.v_idx = (self.v_idx + 1) % V_LEN
        self.v_cnt = (self.v_cnt + 1).clamp_max(V_LEN)
        # i_sa_sun
        sun = ia > SUN_TH
        self.sun_cnt = torch.where(sun & ~self.was_sun, torch.zeros_like(self.sun_cnt), self.sun_cnt)
        upd = sun & (self.sun_cnt < SUN_CAP)
        cnt = torch.where(upd, self.sun_cnt + 1, self.sun_cnt)
        new_mean = (self.sun_mean + (ia - self.sun_mean) / cnt.clamp_min(1).to(torch.float32)).clamp(0.0, 2.0)
        self.sun_mean = torch.where(upd, new_mean, self.sun_mean)
        self.sun_cnt = cnt
        self.was_sun = sun
        # dsoc600
        oldest = torch.where(self.soc_cnt < SOC_LEN, torch.zeros_like(self.soc_idx), self.soc_idx)
        old = self.soc_ring.gather(1, oldest[:, None])[:, 0]
        dsoc = torch.where(self.soc_cnt == 0, torch.zeros_like(ss), ss - old)
        write = self.tick == 0
        self.soc_ring = torch.where(write[:, None], self.soc_ring.scatter(1, self.soc_idx[:, None], ss[:, None]), self.soc_ring)
        self.soc_idx = torch.where(write, (self.soc_idx + 1) % SOC_LEN, self.soc_idx)
        self.soc_cnt = torch.where(write, (self.soc_cnt + 1).clamp_max(SOC_LEN), self.soc_cnt)
        self.tick = (self.tick + 1) % SOC_DEC
        return torch.stack([dv, self.sun_mean, dsoc], 1)
