"""Batched GPU EPS environment (RUNBOOK 4.4): 8S per-unit plant, onboard EKF in
the loop, 6 per-unit inputs, fault library from the FMECA map.

One environment = one spacecraft EPS on one orbit, stepped at dt = 1 s:
  plant      8 cells in series, each the SoC-scheduled 2RC model of 4.1
             (results/ecm_scheduled.json) with per-cell capacity / resistance
             spread and an ageing factor, so the plant differs from what the
             onboard EKF assumes; lumped pack thermal node
  power      solar array (sunlit / eclipse), BCR with CC-CV charge, three loads
             with task bursts, unregulated battery bus; all currents in C-rate
             of the cell capacity (per-unit), voltages per cell
  faults     the 17 XJTU-SPS classes of config/fmeca_xjtu_sps.json, injected
             at a random time with a random magnitude
  actions    S, B, L in 0..3 (SGO semantics, fdir_sgo_v2.c) act on the plant
  EKF        the onboard estimator (same equations, tables and noise as
             firmware/src/soc_ekf.c, float32) estimates SoC from the measured
             cell voltage and current; its SoC is input 4
  inputs     v_cell, c_rate, t_bat, soc_ekf, i_sa_pu, u_bus_pu (CLAUDE.md order)
  labels     expert action: FMECA response for the true fault, then the SGO
             invariants evaluated on the TRUE state (deep discharge,
             undervoltage, overcurrent, over-temperature) - the defender learns
             to reach this from the 6 noisy inputs only

Parameters marked ASSUMED are placeholders for M2 (orbit propagation, FMECA,
pack sizing). Stand-in cell: the 4.1 DFN cell (Chen2020); per-unit inputs keep
the policy independent of the absolute capacity.

Gate (smoke): python sim/env_v2.py --smoke -> results/env_v2_smoke.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "codegen"))
sys.path.insert(0, str(ROOT / "sim"))

N_INPUTS = 6
INPUT_NAMES = ("v_cell", "c_rate", "t_bat", "soc_ekf", "i_sa_pu", "u_bus_pu")
N_FAULTS = 18                           # 0 = normal, 1..17 = XJTU-SPS classes
N_CELLS = 8                             # 8S reference pack (CLAUDE.md)
V_CELL_MAX, V_CELL_CUTOFF = 4.2, 2.5

# SGO thresholds per cell / per unit (fdir_config.h: 3.0 V, 3.6 V, 1.5 C, 60 C, 5 %)
TH_V_CRIT, TH_V_NOM, TH_C_CRIT, TH_T_CRIT, TH_SOC_CRIT = 3.0, 3.6, 1.5, 60.0, 0.05


@dataclass
class Orbit:
    name: str
    period_s: float
    eclipse_s: float                    # ASSUMED until M2 propagation (plan: never assume eclipse)


ORBITS = {
    "xjtu": Orbit("xjtu", 95 * 60, 35 * 60),        # XJTU-SPS project page: 60 sunlit + 35 shadow
    "o2_sso_1030": Orbit("o2_sso_1030", 95.6 * 60, 35.5 * 60),   # ASSUMED, ~550 km SSO
    "o3_dawn_dusk": Orbit("o3_dawn_dusk", 96.7 * 60, 8 * 60),    # ASSUMED, eclipse season
}


@dataclass
class EnvConfig:
    n_env: int = 16384
    dt: float = 1.0
    orbit: str = "xjtu"
    episode_orbits: float = 3.0
    fault_prob: float = 0.9             # share of envs that get a fault this episode
    # ASSUMED sizing (per unit of cell capacity, i.e. C-rate)
    sa_c_rate: float = 0.55             # array current at full sun
    load_base_c: tuple = (0.06, 0.08, 0.06)   # survival, primary, secondary loads
    task_c: float = 0.30                # task burst on load 2
    task_duty: float = 0.25
    charge_cc_c: float = 0.5            # BCR constant-current limit
    trickle_c: float = 0.02
    # thermal (ASSUMED): heat capacity, thermal resistance, ambient sun/eclipse
    c_th_j_per_k: float = 8 * 70.0
    r_th_k_per_w: float = 1.5
    t_amb_sun: float = 28.0
    t_amb_ecl: float = 12.0
    # spreads
    q_spread: float = 0.02
    r_spread: float = 0.05
    soh_range: tuple = (0.80, 1.0)
    # sensors (INA226 / ADS1115 / DS18B20 class, ASSUMED)
    v_noise: float = 0.002
    i_noise_c: float = 0.005
    t_noise: float = 0.1
    t_quant: float = 0.0625
    seed: int = 0
    device: str = "cuda" if torch.cuda.is_available() else "cpu"


def _interp(x: torch.Tensor, xp: torch.Tensor, fp: torch.Tensor) -> torch.Tensor:
    """numpy.interp semantics (clamped), batched; xp strictly increasing.
    fp may be (K,) or (M, K): M tables on the same grid share one search."""
    j = torch.clamp(torch.searchsorted(xp, x.contiguous(), right=True) - 1, 0, xp.numel() - 2)
    x0, x1 = xp[j], xp[j + 1]
    y0, y1 = fp[..., j], fp[..., j + 1]
    y = (y1 - y0) / (x1 - x0) * (x - x0) + y0
    return torch.where(x <= xp[0], fp[..., :1], torch.where(x >= xp[-1], fp[..., -1:], y)) if fp.dim() == 2 else         torch.where(x <= xp[0], fp[0], torch.where(x >= xp[-1], fp[-1], y))


class Tables:
    """4.1 tables + OCV, as the reference EKF builds them, on the device."""

    def __init__(self, device):
        from emit_ekf_tables import reference_model
        ecm = json.load(open(ROOT / "results" / "ecm_scheduled.json"))
        cv = json.load(open(ROOT / "results" / "ekf_sigma_cv.json"))
        tab, ocv, noise = reference_model(ecm, cv, ROOT / "data" / "dfn")
        f = lambda a: torch.tensor(np.asarray(a), dtype=torch.float32, device=device)
        self.q_ah = float(tab.q_ah)
        self.knot, self.r0, self.r1, self.tau1, self.r2, self.tau2 = map(f, (tab.soc, tab.r0, tab.r1, tab.tau1, tab.r2, tab.tau2))
        self.ocv_soc, self.ocv_v, self.ocv_dv = f(ocv.soc), f(ocv.v), f(ocv.dv)
        self.p_stack = torch.stack([self.r0, self.r1, self.tau1, self.r2, self.tau2])
        self.ocv_stack = torch.stack([self.ocv_v, self.ocv_dv])
        self.noise = noise

    def params(self, soc):
        """(r0, r1, tau1, r2, tau2) at soc, one search for all five tables."""
        return tuple(_interp(soc, self.knot, self.p_stack).unbind(0))

    def ocv_and_slope(self, soc):
        v, dv = _interp(soc, self.ocv_soc, self.ocv_stack).unbind(0)
        return v, dv

    def ocv(self, soc):
        return _interp(soc, self.ocv_soc, self.ocv_v)

    def ocv_slope(self, soc):
        return _interp(soc, self.ocv_soc, self.ocv_dv)


class BatchedEKF:
    """soc_ekf.c / SoCEKF.step, batched in float32 (guards included)."""

    def __init__(self, tab: Tables, soc0: torch.Tensor):
        self.t, n = tab, tab.noise
        b = soc0.shape[0]
        self.x = torch.zeros(b, 3, device=soc0.device)
        self.x[:, 0] = soc0
        self.P0 = torch.diag(torch.tensor([n["p0_soc"], n["p0_rc"], n["p0_rc"]], device=soc0.device)).float()
        self.P = self.P0.expand(b, 3, 3).clone()
        self.Q = torch.tensor([n["q_soc"], n["q_rc"], n["q_rc"]], device=soc0.device).float()
        self.R = float(n["r_v"])

    def step(self, i_prev, i_now, v, dt):
        t = self.t
        # G1 as in soc_ekf.c: implausible current or dt -> the step is rejected, state kept
        i_max = 10.0 * t.q_ah
        keep_x, keep_P = self.x, self.P
        rej = ~((i_prev.abs() <= i_max) & (i_now.abs() <= i_max)) | (not 0.0 <= dt <= 60.0)
        i_prev = torch.where(rej, torch.zeros_like(i_prev), i_prev)
        i_now = torch.where(rej, torch.zeros_like(i_now), i_now)
        r0, r1, tau1, r2, tau2 = t.params(self.x[:, 0])
        a1 = torch.exp(-dt / (r1 * (tau1 / r1)))
        a2 = torch.exp(-dt / (r2 * (tau2 / r2)))
        a = torch.stack([torch.ones_like(a1), a1, a2], 1)
        bvec = torch.stack([torch.full_like(a1, -dt / (3600.0 * t.q_ah)), r1 * (1 - a1), r2 * (1 - a2)], 1)
        self.x = a * self.x + bvec * i_prev[:, None]
        self.P = a[:, :, None] * self.P * a[:, None, :] + torch.diag_embed(self.Q.expand_as(a))
        soc = self.x[:, 0]
        ocv_v, ocv_dv = t.ocv_and_slope(soc)
        H = torch.stack([ocv_dv, -torch.ones_like(soc), -torch.ones_like(soc)], 1)
        y = ocv_v - self.x[:, 1] - self.x[:, 2] - r0 * i_now
        hp = torch.einsum("bi,bij->bj", H, self.P)
        S = (hp * H).sum(1) + self.R
        ok = (S >= 0.5 * self.R) & (v >= 0.5) & (v <= 4.5)
        K = torch.einsum("bij,bj->bi", self.P, H) / S[:, None]
        innov = torch.where(ok, v - y, torch.zeros_like(v))
        x_up = self.x + K * innov[:, None]
        P_up = self.P - torch.einsum("bi,bj->bij", K, hp)
        self.x = torch.where(ok[:, None], x_up, self.x)
        self.P = torch.where(ok[:, None, None], P_up, self.P)
        # guards (G3)
        self.x[:, 0] = self.x[:, 0].clamp(-0.2, 1.2)
        self.x[:, 1:] = self.x[:, 1:].clamp(-1.0, 1.0)
        bad = ~torch.isfinite(self.P).all((1, 2)) | (self.P.abs() > 1.0).any((1, 2))
        self.P = torch.where(bad[:, None, None], self.P0.expand_as(self.P), self.P)
        self.x = torch.where(rej[:, None], keep_x, self.x)
        self.P = torch.where(rej[:, None, None], keep_P, self.P)

    @property
    def soc(self):
        return self.x[:, 0]

    @property
    def std(self):
        return self.P[:, 0, 0].clamp_min(0).sqrt()


def load_fmeca() -> dict:
    m = json.load(open(ROOT / "config" / "fmeca_xjtu_sps.json", encoding="utf-8"))
    return {int(k): v for k, v in m["fault_classes"].items()}


def fmeca_response_table(device) -> tuple[torch.Tensor, torch.Tensor]:
    """Per fault id: baseline expert action (S, B, L) from the FMECA response
    channels, and the proposed severity (0 for normal). Mapping (PROPOSED):
      S -> S=1 for array faults, S=2 when the array must be isolated (BCR short)
      B -> B=2 (charge limited); B=3 (battery isolated) for bus faults
      L -> L=2 for severity 1-2, L=1 for severity 3
      report -> no action."""
    m = load_fmeca()
    act = torch.zeros(N_FAULTS, 3, dtype=torch.long)
    sev = torch.zeros(N_FAULTS)
    for k, v in m.items():
        s = v["severity"] or 0
        sev[k] = s
        for ch in v["response"]:
            if ch == "S":
                act[k, 0] = 2 if v["subsystem"] == "BCR" else 1
            elif ch == "B":
                act[k, 1] = 3 if v["subsystem"] == "Bus" else 2   # bus fault: isolate the battery
            elif ch == "L":
                act[k, 2] = 2 if s in (1, 2) else 1
    return act.to(device), sev.to(device)


class EPSEnv:
    def __init__(self, cfg: EnvConfig):
        self.cfg, dev = cfg, cfg.device
        self.dev = dev
        self.tab = Tables(dev)
        self.g = torch.Generator(device=dev).manual_seed(cfg.seed)
        self.orbit = ORBITS[cfg.orbit]
        self.resp, self.severity = fmeca_response_table(dev)
        self.reset()

    # ------------------------------------------------------------ helpers
    def _u(self, *shape, lo=0.0, hi=1.0):
        return lo + (hi - lo) * torch.rand(*shape, generator=self.g, device=self.dev)

    def _n(self, *shape):
        return torch.randn(*shape, generator=self.g, device=self.dev)

    def reset(self):
        c, B, C = self.cfg, self.cfg.n_env, N_CELLS
        self.t = torch.zeros(B, device=self.dev)
        self.phase0 = self._u(B) * self.orbit.period_s
        soh = self._u(B, lo=c.soh_range[0], hi=c.soh_range[1])
        self.q_cell = self.tab.q_ah * soh[:, None] * (1 + c.q_spread * self._n(B, C))          # Ah
        self.r_scale = (1 + 2.0 * (1 - soh))[:, None] * (1 + c.r_spread * self._n(B, C))       # ageing raises R
        self.soc = (self._u(B, lo=0.55, hi=0.95)[:, None] + 0.01 * self._n(B, C)).clamp(0.02, 0.99)
        self.v1 = torch.zeros(B, C, device=self.dev)
        self.v2 = torch.zeros(B, C, device=self.dev)
        self.temp = self._u(B, lo=18.0, hi=30.0)
        # fault: id, onset time, magnitude, PDM open (0) or short (1)
        T = c.episode_orbits * self.orbit.period_s
        has = self._u(B) < c.fault_prob
        self.fault = torch.where(has, torch.randint(1, N_FAULTS, (B,), generator=self.g, device=self.dev),
                                 torch.zeros(B, dtype=torch.long, device=self.dev))
        self.t_onset = self._u(B, lo=0.1 * T, hi=0.7 * T)
        self.mag = self._u(B, lo=0.3, hi=1.0)
        self.pdm_short = self._u(B) < 0.5
        self.task_phase = self._u(B) * 3600.0
        self.i_prev = torch.zeros(B, device=self.dev)
        self.action = torch.zeros(B, 3, dtype=torch.long, device=self.dev)
        self.ekf = BatchedEKF(self.tab, (self.soc.mean(1) + 0.05 * self._n(B)).clamp(0.05, 0.98))
        self.obs, self.info = self._observe(torch.zeros(B, device=self.dev), torch.zeros(B, device=self.dev),
                                            torch.zeros(B, device=self.dev), self.cell_voltage(torch.zeros(B, device=self.dev)))
        z = torch.zeros(B, device=self.dev)
        self.info.update({"i_load": z, "unpowered": z.bool(), "sunlit": z.bool()})
        return self.obs

    def active_fault(self):
        return torch.where(self.t >= self.t_onset, self.fault, torch.zeros_like(self.fault))

    def cell_voltage(self, i_cell_c):
        """Terminal voltage per cell [V] for a C-rate current (discharge > 0)."""
        i_a = i_cell_c[:, None] * self.q_cell
        r0, _, _, _, _ = self.tab.params(self.soc.reshape(-1))
        r0 = r0.reshape(self.soc.shape) * self.r_scale
        return self.tab.ocv(self.soc.reshape(-1)).reshape(self.soc.shape) - self.v1 - self.v2 - r0 * i_a

    # ------------------------------------------------------------ dynamics
    def step(self, action: torch.Tensor | None = None):
        """Advance dt with the given action (B, 3) in 0..3 (None = keep previous).
        Returns obs (B, 6), expert action (B, 3), info dict."""
        c, dt = self.cfg, self.cfg.dt
        if action is not None:
            self.action = action.long().clamp(0, 3)
        S, Bc, L = self.action[:, 0], self.action[:, 1], self.action[:, 2]
        f = self.active_fault()
        mag = self.mag
        fe = lambda k: (f == k)

        # illumination
        ph = torch.remainder(self.t + self.phase0, self.orbit.period_s)
        sunlit = ph >= self.orbit.eclipse_s
        # solar array (C-rate available)
        sa_loss = torch.zeros_like(mag)
        sa_loss = torch.where(fe(1) | fe(2), 0.2 + 0.4 * mag, sa_loss)
        ramp = ((self.t - self.t_onset) / (2 * self.orbit.period_s)).clamp(0, 1)
        sa_loss = torch.where(fe(3), (0.1 + 0.25 * mag) * ramp, sa_loss)
        i_sa = c.sa_c_rate * sunlit.float() * (1 - sa_loss)
        i_sa = torch.where(S == 1, 0.7 * i_sa, i_sa)
        i_sa = torch.where(S >= 2, torch.zeros_like(i_sa), i_sa)

        # loads (C-rate): survival, primary (+ task burst), secondary
        task = (torch.remainder(self.t + self.task_phase, 3600.0) < c.task_duty * 3600.0).float()
        load = torch.stack([torch.full_like(mag, c.load_base_c[0]),
                            c.load_base_c[1] + c.task_c * task,
                            torch.full_like(mag, c.load_base_c[2])], 1)
        # PDM open, PDM short (the channel's latching current limiter trips, as
        # ECSS-E-ST-20C requires) and load open all leave the channel unpowered
        for k, ch in ((12, 0), (13, 1), (14, 2), (15, 0), (16, 1), (17, 2)):
            load[:, ch] = torch.where(fe(k), torch.zeros_like(mag), load[:, ch])
        load[:, 2] = torch.where(L >= 1, torch.zeros_like(mag), load[:, 2])
        load[:, 1] = torch.where(L >= 2, torch.zeros_like(mag), load[:, 1])
        load[:, 0] = torch.where(L >= 3, 0.5 * load[:, 0], load[:, 0])
        bus_open = fe(9)
        i_load = torch.where(bus_open, torch.zeros_like(mag), load.sum(1))
        i_load = i_load + torch.where(fe(10), 1.8 + 1.2 * mag, torch.zeros_like(mag))      # bus short
        i_load = i_load + torch.where(fe(11), 0.05 + 0.15 * mag, torch.zeros_like(mag))    # insulation leakage

        # cell parameters at the SoC before the step (shared by BCR and dynamics)
        sflat = self.soc.reshape(-1)
        r0, r1, tau1, r2, tau2 = (p.reshape(self.soc.shape) for p in self.tab.params(sflat))
        r0, r1, r2 = r0 * self.r_scale, r1 * self.r_scale, r2 * self.r_scale
        qdeg = torch.where(fe(7)[:, None], (0.6 + 0.25 * (1 - mag))[:, None], torch.ones_like(self.q_cell))
        rdeg = torch.where(fe(7)[:, None], (1.3 + 0.7 * mag)[:, None], torch.ones_like(self.q_cell))
        ocv_now = self.tab.ocv(sflat).reshape(self.soc.shape)
        # BCR: charge from the array surplus, CC-CV. The voltage loop limits the charge
        # current so that no cell's terminal voltage exceeds 4.2 V (exact for R0; RC
        # states move little within one step)
        v_now = ocv_now - self.v1 - self.v2 - r0 * rdeg * (self.i_prev[:, None] * self.q_cell)
        v_max = v_now.max(1).values
        surplus = (i_sa - i_load).clamp_min(0)
        i_cv = ((V_CELL_MAX - (ocv_now - self.v1 - self.v2)) / (r0 * rdeg * self.q_cell)).min(1).values.clamp_min(0)
        i_chg = torch.minimum(torch.minimum(surplus, torch.full_like(surplus, c.charge_cc_c)), i_cv)
        i_chg = torch.where(fe(4), torch.zeros_like(i_chg), i_chg)                          # BCR open
        i_chg = torch.where(fe(5), torch.minimum(surplus, torch.full_like(surplus, 0.9)), i_chg)  # short: no CV
        i_chg = torch.where(fe(6), (0.55 + 0.3 * (1 - mag)) * i_chg, i_chg)               # power loss
        # B acts through the BCR, so it cannot limit a shorted BCR; only S=2 (array isolated) stops that
        ctl = ~fe(5)
        i_chg = torch.where(ctl & (Bc == 1), torch.minimum(i_chg, torch.full_like(i_chg, c.trickle_c)), i_chg)
        i_chg = torch.where(ctl & (Bc >= 2), torch.zeros_like(i_chg), i_chg)
        # battery current (discharge > 0): deficit minus charge
        deficit = (i_load - i_sa).clamp_min(0)
        i_bat = deficit - i_chg
        bat_open = fe(8) | (Bc >= 3)
        i_bat = torch.where(bat_open, torch.zeros_like(i_bat), i_bat)
        unpowered = bat_open & (deficit > 0)                                               # bus collapses

        # cells: SoC and RC states with per-cell capacity and resistance
        i_a = i_bat[:, None] * self.q_cell                                                  # A per cell
        a1, a2 = torch.exp(-dt / tau1), torch.exp(-dt / tau2)
        self.soc = self.soc - i_a * dt / (3600.0 * self.q_cell * qdeg)
        self.v1 = a1 * self.v1 + r1 * rdeg * (1 - a1) * i_a
        self.v2 = a2 * self.v2 + r2 * rdeg * (1 - a2) * i_a
        v_cell_true = self.tab.ocv(self.soc.reshape(-1)).reshape(self.soc.shape) - self.v1 - self.v2 - r0 * rdeg * i_a
        # thermal: I^2 R of the pack + BCR loss heat, to ambient
        p_heat = (i_a ** 2 * (r0 + r1 + r2) * rdeg).sum(1) + torch.where(fe(6), 2.0 * mag, torch.zeros_like(mag))
        p_heat = p_heat + torch.where(fe(5), 4.0 * (v_max > V_CELL_MAX).float() * mag, torch.zeros_like(mag))
        t_amb = torch.where(sunlit, torch.full_like(mag, c.t_amb_sun), torch.full_like(mag, c.t_amb_ecl))
        self.temp = self.temp + dt * (p_heat - (self.temp - t_amb) / c.r_th_k_per_w) / c.c_th_j_per_k

        # bus voltage (unregulated battery bus); open battery -> array-only bus
        v_pack = v_cell_true.sum(1)
        u_bus = torch.where(bat_open, torch.where(unpowered, 0.3 * v_pack, v_pack), v_pack)
        u_bus = torch.where(fe(10), 0.6 * u_bus, u_bus)
        u_bus = torch.where(bus_open, torch.zeros_like(u_bus), u_bus)
        self.obs, self.info = self._observe(i_bat, i_sa, u_bus, v_cell_true)
        self.info.update({"i_load": i_load, "unpowered": unpowered, "sunlit": sunlit})
        expert = self.expert_action()
        self.i_prev = i_bat
        self.t = self.t + dt
        return self.obs, expert, self.info

    def _observe(self, i_bat, i_sa, u_bus, v_cells):
        c = self.cfg
        B = i_bat.shape[0]
        v_meas = v_cells.mean(1) + c.v_noise * self._n(B)
        i_meas = i_bat + c.i_noise_c * self._n(B)
        t_meas = torch.round((self.temp + c.t_noise * self._n(B)) / c.t_quant) * c.t_quant
        # onboard EKF: per-cell average, current in A of the nominal (table) cell
        self.ekf.step(self.i_prev * self.tab.q_ah, i_meas * self.tab.q_ah, v_meas, self.cfg.dt)
        obs = torch.stack([v_meas, i_meas, t_meas, self.ekf.soc,
                           i_sa / self.cfg.sa_c_rate, u_bus / (N_CELLS * V_CELL_MAX)], 1)
        info = {"soc_true_min": self.soc.min(1).values, "soc_true_mean": self.soc.mean(1),
                "v_cell_true_min": v_cells.min(1).values, "v_cell_true_max": v_cells.max(1).values,
                "temp_true": self.temp.clone(), "i_bat": i_bat, "ekf_std": self.ekf.std,
                "fault": self.active_fault()}
        return obs, info

    def expert_action(self):
        """FMECA response for the true fault, then the SGO invariants on the
        true state (same order and actions as fdir_sgo_v2.c)."""
        f = self.active_fault()
        a = self.resp[f].clone()
        S, Bc, L = a[:, 0], a[:, 1], a[:, 2]
        i = self.info
        dd = (i["soc_true_min"] < TH_SOC_CRIT) | (i["v_cell_true_min"] < TH_V_CRIT)
        uv = i["v_cell_true_min"] < TH_V_NOM
        oc = i["i_bat"] > TH_C_CRIT
        ot = i["temp_true"] > TH_T_CRIT
        S = torch.where(dd, torch.ones_like(S), S)
        Bc = torch.where(dd, torch.ones_like(Bc), Bc)
        L = torch.where(dd, torch.full_like(L, 3), torch.where(uv, L.clamp_min(1), L))
        Bc = torch.where(oc, Bc.clamp_min(2), Bc)
        L = torch.where(oc & (L == 0), torch.ones_like(L), L)
        S = torch.where(ot, torch.full_like(S, 2), S)
        L = torch.where(ot, L.clamp_min(2), L)
        return torch.stack([S, Bc, L], 1)

    def hazards(self):
        """Unsafe outcomes for the stress tester / residual risk (true state)."""
        i = self.info
        return {"deep_discharge": i["soc_true_min"] < 0.02,
                "overcharge": i["v_cell_true_max"] > 4.25,
                "over_temp": i["temp_true"] > TH_T_CRIT,
                "unpowered": i["unpowered"]}


# ---------------------------------------------------------------- smoke gate
def smoke(cfg: EnvConfig, steps: int) -> dict:
    env = EPSEnv(cfg)
    B = cfg.n_env
    assert env.obs.shape == (B, N_INPUTS), env.obs.shape
    t0 = time.time()
    traj_err, act_hist = [], []
    finite = True
    expert = None
    for k in range(steps):
        a = None if expert is None else expert          # closed loop with the expert
        obs, expert, info = env.step(a)
        assert obs.shape == (B, N_INPUTS) and obs.dtype == torch.float32
        assert expert.shape == (B, 3) and int(expert.min()) >= 0 and int(expert.max()) <= 3
        finite &= bool(torch.isfinite(obs).all())
        if k % 60 == 0:
            traj_err.append((env.ekf.soc - info["soc_true_mean"]).abs().cpu())
            act_hist.append(expert.cpu())
    if cfg.device == "cuda":
        torch.cuda.synchronize()
    wall = time.time() - t0
    err = torch.stack(traj_err)                     # (snapshots, B)
    normal = env.fault == 0
    settled = err[len(err) // 3:]
    acts = torch.stack(act_hist)
    fault_hit = {}
    for k in range(1, N_FAULTS):
        m = (env.fault == k).cpu()
        fault_hit[k] = int(m.sum())
    obs = env.obs
    res = {
        "device": cfg.device, "gpu": torch.cuda.get_device_name(0) if cfg.device == "cuda" else None,
        "n_env": B, "steps": steps, "dt": cfg.dt, "orbit": cfg.orbit, "seed": cfg.seed,
        "wall_s": wall, "env_steps_per_s": B * steps / wall,
        "obs_shape": list(obs.shape), "all_finite": finite,
        "ekf_abs_err_settled_normal_p95_pct": float(100 * torch.quantile(settled[:, normal.cpu()].flatten(), 0.95)),
        "ekf_abs_err_settled_all_p95_pct": float(100 * torch.quantile(settled.flatten(), 0.95)),
        "obs_ranges": {n: [float(obs[:, j].min()), float(obs[:, j].max())] for j, n in enumerate(INPUT_NAMES)},
        "faults_per_class": fault_hit, "normal_envs": int(normal.sum()),
        "expert_action_share_nonzero": float((acts.sum(2) > 0).float().mean()),
        "hazards_final": {k: int(v.sum()) for k, v in env.hazards().items()},
    }
    checks = {
        "shapes asserted every step": True,
        "all observations finite": finite,
        "every fault class injected": all(v > 0 for v in fault_hit.values()),
        "EKF p95 SoC error < 5 % (normal envs, settled)": res["ekf_abs_err_settled_normal_p95_pct"] < 5.0,
        "v_cell inside SGO plausibility band [0.5, 4.5]": 0.5 <= res["obs_ranges"]["v_cell"][0] and res["obs_ranges"]["v_cell"][1] <= 4.5,
    }
    # determinism: same seed -> same first observations
    e1, e2 = EPSEnv(EnvConfig(**{**cfg.__dict__, "n_env": 256})), EPSEnv(EnvConfig(**{**cfg.__dict__, "n_env": 256}))
    for _ in range(50):
        o1, _, _ = e1.step()
        o2, _, _ = e2.step()
    checks["deterministic for a fixed seed"] = bool(torch.equal(o1, o2))
    res["checks"] = checks
    res["gate"] = {"pass": all(checks.values()), "failed": [k for k, v in checks.items() if not v]}
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--n-env", type=int, default=16384)
    ap.add_argument("--steps", type=int, default=3 * 5700)
    ap.add_argument("--orbit", default="xjtu", choices=list(ORBITS))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "results" / "env_v2_smoke.json"))
    a = ap.parse_args()
    if not a.smoke:
        ap.print_help()
        return
    res = smoke(EnvConfig(n_env=a.n_env, orbit=a.orbit, seed=a.seed), a.steps)
    Path(a.out).write_text(json.dumps(res, indent=2))
    for k, v in res["checks"].items():
        print(("PASS " if v else "FAIL ") + k)
    print({k: res[k] for k in ("env_steps_per_s", "wall_s", "ekf_abs_err_settled_normal_p95_pct", "expert_action_share_nonzero", "hazards_final")})
    print("GATE", res["gate"])


if __name__ == "__main__":
    main()
