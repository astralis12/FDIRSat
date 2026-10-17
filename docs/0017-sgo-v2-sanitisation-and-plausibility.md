# 0017 — SGO v2: sanitise tree output, gate implausible telemetry, config-driven thresholds

**Status:** Proposed
**Date:** 2026-10-17

## Context

A CBMC proof of the SGO (`verify/cbmc/harness_sgo.c`) treats the distilled tree as an
untrusted black box and the four telemetry channels as arbitrary IEEE-754 floats.
For SGO v1 the four safety invariants (deep discharge, undervoltage, overcurrent,
over-temperature) hold for every finite input. Two classes of input are not handled:

1. Non-finite or physically impossible telemetry passes with no fault flag (ISS-014).
2. Out-of-range tree output reaches the actuators unchanged (ISS-015). This is the path
   a single-event upset in the tree tables would take.

The SGO thresholds are also literals tied to the old 3S/12 V bus.

## Decision

Revise the SGO as `fdir_sgo_v2.c`, keeping the `FDIRResult` interface unchanged:

- Validate tree output first. Any action > 3 or flag outside bits 0..4 is replaced by the
  safe default: S=0, B=1, L=1.
- Gate telemetry on plausibility (finite and within a physical range per channel). On
  failure, set bit 4 (now "sensor dropout or invalid reading") and apply the safe default.
- Take every threshold from `fdir_config.h`, derived per cell and scaled to 8S (28 V).
- Leave invariant order and actions unchanged, including the ADR-0015 DD+OT behaviour.

## Consequences

**Easier:**
- Proven by CBMC for every float input, against both a contract-respecting tree and a
  worst-case tree.
- Fault injection into the tree tables can now be detected.
- Moving to another pack configuration means editing one header.

**Harder:**
- The safe default is a design choice. It keeps the arrays connected, keeps only the
  trickle-charge path, and sheds secondary loads. It must be justified in the FMECA (M2).
- The plausibility ranges are new thresholds that need tracing too.
- Bit 4 changes meaning slightly, so the host decoder text and the paper must say so.

## Related

ADR-0005 (SGO layer), ADR-0015 (DD+OT), ISS-014, ISS-015.
