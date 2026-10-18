# 0020 — SGO v3.1: charge-temperature invariant, median-of-3 temperature, ref / bench configs

**Status:** Accepted 18 Oct 2026 (owner: "ISS-050 approved; implement in SGO v3 together with
ADR-0018 ..."; bench configuration as a separate build). Implemented in `firmware/src/fdir_sgo_v3.c`,
`firmware/include/fdir_config_{ref,bench}.h`, CBMC P11.
**Date:** 2026-10-18

## Context

- The bench cell is a Sony/Murata US18650VTC6: charge only at 0..45 degC (Murata SDS), discharge
  -20..60 degC. SGO v2/v3 had one temperature limit (60 degC, over-temperature), so charging at
  45..60 degC or below 0 degC was not prevented (ISS-050). Charging below 0 degC plates lithium.
- The SGO read one temperature sensor; a single sensor fault could hide a hot or cold battery.
- The bench pack (8S1P VTC6, ~3 Ah) differs from the reference pack (8S4P, 2.0 Ah XJTU-type cells).
  Running the reference build on the bench would mix "the FDIR works" with "the FDIR is
  mis-parameterised" (owner, 18 Oct).

## Decision

1. **Invariant 6 (SGO v3).** Battery outside `[FDIR_TBAT_CHARGE_MIN, FDIR_TBAT_CHARGE_MAX]` = [0, 45] degC
   -> charge inhibited (B >= 2: the BCR stops charging; arrays keep supplying the loads), flag bit 6.
   Latched; released only inside `[FDIR_TBAT_CHARGE_REL_MIN, _REL_MAX]` = [2, 42] degC. Applied after
   invariant 5, so it only raises B. With deep discharge (B = 1, trickle) it raises B to 2: no trickle
   charge outside the window.
2. **Median-of-3 temperature** (`fdir_temp_vote3`, trusted code in the SGO file). Implausible readings
   are dropped; 3 valid -> median; 2 valid -> highest for the hot checks, lowest for the cold check;
   fewer -> an implausible value, so the SGO flags bit 4 and drives the safe default. A lost sensor or
   one more than 5 degC from the median sets bit 4 (single-sensor fault, FMECA A6), and operation
   continues on the vote.
3. **Two build configurations, one code base.** `fdir_config_ref.h` (8S4P, 2.0 Ah, OV 4.20 V; drives
   simulation and residual risk, unchanged) and `fdir_config_bench.h` (8S1P VTC6, Q placeholder 3.0 Ah,
   OV 4.25 V). `-DFDIR_CONFIG_BENCH` selects the bench file (PlatformIO `env:bench`). Source, tree and
   proofs are the same; `run_cbmc.sh` proves SGO v3 for both (`v3_*`, `v3bench_*`). The tree takes
   per-unit inputs, so only Q x N_p changes. The bench OV threshold is 4.25 V because the bench charger
   CV is fixed at 4.2 +/- 0.05 V and cannot be lowered (ISS-049).

## Proof obligations (CBMC, `verify/cbmc/harness_sgo.c`, SGO v3, both configs)

- P2: flags use bits 0..6 only.
- P11a: outside the window -> latch set, B >= 2, bit 6. P11b: latch set and outside the release band ->
  held, B >= 2. P11c: inside the release band (and inside the window) -> cleared. P11d: latch in {0, 1}.
- P11e: the vote lies between any two valid sensors (one-fault tolerant). P11f: fewer than two valid
  sensors -> bit 4 and safe default. P11g: three valid -> one median value.

## Consequences

- The SGO is now stateful (one latch byte). The DUT keeps one latch per simulated spacecraft in the PIL
  'Y' path. The torch twin (`train/fdir_torch.py`) takes an optional latch; the evaluation scripts
  call it stateless, which differs only inside the hysteresis band (ISS-053).
- The plant model has a new hazard `charge_temp` (charging below 0 or above 45 degC). PIL case C5
  (cold battery) tests set, hold and release. C4 stays reserved for the capacity mis-estimate case.
- A sensor-invalid safe default keeps B = 1 (trickle, 0.02 C) as before (P9b); the latch is not
  consulted there. Trickle charge at an unknown temperature is accepted as minor.
- Bench hardware: three DS18B20 on the pack (median-of-3).

## Related

ADR-0017, ADR-0018, ISS-047, ISS-049, ISS-050, ISS-053.
