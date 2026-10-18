# Handoff — 18 Oct 2026 (for a new session: "Read HANDOFF.md and continue")

Read first: CLAUDE.md (incl. the new "Plan doc updates" section), RUNBOOK.md, docs/ISSUES.md (ISS-014..055),
docs/0017-0020, docs/bench_bom.md,
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
1b. **Done 18 Oct (ADR-0020, ISS-050)**: SGO v3.1 = invariant 6 (charge window 0..45 °C, B>=2, bit 6,
   release 2/42 °C, latched) + median-of-3 temperature (`fdir_temp_vote3`, glue `fdir_onboard_step3`, DUT cmd 'G').
   Configs `fdir_config_ref.h` (unchanged values) / `fdir_config_bench.h` (`pio run -e bench`; Q 3.0 Ah PLACEHOLDER,
   OV 4.25 V). CBMC P11a-g pass for v3 and v3bench (contract + worst case). Torch twin `sgo_v2(..., ct_latch, t_lo)`,
   `temp_vote3`. New plant hazard `charge_temp`. PIL case C5 (cold battery; C4 reserved for the capacity
   mis-estimate case on the bench). FMECA A5 (was missing) and A6 (single temperature-sensor fault) added.
2. **Done 18 Oct**: tree9 seed 6 emitted (6349 nodes, depth 12), `test_tree_bitexact` 0 mismatches, DUT flashed
   (firmware "fdir-dut 0.7 tree9 sgo-v3.1 multi7 seu ref"), `verify/hil_fdir.py` gate pass (tree, chain and 2000-case
   vote: 0 mismatches). **Next:** `python eval/residual_risk.py` (tree9 + v3) → `python eval/esa_adb_metrics.py`.
   Note ISS-053: these call the SGO twin without the latch (differs only inside the hysteresis band).
3/4. Ready, not yet run on the final tree: `python eval/compare_methods.py` (deployed 9-input, tree6,
   M1, M2, M6, N-of-M, expert, none; not-mitigable class; alarm precision). Adversary clusters are in the
   DAgger results (adversary ≈ fuzzing, complementary).
5. Ready, run on the final firmware: `python verify/fault_injection.py` (3000 gated + 400 noscrub, ~1.7 h,
   OpenOCD TCL), `python verify/timing_sweep.py` (~3 min; preliminary 100 MHz/3 WS 54.5 µs/step).
   Then full `run_cbmc.sh` (~90 min), which must exit 0.
6. **SPENVIS done 18 Oct**: `python eval/spenvis_seu.py` → `results/seu_rates.json` (6 runs from `data/spenvis/html`;
   GEO check pass: D_mid 0.79x heavy ion, 0.41x proton vs Roche 2009). `config/fmeca_rates.json` layer 3 now uses
   D_high per orbit (`run_per_orbit`); `eval/residual_risk_band.py` computes layer-3 rates per orbit. ISS-051 (D_mid
   ran with L0 1.0, inputs say 0.5), ISS-052 (4-9x higher at 600 km).
   Threshold table now has ref + bench configs, VTC6 15 A continuous, charge window, hysteresis, sensor vote.
   Earlier: `eval/threshold_table.py` with the VTC6 datasheet and the ECSS mapping (commit 37cbae8),
   `eval/spenvis_inputs.py` → `results/spenvis_inputs.json` (518.6/547.6/600.6 km, SSO, LTAN
   12:00/10:30/06:00, start 2027-01-01 ASSUMED). Then run `python eval/residual_risk_band.py`
   (needs compare_methods.json and fault_injection.json).
   **Still to apply (docs/owner_inputs_2026-10-18.md):**
   - MIL-HDBK-217F: put the per-part rows into `config/fmeca_rates.json` as a third rate layer, with the
     table number on each row. The owner's STM32 example (0.00216 /1e6 h) uses hermetic C2, gate-count C1
     and space-grade π_Q; the part is a commercial plastic 32-bit MCU. Compute both a commercial and a
     space-grade row and flag the difference; verify the tables against 217F Notice 2 if it is reachable.
   - Still pending from the owner: ECSS clause text check; bench Q (C/5, 4.20→3.3 V); EPS parts list for 217F;
     XJTU cell datasheet (ref charge window is ASSUMED 0..45 °C).
   - Hardware to buy/fit (docs/bench_bom.md): INA226 0.01 Ω shunt (0.1 Ω saturates at 0.82 A), electronic load,
     3x DS18B20, MOSFET switches, 8S BMS. ISS-049 closed: bench config, OV 4.25 V (charger CV fixed).
   JSC 20793 §4.4.3a requires per-cell voltage monitoring, so cell taps are mandatory (ISS-047).

## Finish
- ISSUES entries added 18 Oct: ISS-050..055 (incl. standby crash / pipeline race / VRAM thrash = ISS-054,
  adversary clusters = ISS-055).
- Rebuild the report: `python report/build_report.py` (sections in `report/sections_v3.py`; rewrite the
  `analysis()` text for tree9 + v3, since it still describes the 17 Oct state). Republish the artifact from the
  same file, URL https://claude.ai/artifact/XFE6kjf8PdJ2vfz6uAZWeT.
- Update the RUNBOOK 4.x rows, README and CLAUDE.md "Current next step"; commit; push `feature/v2-verification`.
