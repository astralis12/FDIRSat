#!/usr/bin/env bash
# 9-input pipeline, resumable: skips finished seeds, retries a failed run once
cd "C:/Users/Lyskq/Documents/brin/RAMS/fdir-v2-2026-10-17d/fdir-v2"
def_one() { s=$1; [ -f results/defender/s$s.json ] && { echo "defender $s done"; return; }
  for t in 1 2; do .venv/Scripts/python.exe train/train_defender.py --seed $s > "$TMPDIR/logs/def9_s$s.log" 2>&1 && { echo "defender $s ok"; return; }; echo "defender $s failed (try $t)"; sleep 20; done; }
dag_one() { s=$1; until [ -f results/defender/s$s.json ]; do sleep 30; done; [ -f results/dagger/sw_dagger_s$s.json ] && { echo "dagger $s done"; return; }
  for t in 1 2; do .venv/Scripts/python.exe train/dagger_adversary.py --seed $s --mode sw_dagger > "$TMPDIR/logs/dag9_s$s.log" 2>&1 && { echo "dagger $s ok"; return; }; echo "dagger $s failed (try $t)"; sleep 20; done; }
export -f def_one dag_one
printf '%s\n' 3 4 5 6 7 8 9 | xargs -P 2 -I{} bash -c 'def_one {}' &
printf '%s\n' 0 1 2 3 4 5 6 7 8 9 | xargs -P 2 -I{} bash -c 'dag_one {}'
wait
.venv/Scripts/python.exe train/summarise.py > "$TMPDIR/logs/summ9.log" 2>&1
echo "pipeline done $(date)"
