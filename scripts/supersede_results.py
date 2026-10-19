"""Mark (and optionally archive) results that a new SGO / plant version supersedes.

Default: write results/SUPERSEDED.md listing every superseded file with the reason; nothing moves,
so the current report keeps working. --apply (pipeline_v32.sh step S0): move the files into
results/superseded/<tag>/ and the checkpoints into checkpoints/<tag>/, so the retrain writes into
empty directories and nothing old can be read by mistake.

Usage: python scripts/supersede_results.py --tag v3.1 [--apply]
"""
from __future__ import annotations

import argparse
import shutil
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REASON = ("SGO v3.2 (ADR-0024, 19 Oct 2026): datasheet-derated charger and LG M50 over-temperature 55 degC "
          "change the plant; invariant 7 changes the deployed safety layer; the tree is retrained and "
          "redistilled. Coverage, false-action, hazard and residual-risk numbers below were produced with "
          "SGO v3.1 on the underated plant (or earlier) and must not be cited.")
RESULTS = ["compare_methods.json", "residual_risk.json", "residual_risk_sgo_v3.json", "residual_risk_band.json",
           "esa_adb_metrics.json", "hil_cases.json", "fault_injection.json", "fault_injection_preguard.json",
           "timing_sweep.json", "tree_bitexact.json", "hil_fdir.json", "dagger_summary.json", "defender_summary.json",
           "defender", "dagger"]
CHECKPOINTS = ["defender", "tree"]
KEEP_NOTE = ("Still valid (not plant- or SGO-dependent): seu_rates.json, spenvis_inputs.json, ecm_scheduled.json, "
             "ekf_*.json, features_bitexact.json, xjtu_sps_ranges.json, threshold_table.json (regenerated in S13).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v3.1")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    present = [r for r in RESULTS if (ROOT / "results" / r).exists()]
    lines = [f"# Superseded results ({a.tag})", "", f"Marked {date.today().isoformat()}. {REASON}", "",
             "| File | Status |", "|---|---|"]
    lines += [f"| `results/{r}` | superseded by SGO v3.2 — {'archived to results/superseded/' + a.tag + '/' if a.apply else 'to be archived by pipeline step S0'} |"
              for r in present]
    lines += ["", KEEP_NOTE, ""]
    if a.apply:
        dst = ROOT / "results" / "superseded" / a.tag
        dst.mkdir(parents=True, exist_ok=True)
        for r in present:
            shutil.move(str(ROOT / "results" / r), str(dst / r))
        cdst = ROOT / "checkpoints" / a.tag
        cdst.mkdir(parents=True, exist_ok=True)
        for c in CHECKPOINTS:
            if (ROOT / "checkpoints" / c).exists():
                shutil.move(str(ROOT / "checkpoints" / c), str(cdst / c))
        lines.append(f"Checkpoints moved to `checkpoints/{a.tag}/`.")
    (ROOT / "results" / "SUPERSEDED.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
