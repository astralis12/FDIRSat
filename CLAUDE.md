# Project context for coding sessions

## Goal
One Q1 journal paper (target Acta Astronautica → Advances in Space Research → JSSE as the Q2 floor):
verified onboard fault management for a small-spacecraft EPS. A learned policy is distilled into a
decision tree, guarded by a CBMC-proven safety layer (SGO), tested by fault injection on STM32 and a
real 8S1P pack, and quantified as residual risk per orbit. NASA SBIR fit: INSITU.1, EXPAND.3.

## Where things are
- Plan (source of truth for scope and milestones): claude.ai doc "FDIR Publication Plan" (owner has the link)
- `RUNBOOK.md` — commands in order; `docs/ISSUES.md` — issue log; `docs/00xx-*.md` — ADRs
- `firmware/` — SGO v1 (baseline, do not edit) and v2; `fdir_config.h` holds every threshold
- `verify/cbmc/` — proofs; `sim/` — DFN plant, ECM/EKF, NASA loader; `data/` is not committed

## Decisions already made (do not reopen without an ADR)
- 28 V reference bus: 8S·nP flight-like (8S4P), real bench pack 8S1P (1:4 current scale)
- Per-unit inputs (V per cell, C-rate, T, SoC, I_SA per-unit, U_bus per-unit); tree size is not a constraint
- Defender: supervised CE with FMECA severity weights. Adversary: stress tester (not robustness training)
- Distillation: closed-loop severity-weighted DAgger, behaviour cloning kept as ablation
- DFN = truth plant, 2RC ECM + EKF = onboard estimator, PINN = fast GPU plant only
- Data: XJTU-SPS all 4 subsets (FL/FD and AD held out for validation), NASA RW17–20, real pack
- Budget hardware only (Tokopedia, hobbyist): INA226 low-side, ADS1115 taps, DS18B20, relays, 8S charger

## Rules for every change
- Seeds explicit and logged; W&B for metrics; keep only final + best checkpoint per seed
- Any change to firmware C or generated tree → re-run `bash verify/cbmc/run_cbmc.sh` (must exit 0)
- `-std=c99 -Wall -Wextra -Werror`, zero warnings; never hand-edit generated tree C
- Every number in prose comes from `results/*.json`; never type results by hand
- Log anything that went wrong in `docs/ISSUES.md`: what, why, action, evidence, status
- Li-ion safety: never abuse-test cells; bench tests stop at 45 °C cell temperature and 3.3 V per cell

## Current next step
RUNBOOK step 4.4: batched GPU env (`sim/env_v2.py`): 8S per-unit plant, EKF in the loop, 6 inputs,
fault library from `config/fmeca_xjtu_sps.json`. 4.1–4.3 done. Data rules from 4.3: train only on orbit
files 18/34/90/94 (`xjtu_sps.load_training_orbits`); FD and AD are one validation pool (ISS-028); score
FD/AD without absolute temperature (ISS-029); C-rate scale is assumed (ISS-030).
Toolchain: Docker image `fdir-toolchain:24.04` (RUNBOOK step 0). Any change to `soc_ekf.c`,
`fdir_config.h` or the generated tables → re-run `run_cbmc.sh` (~50 min) and the bit-exact test.
GPU (RTX 3080 Laptop) is visible but the Python env has CPU-only torch; set up CUDA torch before 4.4.
