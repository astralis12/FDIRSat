# 0023 — One reference cell: LG INR21700-M50 (5 Ah)

**Status:** Accepted 18 Oct 2026 (owner option (a)). Implemented in `firmware/include/fdir_config_ref.h`.
**Date:** 2026-10-18

## Context

XJTU-SPS does not publish its cell (ISS-030), so there is no datasheet to trace the reference
limits to. The reference configuration used "XJTU-type 18650 NCM, 2.0 Ah", while the DFN plant is
PyBaMM Chen2020 — the LG INR21700-M50 (NMC811, ~5 Ah) — and the EKF tables already carry the DFN's
5.0 Ah. So capacity, plant and limits came from three different sources.

## Decision

The reference spacecraft uses the LG INR21700-M50 for everything: DFN parameters (Chen2020),
capacity and limits. Source: LG Chem Product Specification LRB-PS-CY18.2Wh-M50, 2017-09-22, Rev 0
"Tentative" (`docs/datasheets/LG_INR21700-M50_product_spec_2017-09-22.pdf`, via Conrad item 2114679).

| Value | Before | Now | M50 clause |
|---|---|---|---|
| Cell capacity | 2.0 Ah | 5.0 Ah (18.2 Wh / 3.63 V; spec 1C = 4.85 A) | 2.1, 2.2, 4.2.7 |
| Pack (8S4P) | 8 Ah | 20 Ah | – |
| 1.5 C overcurrent | 12 A | 30 A | 4.2.9: 1.5 C at 25..55 degC |
| Over-temperature | 60 degC | 55 degC | 2.7: discharge -20..55 degC (section 5 says 60; stricter used) |
| Charge window | 0..45 degC (ASSUMED) | 0..45 degC (traced) | 2.7 |
| Charge voltage / OV invariant | 4.20 V | 4.20 V | 2.5 |

Per-unit inputs and the tree are unchanged; only the ampere thresholds move. The over-temperature
change also moves the simulator's threshold (`sim/env_v2.py` TH_T_CRIT, `train/fdir_torch.py`
T_CRIT) to 55 degC, so the evaluation results are re-run. The bench config keeps the VTC6 (60 degC).
Bench current scale to the reference becomes 20 Ah / Q_measured (~1:6.7 at 3.0 Ah).

## Consequences

- Full `run_cbmc.sh` and on-target tests re-run (config change).
- The M50 limits its currents by temperature: charge 0.3 C below 25 degC, 0.7 C at 25..45 degC;
  discharge 0.5 C below 10 degC. The SGO's 1.5 C limit and the simulated BCR's 0.5 C charge are not
  temperature-scheduled (ISS-059).
- The XJTU-SPS loader keeps its ASSUMED 2.0 Ah per string for the dataset's C-rate (ISS-030): that is
  the dataset's own unknown cell, not the reference spacecraft.
- The M50 spec in hand is Rev 0 "Tentative" (2017); a later revision may change limits.

## Related

ADR-0020 (configs), ADR-0022 (PINN), ISS-030, ISS-049, ISS-059.
