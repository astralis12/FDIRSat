# 0022 — Remove the PINN from the paper scope

**Status:** Accepted 18 Oct 2026 (owner: "drop the PINN").
**Date:** 2026-10-18

Note: the owner asked for this as "ADR-0019"; that number is already ADR-0019 (tree input
history), so this decision is ADR-0022.

## Context

The plan (M3) lists a PINN plant "validated against both [ECM and DFN], as error per regime", and
CLAUDE.md kept it as "fast GPU plant only". In the delivered pipeline the PINN has no role:

- The truth plant is the PyBaMM DFN (Chen2020 = LG INR21700-M50); the onboard estimator is the
  2RC ECM + EKF fitted to NASA pulses and the DFN (4.1, `results/ecm_scheduled.json`).
- The GPU training and evaluation plant (`sim/env_v2.py`) is the per-unit 2RC ECM with CUDA-graph
  rollouts; it already runs 10 seeds x DAgger and the full evaluation on the RTX 3080, so the speed
  argument for a PINN does not hold.
- No result in `results/` depends on a PINN.

## Decision

Drop the PINN from the paper scope. M3's gate becomes: ECM voltage error per regime against XJTU and
NASA, and the gap to the DFN reported for every regime where the DFN is used (no PINN bound).

## Consequences

- Plan objection R14 ("plant model unvalidated: PINN") is answered by the ECM-vs-DFN and
  ECM-vs-data errors instead; the R14 row's evidence changes, not its closure.
- Less work in M3; one fewer model to validate and to explain to reviewers.
- If a reviewer asks for a faster plant, the answer is the measured ECM rollout throughput.

## Related

CLAUDE.md "Decisions", plan M3 and R14, ADR-0023 (reference cell).
