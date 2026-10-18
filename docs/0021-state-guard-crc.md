# 0021 — CRC-16 guard on the EKF state, history buffers and charge latch

**Status:** Accepted 18 Oct 2026 (owner: "add CRC-16 protection with re-initialisation to the EKF
state and history buffers, re-prove, and re-run the 3000-injection campaign"). Implemented in
`firmware/src/fdir_state_guard.c`; CBMC `verify/cbmc/harness_guard.c`.
**Date:** 2026-10-18

## Context

The first 3000-injection campaign on SGO v3.1 + tree9 (`results/fault_injection_preguard.json`)
failed the M6 gate: 60 silent data corruptions, all in RAM that the tree-table scrub does not
cover — EKF state 47/700 and history buffers 13/700 (ISS-057). These objects are rewritten every
step, so a fixed reference copy (as for the tree tables) does not apply.

## Decision

- Each spacecraft's mutable state is one `FdirGuardedState` = EKF (48 B) + history (328 B) +
  SGO v3 charge latch (1 B) + CRC-16/CCITT-FALSE (2 B). The DUT keeps 7 (`gstate`) plus one for the
  single-spacecraft path.
- **Seal** after every step: the CRC is computed over EKF, history and latch.
- **Check** before the next step: on a mismatch the state is rebuilt — EKF re-initialised at the SoC
  whose table OCV matches the measured cell voltage (bisection, 24 steps), history cleared, charge
  latch set to 1 (the SGO releases it in the same step if the battery is inside the release band) —
  and the step's status byte carries bit 5 (`FDIR_GUARD_ST_REPAIRED`), so the upset is *detected*.
- Table-driven CRC (256 x 16-bit table in flash); bitwise CRC would cost ~66k cycles per step.

## Proof obligations (CBMC)

- L0: table byte step = bitwise CRC-16/CCITT-FALSE. L1: one step is injective in the byte. L2: one
  step is injective in the register. By induction over the bytes, any single-bit flip in the guarded
  bytes changes the CRC, so it is repaired (G3). A symbolic 380-byte state in one proof ran out of
  memory, hence the lemma split.
- G1: the rebuilt SoC is in [0, 1] for any float voltage (NaN, Inf included) and any OCV curve (OCV stubbed as any float).
- G2 / G4 on a concrete instance (SoC 0.6, 3.8 V, linear OCV stub) with any wrong CRC value; a symbolic state timed out (> 30 min). G2: a sealed state passes the check unchanged. G4: a mismatch is repaired, the rebuilt state has
  SoC in [0, 1], latch 1, and is sealed (passes the next check).

## Consequences

- Cost per spacecraft step (`results/timing_sweep.json`, with the guard): 983.8 us at 16 MHz, 181.1 us
  at 100 MHz / 3 wait states (was ~5k cycles before the guard). 7 spacecraft at 1 Hz: 1.82 % duty at
  16 MHz, 0.31 % at 100 MHz.
- A repair loses the EKF's convergence and the 60 s / 10 min history: the tree runs on a cold-start
  EKF for a few minutes. This is a detected, bounded degradation, not an SDC.
- Upsets that land between the check and the seal inside one step (the step's own computation) are
  not covered; the injection campaign flips bits between frames, which is the dominant exposure.
- Padding byte between latch and CRC is not guarded (never read).

## Related

ADR-0019 (history), ADR-0020 (latch), ISS-057, ISS-058.
