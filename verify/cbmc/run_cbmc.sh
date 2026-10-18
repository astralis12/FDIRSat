#!/usr/bin/env bash
# Proves SGO properties with CBMC for v1 (original), v2 and v3 (ADR-0018, ISS-050; ref and bench configs),
# each against a contract-respecting tree and a worst-case (any byte) tree,
# and runtime safety of the SoC EKF (float build, generated tables).
# Any "no body for function" warning fails the run (ISS-016).
# Logs + SHA-256 of every input go to verify/cbmc/logs/.
# Usage: bash verify/cbmc/run_cbmc.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INC="$ROOT/firmware/include"
H="$ROOT/verify/cbmc/harness_sgo.c"
LOG="$ROOT/verify/cbmc/logs"
mkdir -p "$LOG"
cbmc --version > "$LOG/cbmc_version.txt"

COMMON=(--bounds-check --pointer-check --signed-overflow-check
        --unsigned-overflow-check --float-overflow-check --nan-check
        --div-by-zero-check --undefined-shift-check --trace)

declare -A SRC=( [v1]="$ROOT/firmware/src/fdir_sgo.c" [v2]="$ROOT/firmware/src/fdir_sgo_v2.c" [v3]="$ROOT/firmware/src/fdir_sgo_v3.c" [v3bench]="$ROOT/firmware/src/fdir_sgo_v3.c" )
HE="$ROOT/verify/cbmc/harness_ekf.c"
SE="$ROOT/firmware/src/soc_ekf.c"
HT="$ROOT/verify/cbmc/harness_tree.c"
ST="$ROOT/firmware/src/fdir_tree.c"
HF="$ROOT/verify/cbmc/harness_features.c"
SF="$ROOT/firmware/src/fdir_features.c"
sha256sum "$H" "$HE" "$HT" "$INC"/*.h "${SRC[v1]}" "${SRC[v2]}" "${SRC[v3]}" "$SE" "$ST" "$HF" "$SF" "$ROOT/verify/cbmc/harness_tree_any.c" > "$LOG/hashes.txt"

status=0
# v3 = reference configuration (fdir_config_ref.h), v3bench = bench (fdir_config_bench.h)
for VER in v1 v2 v3 v3bench; do
  for MODE in contract worstcase; do
    DEFS=()
    [[ "$MODE" == contract ]] && DEFS+=(-DTREE_CONTRACT)
    [[ "$VER"  == v2 ]]       && DEFS+=(-DSGO_V2)
    [[ "$VER"  == v3 ]]       && DEFS+=(-DSGO_V3)
    [[ "$VER"  == v3bench ]]  && DEFS+=(-DSGO_V3 -DFDIR_CONFIG_BENCH)
    TAG="${VER}_${MODE}"
    cbmc "$H" "${SRC[$VER]}" -I "$INC" "${DEFS[@]}" "${COMMON[@]}" \
         > "$LOG/$TAG.log" 2>&1 || true
    grep -E '^\[main\.assertion.*(SUCCESS|FAILURE)$' "$LOG/$TAG.log" > "$LOG/$TAG.properties.txt" || true
    grep -E 'FAILURE$' "$LOG/$TAG.log" | grep -v '^\[main\.assertion' > "$LOG/$TAG.runtime_failures.txt" || true
    verdict=$(grep -E '^VERIFICATION (SUCCESSFUL|FAILED)' "$LOG/$TAG.log" | tail -1)
    nfail=$(grep -c 'FAILURE$' "$LOG/$TAG.properties.txt" || true)
    printf '%-14s %-26s failed properties: %s\n' "$TAG" "$verdict" "$nfail"
    [[ "$VER" != v1 && "$verdict" != "VERIFICATION SUCCESSFUL" ]] && status=1
    if grep -qE "no body for function|implicit function declaration" "$LOG/$TAG.log"; then echo "$TAG: missing body or implicit declaration (ISS-016)"; status=1; fi
  done
done

# Tree input history (ADR-0019): ring-buffer bounds, inductive invariant, finite outputs
TAG=features
cbmc "$HF" "$SF" -I "$INC" --unwind 62 --unwinding-assertions "${COMMON[@]}" > "$LOG/$TAG.log" 2>&1 || true
grep -E '\.assertion\.[0-9]+\].*(SUCCESS|FAILURE)$' "$LOG/$TAG.log" > "$LOG/$TAG.properties.txt" || true
grep -E 'FAILURE$' "$LOG/$TAG.log" | grep -v '\.assertion\.' > "$LOG/$TAG.runtime_failures.txt" || true
verdict=$(grep -E '^VERIFICATION (SUCCESSFUL|FAILED)' "$LOG/$TAG.log" | tail -1)
nfail=$(grep -c 'FAILURE$' "$LOG/$TAG.properties.txt" || true)
printf '%-14s %-26s failed properties: %s
' "$TAG" "$verdict" "$nfail"
[[ "$verdict" != "VERIFICATION SUCCESSFUL" ]] && status=1
if grep -qE "no body for function|implicit function declaration" "$LOG/$TAG.log"; then echo "$TAG: missing body or implicit declaration (ISS-016)"; status=1; fi

# Generated tree: outputs in 0..3 for any input (= the SGO's TREE_CONTRACT), bounded traversal
TAG=tree
DEPTH=$(grep -E '^#define FDIR_TREE_DEPTH' "$ST" | awk '{print $3}')
cbmc "$HT" "$ST" -I "$INC" --unwind $((DEPTH + 2)) --unwinding-assertions "${COMMON[@]}" > "$LOG/$TAG.log" 2>&1 || true
grep -E '\.assertion\.[0-9]+\].*(SUCCESS|FAILURE)$' "$LOG/$TAG.log" > "$LOG/$TAG.properties.txt" || true
grep -E 'FAILURE$' "$LOG/$TAG.log" | grep -v '\.assertion\.' > "$LOG/$TAG.runtime_failures.txt" || true
verdict=$(grep -E '^VERIFICATION (SUCCESSFUL|FAILED)' "$LOG/$TAG.log" | tail -1)
nfail=$(grep -c 'FAILURE$' "$LOG/$TAG.properties.txt" || true)
printf '%-14s %-26s failed properties: %s\n' "$TAG" "$verdict" "$nfail"
[[ "$verdict" != "VERIFICATION SUCCESSFUL" ]] && status=1
if grep -qE "no body for function|implicit function declaration" "$LOG/$TAG.log"; then echo "$TAG: missing body or implicit declaration (ISS-016)"; status=1; fi

# Tree evaluator with ANY table contents (RAM tables havocked): outputs in 0..3, no OOB (item 5)
TAG=tree_any
HA="$ROOT/verify/cbmc/harness_tree_any.c"
cbmc "$HA" "$ST" -I "$INC" -DFDIR_TREE_IN_RAM -DFDIR_TREE_PROOF_HAVOC --unwind $((DEPTH + 3)) --unwinding-assertions "${COMMON[@]}" > "$LOG/$TAG.log" 2>&1 || true
grep -E '\.assertion\.[0-9]+\].*(SUCCESS|FAILURE)$' "$LOG/$TAG.log" > "$LOG/$TAG.properties.txt" || true
grep -E 'FAILURE$' "$LOG/$TAG.log" | grep -v '\.assertion\.' > "$LOG/$TAG.runtime_failures.txt" || true
verdict=$(grep -E '^VERIFICATION (SUCCESSFUL|FAILED)' "$LOG/$TAG.log" | tail -1)
nfail=$(grep -c 'FAILURE$' "$LOG/$TAG.properties.txt" || true)
printf '%-14s %-26s failed properties: %s
' "$TAG" "$verdict" "$nfail"
[[ "$verdict" != "VERIFICATION SUCCESSFUL" ]] && status=1
if grep -qE "no body for function|implicit function declaration" "$LOG/$TAG.log"; then echo "$TAG: missing body or implicit declaration (ISS-016)"; status=1; fi

# SoC EKF: one step from any state in the guard band, any float inputs
TAG=ekf
cbmc "$HE" "$SE" -I "$INC" -DSOC_EKF_MATH_MODELS -DSOC_EKF_EXP=cbmc_expf -DSOC_EKF_SQRT=cbmc_sqrtf \
     --unwind 9 --unwinding-assertions "${COMMON[@]}" > "$LOG/$TAG.log" 2>&1 || true
grep -E '\.assertion\.[0-9]+\].*(SUCCESS|FAILURE)$' "$LOG/$TAG.log" > "$LOG/$TAG.properties.txt" || true
grep -E 'FAILURE$' "$LOG/$TAG.log" | grep -v '\.assertion\.' > "$LOG/$TAG.runtime_failures.txt" || true
verdict=$(grep -E '^VERIFICATION (SUCCESSFUL|FAILED)' "$LOG/$TAG.log" | tail -1)
nfail=$(grep -c 'FAILURE$' "$LOG/$TAG.properties.txt" || true)
printf '%-14s %-26s failed properties: %s\n' "$TAG" "$verdict" "$nfail"
[[ "$verdict" != "VERIFICATION SUCCESSFUL" ]] && status=1
if grep -qE "no body for function|implicit function declaration" "$LOG/$TAG.log"; then echo "$TAG: missing body or implicit declaration (ISS-016)"; status=1; fi
exit $status
