# Handoff — 18 Oct 2026 (for a new session: "Read HANDOFF.md and continue")

Read first: CLAUDE.md, RUNBOOK.md, docs/ISSUES.md (ISS-014..049), docs/0017-0019,
**docs/owner_inputs_2026-10-18.md** (cell, ECSS, MIL-HDBK-217F and SPENVIS inputs from the owner).
Branch `feature/v2-verification` on https://github.com/astralis12/FDIRSat.git (push is authorised).
Owner's ordered work list (18 Oct): 1 SGO v3 · 2 tree input history · 3 baselines M1/M2/M6,
coverage-vs-false-action curve, N-of-M ablation · 4 metric fixes · 5 fault injection ≥3000 +
100 MHz timing/SWaP sweep · 6 M2 rates, residual-risk band, threshold table.
When the owner is away: take the recommended default, stop only for hardware or credentials.

## Environment
- Windows laptop, `.venv` (Py 3.11, torch 2.13 cu130, RTX 3080 16 GB). Keep the laptop awake
  and on power (standby reset the NVIDIA driver, 18 Oct 05:45). Do not change Windows sleep
  settings; ask the owner, or use the app's keep-awake. Max 2 GPU training + 2 DAgger jobs at once.
- Toolchain: Docker image `fdir-toolchain:24.04` (CBMC 5.95.1, gcc 13). Run
  `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src" -w /src fdir-toolchain:24.04 bash verify/cbmc/run_cbmc.sh`.
  Never use the sudo/WSL password.
- DUT: Black Pill STM32F411CEU6, ST-Link, USART1 PA9/PA10 on COM5 at 460800 (flashing authorised).
  `cd firmware/pio/dut && pio run -e dut -t upload`; envs f32..f100, f100_noart, f16_noart for the sweep.
- Data (not committed): `data/defender9` (9-input, current), `data/defender` (6-input, old),
  `data/dfn`, junctions `data/xjtu_sps`, `data/nasa_rw`.
- PDFs: no poppler for Read; use `pdftotext -layout` (Git Bash, /mingw64/bin) on a copy in $TMP.

## Status per item
1. **Done**: SGO v3 (`fdir_sgo_v3.c/.h`, bit 5, `FDIR_VCELL_OV_MAX`), CBMC P10 proved, DUT flashed,
   BCR-short PIL rerun (`results/hil_cases.json` keys `C2_sgo_v3_bcr_short`, `C1_sgo_v3`),
   `results/residual_risk_sgo_v3.json` (6-input tree + v3). Old v2 results in `results/tree6/`.
2. **Training done 18 Oct 08:33**: 10 defenders, 10 DAgger, gate CI 0.42 pp, deploy seed 6
   (`results/dagger_summary.json`). Features proved (0/454) and bit-exact. **Next, in order:**
   `python codegen/emit_tree.py` (seed 6) → `python verify/test_tree_bitexact.py` → rebuild/flash DUT →
   `python verify/hil_fdir.py` → `python eval/residual_risk.py` (tree9 + v3) → `python eval/esa_adb_metrics.py`.
3/4. Ready, not yet run on the final tree: `python eval/compare_methods.py` (deployed 9-input, tree6,
   M1, M2, M6, N-of-M, expert, none; not-mitigable class; alarm precision). Adversary clusters are in the
   DAgger results (adversary ≈ fuzzing, complementary).
5. Ready, run on the final firmware: `python verify/fault_injection.py` (3000 gated + 400 noscrub, ~1.7 h,
   OpenOCD TCL), `python verify/timing_sweep.py` (~3 min; preliminary 100 MHz/3 WS 54.5 µs/step).
   Then full `run_cbmc.sh` (~90 min), which must exit 0.
6. Done: `eval/threshold_table.py` with the VTC6 datasheet and the ECSS mapping (commit 37cbae8),
   `eval/spenvis_inputs.py` → `results/spenvis_inputs.json` (518.6/547.6/600.6 km, SSO, LTAN
   12:00/10:30/06:00, start 2027-01-01 ASSUMED). Then run `python eval/residual_risk_band.py`
   (needs compare_methods.json and fault_injection.json).
   **New owner inputs to apply (docs/owner_inputs_2026-10-18.md):**
   - VTC6: 15 A continuous without a thermal cut-off (use it for the overcurrent margin),
     charge temperature 0..45 °C, discharge -20..60 °C. Add these to `CELL` in threshold_table.py and re-run it.
   - ISS-050 (new, to log): `FDIR_TBAT_CRITICAL_MAX` 60 °C is above the 45 °C charge limit, so there is no
     charge-temperature protection. Draft an ADR for a charge-temperature invariant (and a < 0 °C charge
     inhibit). Firmware change only after owner OK, then CBMC + `run_cbmc.sh`.
   - MIL-HDBK-217F: put the per-part rows into `config/fmeca_rates.json` as a third rate layer, with the
     table number on each row. The owner's STM32 example (0.00216 /1e6 h) uses hermetic C2, gate-count C1
     and space-grade π_Q; the part is a commercial plastic 32-bit MCU. Compute both a commercial and a
     space-grade row and flag the difference; verify the tables against 217F Notice 2 if it is reachable.
   - Still pending from the owner: SPENVIS SEU rate per orbit; ECSS clause text check.
   - Owner decisions open: ISS-049 (firmware 2.0 Ah vs bench 3.0 Ah VTC6; the 4.2 ± 0.05 V charge tolerance
     sits on the 4.20 V invariant; proposed charger CV 4.15 V/cell); ISS-050 above.
   JSC 20793 §4.4.3a requires per-cell voltage monitoring, so cell taps are mandatory (ISS-047).

## Finish
- Add ISSUES entries: overnight standby crash, pipeline race (data read while written), VRAM thrash,
  adversary-cluster finding, ISS-050.
- Rebuild the report: `python report/build_report.py` (sections in `report/sections_v3.py`; rewrite the
  `analysis()` text for tree9 + v3, since it still describes the 17 Oct state). Republish the artifact from the
  same file, URL https://claude.ai/artifact/XFE6kjf8PdJ2vfz6uAZWeT.
- Update the RUNBOOK 4.x rows, README and CLAUDE.md "Current next step"; commit; push `feature/v2-verification`.
