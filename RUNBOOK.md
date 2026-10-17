# Runbook — commands, in order

Status marks: **[ready]** = script exists in this bundle and was run on 17 Oct;
**[next]** = being written next (software workstream); **[you]** = hardware or data step on your side.

## 0. One-time setup on your laptop (CachyOS)

CBMC and Vivado are easiest on Ubuntu, so run the toolchain in an Ubuntu 24.04
container through distrobox. Ubuntu 24.04 ships CBMC 5.95.1, the version used here.

```bash
sudo pacman -S --needed distrobox podman git
distrobox create --name fdir --image ubuntu:24.04
distrobox enter fdir
# inside the container:
sudo apt update && sudo apt install -y cbmc gcc python3-venv python3-pip git
cd ~/XJTU-SPS                     # your repo
git switch -c feature/v2-verification
unzip ~/Downloads/fdir-v2-2026-10-17b.zip -d /tmp && cp -r /tmp/fdir-v2/* .
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt torch scikit-learn pandas wandb rliable==1.2.0 huggingface_hub
```

**Windows laptop (no sudo in WSL): Docker toolchain image instead of distrobox.**
Same Ubuntu 24.04 / CBMC 5.95.1 / gcc 13.3; root inside the container, so no password.
Built once on 17 Oct (~180 MB download); steps 2 and 4.2 then run through it:

```bash
docker run --name fdir-tc ubuntu:24.04 bash -c "apt-get update && apt-get install -y cbmc gcc"
docker commit fdir-tc fdir-toolchain:24.04 && docker rm fdir-tc
MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src" -w /src fdir-toolchain:24.04 bash verify/cbmc/run_cbmc.sh
```

GPU training (PyTorch + CUDA) can run in the same container if the NVIDIA driver is
visible (`nvidia-smi` inside it); otherwise run training on the host venv.

## 1. Data **[you]**

```bash
# XJTU-SPS, all four subsets (~5.4 GB)
huggingface-cli download DiYi1999/XJTU-SPS --repo-type dataset --local-dir data/xjtu_sps
# NASA RW17-RW20 (already sent): put the four .mat files in data/nasa_rw/
mkdir -p data/nasa_rw && cp /path/to/RW1{7,8,9}.mat /path/to/RW20.mat data/nasa_rw/
```

Column ranges (settled ISS-020 on 17 Oct: `U_BAT*` is pack-level, 4S):
`python sim/xjtu_sps.py --check` → `results/xjtu_sps_ranges.json` (RUNBOOK 4.3).

On the Windows laptop the data already sit in `RAMS/DATASET` and
`RAMS/5.RW_Skewed_High_Room_Temp_DataSet_2Post/.../data/Matlab`; `data/xjtu_sps` and
`data/nasa_rw` are directory junctions to them (`New-Item -ItemType Junction`), nothing copied.

## 2. Verification of the safety layer **[ready]**

```bash
bash verify/cbmc/run_cbmc.sh          # ~1 s for the SGO, ~50 min for the EKF proof; exit 0 only if SGO v2 and the EKF prove fully
cat verify/cbmc/logs/v2_worstcase.properties.txt
gcc -std=c99 -Wall -Wextra -Werror -Ifirmware/include -c firmware/src/fdir_sgo_v2.c
gcc -std=c99 -Wall -Wextra -Werror -Wdouble-promotion -ffp-contract=off -Ifirmware/include -c firmware/src/soc_ekf.c
```

## 3. Battery plant and estimator **[ready]**

```bash
python sim/dfn_plant.py --out data/dfn                 # DFN truth data, ~20 s (~1 min on Windows)
# validation profiles for 4.1: seeds 1-19 except 5 and 16 (they stop at the 4.2 V cut-off, ISS-024).
# Each call also rewrites the (identical) OCV and pulse files; ~1 min per seed, runs in parallel.
for s in 1 2 3 4 6 7 8 9 10 11 12 13 14 15 17 18 19; do python sim/dfn_plant.py --out data/dfn --seed $s & done; wait
cd sim && python eval_ekf.py --data ../data/dfn --out ../results/ekf_eval.json && cd ..
python sim/nasa_rw.py                                  # NASA summary -> results/nasa_rw_summary.json
```

## 4. Software deliverables (in this order) **[ready]**

```bash
python sim/fit_ecm_scheduled.py      # 4.1, ~11 min on 4+ cores -> results/ecm_scheduled.json
python codegen/emit_ekf_tables.py    # 4.2: tables -> firmware/include/soc_ekf_tables.h (then re-run step 2)
python verify/test_ekf_bitexact.py   # 4.2: C vs Python, float + double builds -> results/ekf_bitexact.json
python train/gen_data.py --seeds 0 1 2 3 && python train/gen_data.py --seeds 100 --split val   # 4.5 data (~11 min each)
for s in 0 1 2 3 4 5 6 7 8 9; do python train/train_defender.py --seed $s; done                # 4.5 (~12 min/seed)
for s in 0 1 2 3 4 5 6 7 8 9; do python train/dagger_adversary.py --seed $s --mode sw_dagger; done   # 4.6 (~40 min/seed)
python train/summarise.py            # 4.5/4.6 gates -> results/defender_summary.json, results/dagger_summary.json
python codegen/emit_tree.py && python verify/test_tree_bitexact.py   # 4.7, then step 2 (run_cbmc.sh)
python eval/residual_risk.py && python eval/esa_adb_metrics.py       # 4.8
python sim/ekf_sigma_cv.py           # ISS-025: EKF noise/sigma by leave-one-seed-out, ~4 min -> results/ekf_sigma_cv.json
                                     # needs validation seeds 14,15,17,18,19 as well (dfn_plant.py --seed N)
```

| Step | Script | What it does | Gate |
| --- | --- | --- | --- |
| 4.1 **[ready]** | `sim/fit_ecm_scheduled.py` | N4: ECM parameters per SoC level from NASA pulsed-load blocks and DFN; rest-corrected OCV; EKF r_v + sigma calibrated on pooled profiles. Temperature axis not done (NASA blocks are all near room temperature; needs DFN at other temperatures) | Test-seed 2σ coverage ≥ 0.90 — **PASS** (`results/ecm_scheduled.json` → `dfn.gate`); residual risk ISS-025 |
| 4.2 **[ready]** | `firmware/src/soc_ekf.c` + `verify/test_ekf_bitexact.py` | EKF in C (float flight build, double check build); tables generated by `codegen/emit_ekf_tables.py`; CBMC runtime-safety proof `verify/cbmc/harness_ekf.c` | 0 mismatches — **PASS** (`results/ekf_bitexact.json`); CBMC success — **PASS** (`verify/cbmc/logs/ekf.*`) |
| 4.3 **[ready]** | `sim/xjtu_sps.py`, `config/fmeca_xjtu_sps.json` | Loaders for FL/FD, AD, F/R, MR; per-unit conversion; leakage-safe split (ISS-028); FMECA label map (severities PROPOSED for M2) | Column ranges verified — **PASS** (`python sim/xjtu_sps.py --check` → `results/xjtu_sps_ranges.json` → `gate`); open: ISS-029, ISS-030, ISS-031 |
| 4.4 **[ready]** | `sim/env_v2.py`, `train/graph_rollout.py` | Batched GPU env: 8S per-unit plant, EKF in the loop, 6 inputs, fault library from FMECA; CUDA-graph rollouts | Smoke run, shapes asserted — **PASS** (`results/env_v2_smoke.json`) |
| 4.5 **[ready]** | `train/gen_data.py`, `train/train_defender.py --seed N` | Supervised severity-weighted defender, W&B (offline) logging, 10 seeds | All seeds converge — **PASS** (`results/defender_summary.json`) |
| 4.6 **[ready]** | `train/dagger_adversary.py --mode {sw_dagger,dagger,bc}`, `train/summarise.py` | Severity-weighted DAgger + adversarial stress test; DAgger and BC ablations | Fidelity CI ≤ 1.5 pp — **PASS** (`results/dagger_summary.json`) |
| 4.7 **[ready]** | `codegen/emit_tree.py`, `verify/test_tree_bitexact.py`, `verify/cbmc/harness_tree.c` | Tree → C (6 inputs), glue `fdir_onboard.c`, then step 2 on the generated file | CBMC success — **PASS** (`verify/cbmc/logs/tree.*`); 0 mismatches (`results/tree_bitexact.json`) |
| 4.8 **[ready]** | `eval/esa_adb_metrics.py`, `eval/residual_risk.py` | Event-wise F0.5, coverage per FMECA mode, residual risk per orbit | Tables filled — `results/esa_adb_metrics.json`, `results/residual_risk.json`; findings ISS-040..042, ADR-0018 |

## 5. Hardware **[you]**, then HIL **[next]**

1. Buy the budget parts (plan doc, "Real-pack testbed").
2. Wire DAQ STM32 #2: INA226 (low side), ADS1115 ×2 on taps (optional), DS18B20 ×3, relay module.
3. Calibrate: each tap divider and the INA226 against the multimeter at 3 points; log in `hw/calibration.csv`.
4. Characterise the pack: C/20-equivalent slow discharge for OCV, then pulsed load at 10 SoC levels.
5. Flash DUT STM32 #1 with SGO v2 + EKF + tree: `cd firmware/pio/dut && pio run -t upload` **[ready, flashed 17 Oct]**
   (Black Pill STM32F411CEU6, ST-Link; USART1 PA9/PA10 at 460800 baud on COM5). On-target checks:
   `python verify/hil_ekf.py`, `python verify/hil_fdir.py`. DAQ logger firmware **[next]** (needs board #2).
6. Run cases C1 → C2 → C3, ≥ 5 repeats each: `python hil/run_case.py --case all --repeat 5` **[ready as processor-in-the-loop]**:
   the plant runs on the laptop, the DUT closes the loop (results/hil_cases.json). With the pack and DAQ the same cases become HIL.
7. Report: `python report/build_report.py` → `report/fdirsat_results.html` (every number read from results/).
