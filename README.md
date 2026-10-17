# fdir-v2 — software workstream (started 17 Oct 2026)

New code for the single-paper plan, kept separate from the existing XJTU-SPS repo
until it is merged. Copy these folders into the repo on a `feature/v2-verification` branch.

```
firmware/include/fdir_sgo.h        original header (unchanged copy)
firmware/include/fdir_config.h     28 V / 8S reference thresholds (PROPOSED, trace in M2)
firmware/src/fdir_sgo.c            original SGO v1 (unchanged copy, for the proof baseline)
firmware/src/fdir_sgo_v2.c         SGO v2: output sanitisation + plausibility gate (ADR-0017)
verify/cbmc/harness_sgo.c          CBMC properties P1–P9
verify/cbmc/run_cbmc.sh            runs v1 and v2 x {contract, worst-case} tree; logs + hashes
verify/cbmc/logs/                  results of the 17 Oct run
sim/dfn_plant.py                   PyBaMM DFN truth plant (stand-in: Chen2020) -> data/dfn
sim/ecm_ekf.py                     2RC ECM, bounded identification, SoC EKF, P(SoC<th)
sim/eval_ekf.py                    fit + EKF evaluation -> results/ekf_eval.json
sim/eval_ekf_calibration.py        uncertainty calibration test -> results/ekf_calibration.json
sim/fit_ecm_scheduled.py           N4 SoC-scheduled ECM + EKF recalibration -> results/ecm_scheduled.json
sim/ekf_sigma_cv.py                EKF noise + sigma inflation by leave-one-seed-out -> results/ekf_sigma_cv.json
firmware/include/soc_ekf.h         SoC EKF interface (float flight build, -DSOC_EKF_DOUBLE check build)
firmware/include/soc_ekf_tables.h  GENERATED tables (codegen/emit_ekf_tables.py), do not edit
firmware/src/soc_ekf.c             SoC EKF with input / covariance / state guards
codegen/emit_ekf_tables.py         results JSON -> soc_ekf_tables.h
verify/ekf_driver.c                host driver for the bit-exact test
verify/test_ekf_bitexact.py        C vs Python EKF -> results/ekf_bitexact.json
verify/cbmc/harness_ekf.c          CBMC properties E2-E7 (one step, any state in the guard band)
verify/cbmc/harness_tree.c         CBMC properties T1-T3 for the generated tree (= the SGO's tree contract)
firmware/src/fdir_tree.c           GENERATED decision tree (codegen/emit_tree.py), do not edit
firmware/src/fdir_onboard.c        glue: 6 tree inputs -> HealthTelemetry -> SGO v2
firmware/pio/dut/                  PlatformIO DUT project (Black Pill F411, ST-Link, USART1 PA9/PA10)
sim/env_v2.py                      batched GPU EPS environment (4.4)
train/                             data generation, defender, DAgger + adversary, CUDA-graph rollouts, summaries
eval/                              residual risk per orbit, XJTU-SPS event-wise metrics (4.8)
hil/run_case.py                    processor-in-the-loop cases C1-C3 on the DUT
report/build_report.py             results report generator
sim/xjtu_sps.py                    XJTU-SPS loaders, per-unit, leakage-safe split; --check -> results/xjtu_sps_ranges.json
config/fmeca_xjtu_sps.json         FMECA label map (fault ids 0-17, work modes, artefact modes), PROPOSED
docs/ISSUES.md                     issue log ISS-014..026
docs/0017-*.md                     ADR draft for SGO v2
```

## Reproduce

```bash
pip install -r requirements.txt          # Python 3.11+; tested on 3.13
sudo apt install cbmc gcc                # CBMC 5.95.1 used here
bash verify/cbmc/run_cbmc.sh             # ~1 s; exit 0 iff v2 proves fully
python sim/dfn_plant.py --out data/dfn   # ~20 s
cd sim && python eval_ekf.py --data ../data/dfn --out ../results/ekf_eval.json
python eval_ekf_calibration.py           # needs valid seeds 1, 2 (see below)
```

Seeds 1 and 2 of the validation profile were generated with `random_walk_profile(seed=1|2)`
and `_run_current(..., soc0=0.85)`; a CLI flag for this is a TODO.

## Results so far (17 Oct)

| Item | Result |
| --- | --- |
| SGO v1, contract tree | Invariants P3–P6 proven for all floats; P7 (non-finite) and P8 (impossible SoC) FAIL |
| SGO v1, worst-case tree | Additionally P1/P2 FAIL: corrupted tree bytes reach actuators |
| SGO v2, both tree modes | All 16 properties proven, no runtime-check failures |
| Strict compile | v1 and v2: zero warnings, `-std=c99 -Wall -Wextra -Werror` |
| ECM fit (DFN pulses) | 9.1 mV RMSE; 10.8 mV open-loop on an unseen 3 h profile |
| EKF, 20 % initial SoC error | < 2 % within 51 s; settled RMSE 0.28–0.79 %, max 1.6 % across 3 profiles |
| EKF uncertainty | Overconfident; constant inflation does not transfer (ISS-018) |
| Environment (4.4) | Smoke gate passed (`results/env_v2_smoke.json`); CUDA-graph rollouts checked equal to eager |
| Defender (4.5) | 10 seeds, all converged (`results/defender_summary.json`) |
| Distillation (4.6) | Severity-weighted DAgger meets the fidelity CI gate; BC and DAgger ablations (`results/dagger_summary.json`) |
| Tree in C (4.7) | CBMC proof, 0 mismatches vs sklearn (`results/tree_bitexact.json`) and on the STM32F411 (`results/hil_fdir.json`) |
| Coverage and risk (4.8) | `results/residual_risk.json`, `results/esa_adb_metrics.json`; overvoltage gap and ADR-0018 |
| Processor-in-the-loop | C1–C3 on the STM32F411 (`results/hil_cases.json`) |
| Report | `python report/build_report.py` → `report/fdirsat_results.html` |
| XJTU-SPS loaders (4.3) | Consistency gate passed (`results/xjtu_sps_ranges.json`); leakage-safe split, temperature confound and unknown capacity logged (ISS-028 to ISS-031) |
| EKF in C (4.2) | Bit-exact test: 0 mismatches, 0 guard activations (`results/ekf_bitexact.json`); CBMC: all EKF properties proven, no runtime-check failures (`verify/cbmc/logs/ekf.*`) |
| N4 scheduled ECM (4.1) | Gate met on untouched test profiles; see `results/ecm_scheduled.json` (`dfn.gate`, `dfn.variants`); one calibration profile still under-covers (ISS-025) |

## Next software steps

1. ~~SoC-dependent ECM parameters (N4) → recalibrate EKF sigma on pooled profiles~~ done 17 Oct (`sim/fit_ecm_scheduled.py`).
2. EKF in C (`soc_ekf.c`) + bit-exactness test against `ecm_ekf.py` + CBMC runtime-safety proof.
3. XJTU and NASA PCoE loaders (downloads run on your machine; this workspace cannot reach Zenodo).
4. 8S environment update in `batched_eps_env.py`: EKF in the loop, new fault classes, 28 V thresholds.
5. Severity-weighted DAgger + adversarial stress test; W&B logging; 10-seed runner.
