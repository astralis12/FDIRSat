# Handoff — 19 Oct 2026 (for a new session: "Read HANDOFF.md and continue")

Read first: CLAUDE.md, RUNBOOK.md (§6 = the v3.2 pipeline), docs/ISSUES.md (ISS-014..061),
docs/0017-0024, docs/cell_limits_trace.md, docs/bench_bom.md, results/SUPERSEDED.md.
Branch `feature/v2-verification` on https://github.com/astralis12/FDIRSat.git (push is authorised).
When the owner is away: take the recommended default, stop only for hardware or credentials.

## Environment
- Windows laptop, `.venv` (Py 3.11, torch cu130, RTX 3080 16 GB). Keep awake + on power (ISS-054);
  max 2 GPU training + 2 DAgger jobs at once.
- Toolchain: Docker `fdir-toolchain:24.04` (CBMC 5.95.1). Never use the sudo/WSL password.
- DUT: Black Pill STM32F411CEU6, ST-Link, USART1 PA9/PA10 on COM5 at 460800 (flashing authorised).
  `cd firmware/pio/dut && pio run -e dut -t upload` (ref); `-e bench` (VTC6). Flight sources compile per
  env (shared_src.py, ISS-058).
- PDFs: `pdftotext -layout` on a copy in $TMP. Patch scripts: write them with the Write tool (heredocs
  with quotes break).

## State (19 Oct)
- Reference cell = LG INR21700-M50 5 Ah (ADR-0023); PINN out of scope (ADR-0022).
- SGO v3.2 (ADR-0024): invariant 7 band current limits in C + torch twin (`SgoState`), derated charger
  (nested envelopes, ISS-060), raise-only invariant 5 (ISS-061), out-of-envelope metric, PIL C6.
  CBMC quick runs: v3 ref + bench, contract + worst case, all properties pass (P1-P14).
  On target: `verify/hil_fdir.py` 0 mismatches with the CURRENT (v3.1-trained) tree.
- CRC state guard (ADR-0021): M6 gate passed on v3.1 (0 SDC / 3000) — to be re-run on v3.2 (pipeline S10).
- SEU: `results/seu_rates.json`, GEO check with D_mid L0 0.5 passes (1.17x / 0.41x); orbit runs still
  have D_mid at L0 1.0 (ISS-051, D_high main value unaffected).

## Next: run the pipeline
`bash train/pipeline_v32.sh` (12-16 h, resumable; RUNBOOK §6 has the step table, gates and what to do
with each output). Then: close ISS-059 with the numbers, rewrite `report/build_report.py` analysis(),
republish the artifact (https://claude.ai/artifact/XFE6kjf8PdJ2vfz6uAZWeT), update CLAUDE.md, push.

## Waiting on the owner
Bench Q (C/5, 4.20 -> 3.3 V) after fitting the 0.01 ohm shunt; parts (docs/bench_bom.md); EPS parts list
for MIL-HDBK-217F; ECSS clause texts; optional SPENVIS orbit reruns with D_mid L0 0.5; M1 literature audit.
