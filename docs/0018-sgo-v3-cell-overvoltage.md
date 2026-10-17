# 0018 — SGO v3 candidate: cell-overvoltage invariant (and cell taps)

**Status:** Proposed (owner decision needed; not implemented in firmware)
**Date:** 2026-10-17

## Context

The 4.8 evaluation (`results/residual_risk.json`) shows the lowest coverage of all
FMECA modes for the one severity-1 mode the SGO does not back up: a BCR short
(uncontrolled charging). SGO v2 has invariants for deep discharge, undervoltage,
overcurrent and over-temperature, but none for cell overvoltage, so an overcharge
is prevented only if the tree isolates the array itself. The memoryless tree often
does not, and the overcharge hazard occurs in a large share of BCR-short runs.

A first candidate with the hazard threshold itself (4.25 V on the pack-average cell
voltage) changed nothing: in an 8S pack with cell spread, the highest cell crosses
4.25 V while the average stays below it. The SGO only sees the pack voltage.

## Proposal

1. Add invariant 5 to the SGO: pack-average cell voltage above `FDIR_VCELL_OV_MAX`
   (proposed 4.20 V) -> arrays isolated (S = 2) and charge limited (B >= 2).
2. Read the cell taps (ADS1115, already "optional" in RUNBOOK 5.2) and apply the
   same invariant to the highest cell, with the threshold at the cell limit
   (4.25 V). This is the robust form; (1) is the fallback without taps.

## Evidence (simulation, candidate only)

`results/residual_risk_sgo_v3_candidate.json` (XJTU orbit, same scenarios and seed
as `results/residual_risk.json`, flag `--sgo-v3-candidate`): with (1) at 4.20 V the
BCR-short hazard share drops below the expert's, while the fault-free hazard share
and the false-action rate stay as before. The coverage metric moves little because
it asks for a response within 10 min of onset and the invariant acts later, when
the voltage crosses the threshold (ISS-040).

## Consequences

- New threshold to trace in M2; CBMC harness gets P10 (overvoltage -> S = 2, B >= 2).
- Isolating the array at full charge is benign (the shunt does the same), so a false
  trigger costs little; the threshold must still sit above the CV set point.
- With taps, the 8S per-cell effects the plan mentions become testable on the bench.

## Related

ADR-0017 (SGO v2), ISS-036 (plant), ISS-040 (coverage metric), ISS-041 (this finding).
