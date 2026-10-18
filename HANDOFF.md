# Handoff — 18 Oct 2026 (session context full)

Read CLAUDE.md, RUNBOOK.md, docs/ISSUES.md (ISS-014..048), docs/0017-0019 first.
Owner's ordered work list (18 Oct): 1 SGO v3 · 2 tree input history · 3 baselines M1/M2/M6,
coverage-vs-false-action curve, N-of-M ablation · 4 metric fixes · 5 fault injection ≥3000 +
100 MHz timing/SWaP sweep · 6 M2 rates, residual-risk band, threshold table.

## Environment
- Windows laptop, `.venv` (Py 3.11, torch 2.13 cu130, RTX 3080 16 GB). Keep the laptop awake
  and on power: standby resets the NVIDIA driver and kills CUDA jobs (seen 18 Oct 05:45).
  Max 2 GPU training jobs + 2 DAgger jobs at once (7 jobs filled VRAM and thrashed).
- Toolchain: Docker image `fdir-toolchain:24.04` (CBMC 5.95.1, gcc 13). Run
  `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src" -w /src fdir-toolchain:24.04 bash verify/cbmc/run_cbmc.sh`.
  Do not use sudo/WSL password.
- DUT: Black Pill STM32F411CEU6, ST-Link, USART1 PA9/PA10 on COM5 at 460800.
  `cd firmware/pio/dut && pio run -e dut -t upload`; envs f32..f100, f100_noart, f16_noart for the sweep.
- Data (not committed): `data/defender9` (9-input, current), `data/defender` (6-input, old),
  `data/dfn`, junctions `data/xjtu_sps`, `data/nasa_rw`.

## Status per item
1. **Done**: SGO v3 (`fdir_sgo_v3.c/.h`, bit 5, `FDIR_VCELL_OV_MAX`), CBMC P10 proved, DUT flashed,
   BCR-short PIL rerun (`results/hil_cases.json` keys `C2_sgo_v3_bcr_short`, `C1_sgo_v3`),
   `results/residual_risk_sgo_v3.json` (6-input tree + v3). Old v2 results in `results/tree6/`.
2. **Training done 18 Oct 08:33** (10 defenders, 10 DAgger; gate CI 0.42 pp, deploy seed 6 — see results/dagger_summary.json). Next: features (`fdir_features.c`, `sim/features.py`) proved (0/454) and bit-exact
   (`results/features_bitexact.json`). 10 defenders done (`results/defender/`); DAgger seeds 0-5 done,
   6-9 were running via `train/pipeline9b.sh` (copy in repo) (resumable: rerun it, it skips finished seeds;
   ends with `train/summarise.py`). Then:
   `python codegen/emit_tree.py` → `python verify/test_tree_bitexact.py` → rebuild/flash DUT →
   `python verify/hil_fdir.py` → `python eval/residual_risk.py` (writes results/residual_risk.json, tree9+v3)
   → `python eval/esa_adb_metrics.py`.
3/4. Script ready, not yet run on final tree: `python eval/compare_methods.py` (deployed=9-input,
   tree6, M1, M2, M6, N-of-M filters, expert, none; not-mitigable class; alarm precision).
   Adversary distinct-cluster counting is in DAgger results (seeds 0-1: adversary ≈ fuzzing, complementary).
5. Ready, run on final firmware: `python verify/fault_injection.py` (3000 gated + 400 noscrub, ~1.7 h,
   uses OpenOCD TCL; DUT tree tables in RAM + CRC scrub, IWDG, HardFault reset),
   `python verify/timing_sweep.py` (~3 min; preliminary: 100 MHz/3 WS 54.5 µs per step).
   Then full `run_cbmc.sh` (~90 min: SGO v1-v3, features, tree, tree_any, EKF) — must exit 0.
   The any-tables tree proof passed A1/A4 before the CRC rotate fix (ISS-046).
6. `config/fmeca_rates.json`, `python eval/residual_risk_band.py` (needs compare_methods.json and
   fault_injection.json), `python eval/threshold_table.py` (done). 18 Oct: owner supplied the cell (Sony US18650VTC6 Rev 0.1) and the ECSS clause mapping; both are traced
   in `results/threshold_table.json`. `python eval/spenvis_inputs.py` writes the SPENVIS orbit inputs. ISS-049:
   bench cell 3.0 Ah vs firmware 2.0 Ah, and the 4.2 +/- 0.05 V charge tolerance sits on the 4.20 V invariant —
   owner decision. PENDING owner: MIL-HDBK-217F per-part values, SPENVIS SEU rate, VTC6 charge temperature and
   max continuous current. JSC 20793 §4.4.3a requires per-cell voltage monitoring → cell taps mandatory (ISS-047).

## Finish
- Add ISSUES entries for: overnight standby crash and pipeline race (data files read while written),
  VRAM thrash, adversary-cluster finding.
- Rebuild report: `python report/build_report.py` (sections in `report/sections_v3.py`; rewrite the
  `analysis()` text in build_report.py for tree9+v3 — it still describes the 17 Oct state);
  republish the artifact (same file path; URL https://claude.ai/artifact/XFE6kjf8PdJ2vfz6uAZWeT).
- Update RUNBOOK 4.x rows, README, CLAUDE.md "Current next step"; commit; push `feature/v2-verification`.
