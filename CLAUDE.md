# Project context for coding sessions

## Goal
One Q1 journal paper (target Acta Astronautica → Advances in Space Research → JSSE as the Q2 floor):
verified onboard fault management for a small-spacecraft EPS. A learned policy is distilled into a
decision tree, guarded by a CBMC-proven safety layer (SGO), tested by fault injection on STM32 and a
real 8S1P pack, and quantified as residual risk per orbit. NASA SBIR fit: INSITU.1, EXPAND.3.

## Where things are
- Plan (source of truth for scope and milestones): claude.ai doc "FDIR Publication Plan" (owner has the link)
- `RUNBOOK.md` — commands in order; `docs/ISSUES.md` — issue log; `docs/00xx-*.md` — ADRs
- `firmware/` — SGO v1 (baseline, do not edit), v2, v3; `fdir_config.h` + `fdir_config_{ref,bench}.h` hold every threshold
- `verify/cbmc/` — proofs; `sim/` — DFN plant, ECM/EKF, NASA loader; `data/` is not committed

## Decisions already made (do not reopen without an ADR)
- 28 V reference bus: 8S4P LG INR21700-M50 (5 Ah, = DFN Chen2020; ADR-0023) = 20 Ah, real bench pack 8S1P
  US18650VTC6 (~3 Ah): current scale ≈ 1:6.7, exactly 20 Ah / Q_measured. Same C-rates: 1.5 C = 30 A ref, ≈ 4.5 A bench
- Two build configs, one code base (ADR-0020): `fdir_config_ref.h` (sim, residual risk; unchanged) and
  `fdir_config_bench.h` (`-DFDIR_CONFIG_BENCH`, `pio run -e bench`). Same source, tree and proofs;
  `run_cbmc.sh` proves SGO v3 for both, record both hashes. Per-cell V and T thresholds are shared
- SGO v3 = ADR-0018 (overvoltage, bit 5) + ADR-0020 (charge window 0..45 °C, release 2/42 °C, bit 6,
  median-of-3 battery temperature). The SGO is stateful (one latch byte)
- Per-unit inputs (V per cell, C-rate, T, SoC, I_SA per-unit, U_bus per-unit); tree size is not a constraint
- Defender: supervised CE with FMECA severity weights. Adversary: stress tester (not robustness training)
- Distillation: closed-loop severity-weighted DAgger, behaviour cloning kept as ablation
- DFN (Chen2020 = LG M50) = truth plant, 2RC ECM + EKF = onboard estimator and GPU plant. No PINN (ADR-0022)
- Data: XJTU-SPS all 4 subsets (FL/FD and AD held out for validation), NASA RW17–20, real pack
- Budget hardware only (Tokopedia, hobbyist): INA226 low-side, ADS1115 taps, DS18B20, relays, 8S charger

## Rules for every change
- Seeds explicit and logged; W&B for metrics; keep only final + best checkpoint per seed
- Any change to firmware C or generated tree → re-run `bash verify/cbmc/run_cbmc.sh` (must exit 0)
- `-std=c99 -Wall -Wextra -Werror`, zero warnings; never hand-edit generated tree C
- Every number in prose comes from `results/*.json`; never type results by hand
- Log anything that went wrong in `docs/ISSUES.md`: what, why, action, evidence, status
- Li-ion safety: never abuse-test cells; bench tests stop at 45 °C cell temperature and 3.3 V per cell

## Plan doc updates (continued here from 18 Oct 2026; copy into the claude.ai plan doc when convenient)

### Bench vs reference scaling
| Quantity | Reference (sim, residual risk) | Bench | Ratio |
|---|---|---|---|
| Pack | 8S4P LG INR21700-M50 (ADR-0023) | 8S1P Sony/Murata US18650VTC6 | – |
| Cell capacity Q | 5.0 Ah (M50 spec) | 3.0 Ah rated; **measure** at C/5, 4.20 → 3.3 V | – |
| Pack capacity Q·N_p | 20 Ah | ≈ 3 Ah (Q_measured) | ≈ 1:6.7 (20 / Q_measured) |
| 1.5 C (overcurrent limit) | 30 A | ≈ 4.5 A | same C-rate |
| Over-temperature | 55 °C (M50 spec 2.7) | 60 °C (VTC6) | – |
| OV invariant (pack average) | 4.20 V/cell | 4.25 V/cell (charger CV fixed 4.2 ± 0.05 V) | – |
| Charge window | 0..45 °C (M50 spec 2.7) | 0..45 °C (Murata SDS) | – |
| Bench test window | – | 4.20 → 3.3 V/cell, stop at 45 °C cell (CLAUDE.md rule) | – |

### Bench measurement and instrumentation rules
- Measure Q, don't trust the label: C/5 discharge from 4.20 V to the 3.3 V bench floor; define SoC and build the
  bench OCV and resistance tables over that same window (RUNBOOK 5.4). The bench EKF uses tables and Q from that cell.
- INA226 full scale is ±81.92 mV across the shunt (2.5 µV LSB). The fitted 0.1 Ω shunt saturates at ≈ 0.82 A:
  replace it with 0.01 Ω (≈ 8.2 A full scale, 25 µA LSB), ≥ 2 W at peak current, calibrated against the multimeter.
- Electronic load must take ≈ 4.5 A at up to 33.6 V (≈ 150 W) for 1.5 C tests. The FDIR cases do not need 1.5 C
  continuous: run them at ≤ 1 C (≈ 3 A, ≤ 100 W) and use 1.5 C only as short overcurrent pulses.
- Temperature: three DS18B20 on the pack (median-of-3, ADR-0020).
- Check the tree's input envelope on the bench: compare per-input histograms / min-max of the bench inputs with the
  simulator's training distributions (`obs_ranges`). Any input outside the envelope is a domain-shift finding to
  report, not to hide (the VTC6 chemistry differs from Chen2020).

### Test cases (processor-in-the-loop now, true HIL on the bench later)
- C1 nominal, C2 FMECA classes 1..17, C3 corrupted telemetry / memory upsets — on the correctly configured build.
- C4 capacity mis-estimate (deliberate): run the **ref** build with `-DFDIR_N_PARALLEL=1` (assumes 5.0 Ah) on the ~3 Ah
  1P bench pack (≈ 60 % of the assumed capacity, close to the NASA cells' ≈ 51 %) after C1–C3, labelled
  as such. It mimics on-orbit ageing (the NASA cells kept ≈ 51 %) and shows how the SGO floor and the EKF degrade.
- C5 cold battery (ADR-0020): C5a warming through 0 → 2 °C (inhibit, then release), C5b cold soak (inhibit held).

### Item 6 sources now in hand
- SPENVIS 4.6.14 SEU rates (`results/seu_rates.json`; GEO check passed). D_high is the main value, per orbit.
- Cell: VTC6 Technical Information Rev 0.1 + Murata SDS values (owner); ECSS clause mapping (owner, not checked
  against the texts); MIL-HDBK-217F model forms (owner; the STM32 example needs the commercial-part factors —
  docs/owner_inputs_2026-10-18.md).

## Current next step
Read `HANDOFF.md` first (status per item and commands). As of 18 Oct 2026: SGO v3.1 (ADR-0018 + ADR-0020),
9-input tree (seed 6) flashed and bit-exact on the DUT, ref/bench configs. Next: compare_methods,
residual_risk (tree9 + v3), fault-injection campaign and timing sweep on the final firmware, residual-risk band,
report rebuild. Hardware: INA226 0.01 Ω shunt, 3× DS18B20, load, then measure Q and run C1–C5 as true HIL.
Environment: `.venv` (Python 3.11, torch cu130, RTX 3080); toolchain Docker image `fdir-toolchain:24.04`;
DUT = Black Pill STM32F411CEU6 on ST-Link + COM5 (USART1 PA9/PA10, 460800 baud).
Any change to firmware C, `fdir_config*.h` or generated tables/tree → `run_cbmc.sh` (~90 min) + bit-exact tests.
