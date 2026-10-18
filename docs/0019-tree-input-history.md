# 0019 — Short input history for the tree (9 inputs)

**Status:** Accepted (owner instruction, 17 Oct 2026)
**Date:** 2026-10-17

## Context

The deployed tree sees six instantaneous per-unit inputs (CLAUDE.md decision). The 4.8
evaluation (ISS-042) shows the cost: an array fault is invisible in eclipse, battery
degradation is a slow drift, and the policy pays a false-action rate on fault-free steps.

## Decision

Add three physical history features, computed onboard at 1 Hz by
`firmware/src/fdir_features.c` (twin: `sim/features.py`), so the tree has 9 inputs:

| # | Feature | Definition | Why |
| --- | --- | --- | --- |
| 6 | `dv60` | v_cell(t) − v_cell(t − 60 s) [V per cell] | load steps, short circuits, open string |
| 7 | `i_sa_sun` | mean of i_sa_pu over the current sunlit phase (i_sa_pu > 0.02), held through eclipse; 1.0 before the first sunlit sample | array faults stay visible in eclipse |
| 8 | `dsoc600` | soc_ekf(t) − soc_ekf(t − 600 s), one sample per minute | energy balance: charge loss, degradation |

Only plausible readings enter the buffers (v_cell in [0.5, 4.5] V, soc in [−0.2, 1.2],
i_sa_pu in [0, 2]); otherwise the previous value is repeated, so the features are always
finite. The six original inputs and their order are unchanged; the SGO still reads only
the first four. The ring buffers carry a CBMC bounds proof (`verify/cbmc/harness_features.c`).

## Consequences

- Tree, defender and DAgger are retrained on 9 inputs; the 6-input results stay as the
  ablation (`results/*_tree6*.json`).
- 760 bytes of RAM for the buffers; a few hundred cycles per step.
- The features are auditable: each is a named physical quantity with a unit.

## Related

ADR-0018 (SGO v3), ISS-042.
