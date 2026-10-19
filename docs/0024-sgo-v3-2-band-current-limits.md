# 0024 — SGO v3.2: temperature-band cell current limits, derated charger, nested envelopes

**Status:** Accepted 19 Oct 2026 (owner: "ISS-059 approved as SGO v3.2 ... close every gap in one batch").
Code and proofs are in place; the retrain, re-verification and re-evaluation run with
`train/pipeline_v32.sh` (RUNBOOK §6).
**Date:** 2026-10-19

## Context

`docs/cell_limits_trace.md` traces every LG M50 (reference) and VTC6 (bench) limit. The M50 limits its
continuous current by temperature (spec 4.2.7 charge 0.3 C at 0..25 °C, 0.7 C at 25..45 °C; 4.2.9
discharge 0.5 C at -20..10 °C, 3.0 C at 10..25 °C, 1.5 C at 25..55 °C). Before v3.2 the simulated
charger charged at 0.5 C flat and the SGO had only a flat 1.5 C limit (ISS-059).

## Decision

1. **Derated charger (plant).** The simulated BCR derates its CC limit to 0.90 × the lowest M50 charge
   band within ±2 °C, and does not charge outside 0..45 °C (`sim/env_v2.py`, `charge_derate`). A
   per-env flag turns derating off to model a charger fault (PIL C6a).
2. **Invariant 7 (SGO v3.2).** Per configuration (`fdir_config_{ref,bench}.h`): stepped band tables.
   The band used is the lowest over [t_lo − 1 °C, t_hi + 1 °C] (temperature margin); the threshold is
   0.95 × the band limit (measurement margin), in pack amps. Over the threshold for 5 consecutive samples
   (persistence) → latched: charge → B ≥ 2, discharge → L ≥ 2, flag bit 7. Released after 60 consecutive
   samples below 0.8 × threshold (hysteresis). State lives in `FdirSgoState`, inside the CRC guard.
3. **Nested envelopes.** Charger setpoint (0.90 ×, ±2 °C) < SGO threshold (0.95 ×, ±1 °C) < datasheet
   limit. A working charger therefore never trips the SGO; the SGO acts only when the charger fails.
   Found while building v3.2: with the charger at the band limit, invariant 7 tripped on ~20 % of
   fault-free steps (ISS-060).
4. **Raise-only invariant 5.** Invariant 5 used to *set* S = 2, which lowered a tree S = 3; it now raises
   to ≥ 2 (same plant behaviour; needed for P14, ISS-061).

## Proof obligations (CBMC, `harness_sgo.c`, SGO v3 ref and bench, contract and worst case)

- P12a/P13a: persistent over-limit → latch set, B ≥ 2 / L ≥ 2, bit 7. P12b/P13b: held until the release
  time. P12e/P13e: no action without persistence. P12c/P13c: latches and counters stay in range.
- P12d/P13d: the band limit is ≤ the datasheet table at every temperature of the margin interval.
- P14a: invariants 5–7 never lower an action. P14b: the result equals the component-wise maximum of the
  output after invariants 1–4 and each later requirement (monotone composition; `FDIR_PROOF_HOOKS`).
- P10a relaxed to S ≥ 2 (raise-only invariant 5). Quick runs on 19 Oct: all pass, both configs.

## Consequences

- The plant changed (derating, 55 °C), so every coverage / false-action / hazard / residual-risk result
  is superseded (`results/SUPERSEDED.md`) and the tree is retrained and redistilled with v3.2 in the loop.
- The torch twin is stateful: v3.2 runs only with `SgoState` passed per episode; all evaluation scripts
  do this now (closes ISS-053 for v3.2). The ESA-ADB replay runs in time order.
- New metric: out-of-envelope time (share of steps with the cell outside the M50 datasheet envelope:
  band current, temperature window, cell voltage), per method and fault class.
- New PIL case C6 (cold charge at ~10 °C): C6a charger derating lost, C6b working (control).
- Guarded state grows to 388 B per spacecraft.

## Related

ADR-0018/0020/0021/0023, ISS-059, ISS-060, ISS-061, docs/cell_limits_trace.md.
