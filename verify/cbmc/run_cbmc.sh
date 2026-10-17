#!/usr/bin/env bash
# Proves SGO properties with CBMC for v1 (original) and v2 (proposed),
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

declare -A SRC=( [v1]="$ROOT/firmware/src/fdir_sgo.c" [v2]="$ROOT/firmware/src/fdir_sgo_v2.c" )
HE="$ROOT/verify/cbmc/harness_ekf.c"
SE="$ROOT/firmware/src/soc_ekf.c"
sha256sum "$H" "$HE" "$INC"/*.h "${SRC[v1]}" "${SRC[v2]}" "$SE" > "$LOG/hashes.txt"

status=0
for VER in v1 v2; do
  for MODE in contract worstcase; do
    DEFS=()
    [[ "$MODE" == contract ]] && DEFS+=(-DTREE_CONTRACT)
    [[ "$VER"  == v2 ]]       && DEFS+=(-DSGO_V2)
    TAG="${VER}_${MODE}"
    cbmc "$H" "${SRC[$VER]}" -I "$INC" "${DEFS[@]}" "${COMMON[@]}" \
         > "$LOG/$TAG.log" 2>&1 || true
    grep -E '^\[main\.assertion.*(SUCCESS|FAILURE)$' "$LOG/$TAG.log" > "$LOG/$TAG.properties.txt" || true
    grep -E 'FAILURE$' "$LOG/$TAG.log" | grep -v '^\[main\.assertion' > "$LOG/$TAG.runtime_failures.txt" || true
    verdict=$(grep -E '^VERIFICATION (SUCCESSFUL|FAILED)' "$LOG/$TAG.log" | tail -1)
    nfail=$(grep -c 'FAILURE$' "$LOG/$TAG.properties.txt" || true)
    printf '%-14s %-26s failed properties: %s\n' "$TAG" "$verdict" "$nfail"
    [[ "$VER" == v2 && "$verdict" != "VERIFICATION SUCCESSFUL" ]] && status=1
    if grep -qE "no body for function|implicit function declaration" "$LOG/$TAG.log"; then echo "$TAG: missing body or implicit declaration (ISS-016)"; status=1; fi
  done
done

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
