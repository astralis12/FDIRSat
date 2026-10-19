#!/usr/bin/env bash
# SGO v3.2 batch (ADR-0024): retrain + redistil on the derated plant, re-prove, re-verify, re-evaluate.
#
# Resumable: each step writes results/pipeline_v32/<step>.done and is skipped when that exists.
# Run all:            bash train/pipeline_v32.sh
# From a step:        bash train/pipeline_v32.sh --from S5
# Only some steps:    bash train/pipeline_v32.sh --only "S9 S10"
# Logs:               results/pipeline_v32/logs/<step>.log   (status per step in .../status.txt)
# Needs: .venv (CUDA), Docker image fdir-toolchain:24.04, DUT on COM5 + ST-Link for S8-S12.
# Keep the laptop awake and on power (ISS-054). Every gate that fails stops the pipeline.
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="$ROOT/.venv/Scripts/python.exe"; [ -x "$PY" ] || PY=python
OUT="$ROOT/results/pipeline_v32"; LOGS="$OUT/logs"; mkdir -p "$LOGS"
FROM=""; ONLY=""
while [ $# -gt 0 ]; do case "$1" in --from) FROM="$2"; shift 2;; --only) ONLY="$2"; shift 2;; *) echo "unknown arg $1"; exit 2;; esac; done
STEPS="S0 S1 S2 S3 S4 S5 S6 S7 S8 S9 S10 S11 S12 S13 S14"
started=""; [ -z "$FROM" ] && started=1

log() { echo "$(date '+%F %T') $*" | tee -a "$OUT/status.txt"; }
gate() {  # gate <json> <python expression on d> <label>
  "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if ($2) else 1)" "$1" \
    && log "GATE ok   $3" || { log "GATE FAIL $3 ($1)"; exit 1; }
}
retry() { "$@" || { log "retry: $*"; sleep 20; "$@"; }; }

S0() {  # archive the v3.1 results and checkpoints they supersede (results/SUPERSEDED.md)
  "$PY" scripts/supersede_results.py --tag v3.1 --apply
}
S1() {  # training data on the derated plant (sim/env_v2.py charge_derate=True, T_CRIT 55)
  retry "$PY" train/gen_data.py --seeds 0 1 2 3 --out data/defender_v32 &&
  retry "$PY" train/gen_data.py --seeds 100 --split val --out data/defender_v32
}
S2() {  # 10 defenders, 2 at a time (16 GB VRAM)
  printf '%s\n' 0 1 2 3 4 5 6 7 8 9 | xargs -P 2 -I{} bash -c \
    '[ -f results/defender/s{}.json ] || "'"$PY"'" train/train_defender.py --seed {} --data data/defender_v32 > "'"$LOGS"'/S2_s{}.log" 2>&1 || exit 255'
}
S3() {  # closed-loop severity-weighted DAgger, 10 seeds, SGO v3.2 with state in the loop
  printf '%s\n' 0 1 2 3 4 5 6 7 8 9 | xargs -P 2 -I{} bash -c \
    '[ -f results/dagger/sw_dagger_s{}.json ] || "'"$PY"'" train/dagger_adversary.py --seed {} --mode sw_dagger > "'"$LOGS"'/S3_s{}.log" 2>&1 || exit 255'
}
S4() {  # ablations (plan core: BC and plain DAgger), 3 seeds each
  for m in dagger bc; do printf '%s\n' 0 1 2 | xargs -P 2 -I{} bash -c \
    '[ -f results/dagger/'"$m"'_s{}.json ] || "'"$PY"'" train/dagger_adversary.py --seed {} --mode '"$m"' > "'"$LOGS"'/S4_'"$m"'_s{}.log" 2>&1 || exit 255' || return 1; done
}
S5() {  # gates 4.5 / 4.6 and the deploy seed
  "$PY" train/summarise.py &&
  gate results/dagger_summary.json "d['gate']['pass']" "closed-loop fidelity CI <= 1.5 pp"
}
S6() {  # tree -> C, bit-exact against sklearn and the GPU tree
  "$PY" codegen/emit_tree.py && "$PY" verify/test_tree_bitexact.py &&
  gate results/tree_bitexact.json "d['gate']['pass']" "tree bit-exact"
}
S7() {  # full re-proof: SGO v1-v3.2 (ref + bench), features, tree, tree_any, EKF, guard (~2-3 h)
  MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/src" -w /src fdir-toolchain:24.04 bash verify/cbmc/run_cbmc.sh
}
S8() {  # flash the ref build and check it on target (tree, chain, vote + latch + invariant 7)
  (cd firmware/pio/dut && pio run -e bench && pio run -e dut -t upload) &&
  "$PY" verify/hil_fdir.py && gate results/hil_fdir.json "d['gate']['pass']" "on-target bit-exact (hil_fdir)"
}
S9() {  # processor-in-the-loop cases C1, C2, C3, C5, C6 (C6 = cold charge, ADR-0024)
  "$PY" hil/run_case.py --case all --repeat 5 --tag _v32
}
S10() { # SEU campaign on the final firmware (M6 gate: 0 SDC in 3000)
  "$PY" verify/fault_injection.py && gate results/fault_injection.json "d['gate']['pass']" "fault injection 0 SDC / 3000"
}
S11() { # timing / SWaP sweep (reflashes; S12 restores the ref build)
  "$PY" verify/timing_sweep.py
}
S12() { # restore the ref build on the DUT
  (cd firmware/pio/dut && pio run -e dut -t upload)
}
S13() { # evaluation on the retrained tree with SGO v3.2 (state per episode, out-of-envelope time)
  "$PY" eval/compare_methods.py && "$PY" eval/residual_risk.py --sgo v3.2 && "$PY" eval/esa_adb_metrics.py &&
  "$PY" eval/residual_risk_band.py && "$PY" eval/threshold_table.py && "$PY" eval/spenvis_seu.py
}
S14() { # report (rewrite report/build_report.py analysis() text first if numbers moved)
  "$PY" report/build_report.py
}

for s in $STEPS; do
  [ -n "$FROM" ] && [ "$s" = "$FROM" ] && started=1
  [ -z "$started" ] && continue
  if [ -n "$ONLY" ] && ! echo " $ONLY " | grep -q " $s "; then continue; fi
  if [ -f "$OUT/$s.done" ]; then log "skip $s (done)"; continue; fi
  log "start $s"
  if "$s" > "$LOGS/$s.log" 2>&1; then touch "$OUT/$s.done"; log "done  $s"; else log "FAIL  $s (see $LOGS/$s.log)"; exit 1; fi
done
log "pipeline v3.2 finished"
