"""Threshold traceability table (item 6): value, source, margin, verifying CBMC property.

Values are parsed from firmware/include/fdir_config.h (never retyped). Limits:
  cell     plan doc 'Reference spacecraft': XJTU-type 18650 NCM, 4.2 / 2.5 V cut-offs,
           2 Ah - PENDING the cell model and datasheet clause from the owner
  JSC      JSC 20793 Rev C (Jan 2014), clauses checked in the NTRS PDF
  ECSS     ECSS-E-ST-20C - clause numbers PENDING (text not available here)
Margin = distance from the threshold to the limit it protects (sign: positive = safe side).
Writes results/threshold_table.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CELL = {"v_max": 4.2, "v_min": 2.5, "q_ah": 2.0, "src": "plan doc, Reference spacecraft (XJTU-type 18650 NCM, 4.2/2.5 V); PENDING cell datasheet"}


def config() -> dict:
    txt = (ROOT / "firmware" / "include" / "fdir_config.h").read_text(encoding="utf-8")
    vals = {}
    for m in re.finditer(r"#define\s+(FDIR_\w+)\s+\(?(-?[0-9.]+)f?\)?", txt):
        vals[m.group(1)] = float(m.group(2))
    return vals


def main() -> None:
    c = config()
    ns, npar = c["FDIR_N_SERIES"], c["FDIR_N_PARALLEL"]
    rows = [
        {"threshold": "Deep discharge, cell voltage", "symbol": "FDIR_VCELL_CRITICAL_MIN", "value": c["FDIR_VCELL_CRITICAL_MIN"], "unit": "V/cell",
         "source": f"{CELL['src']}; JSC 20793 Rev C 4.4.3a (detect and control under-voltage of any cell)",
         "margin": c["FDIR_VCELL_CRITICAL_MIN"] - CELL["v_min"], "margin_vs": "cell cut-off 2.5 V", "cbmc": "P3a-c (SGO v2/v3)"},
        {"threshold": "Undervoltage, cell voltage", "symbol": "FDIR_VCELL_NOMINAL_MIN", "value": c["FDIR_VCELL_NOMINAL_MIN"], "unit": "V/cell",
         "source": "derived: 3.6 V/cell = original 10.8 V / 3S nominal minimum (fdir_config.h header); ECSS-E-ST-20C clause PENDING",
         "margin": c["FDIR_VCELL_NOMINAL_MIN"] - c["FDIR_VCELL_CRITICAL_MIN"], "margin_vs": "deep-discharge threshold", "cbmc": "P4"},
        {"threshold": "Overvoltage, pack-average cell voltage", "symbol": "FDIR_VCELL_OV_MAX", "value": c["FDIR_VCELL_OV_MAX"], "unit": "V/cell",
         "source": f"ADR-0018; {CELL['src']}; JSC 20793 Rev C 5.1.2.1 a, e, i (overcharge failure tolerance, no uncontrolled charging); 4.4.3a requires per-cell monitoring (taps)",
         "margin": CELL["v_max"] - c["FDIR_VCELL_OV_MAX"], "margin_vs": "cell charge cut-off 4.2 V (average; one cell may be higher)", "cbmc": "P10a-c (SGO v3)"},
        {"threshold": "Overcurrent, battery", "symbol": "FDIR_C_RATE_MAX", "value": c["FDIR_C_RATE_MAX"], "unit": "C",
         "source": "fdir_config.h (1.5 C); JSC 20793 Rev C 4.4.3b (battery-level current monitoring); cell max continuous discharge PENDING datasheet",
         "margin": None, "margin_vs": "cell max continuous current (PENDING)", "cbmc": "P5a-b",
         "pack_value": c["FDIR_C_RATE_MAX"] * CELL["q_ah"] * npar, "pack_unit": "A (8S4P)"},
        {"threshold": "Over-temperature, battery", "symbol": "FDIR_TBAT_CRITICAL_MAX", "value": c["FDIR_TBAT_CRITICAL_MAX"], "unit": "degC",
         "source": "fdir_sgo.h v1 (ECSS_TBAT_CRITICAL_MAX, cell qualification envelope); JSC 20793 Rev C 4.4.3c (temperature monitoring); bench rule 45 degC (CLAUDE.md)",
         "margin": None, "margin_vs": "cell max charge / discharge temperature (PENDING datasheet)", "cbmc": "P6a-b"},
        {"threshold": "Deep discharge, SoC", "symbol": "FDIR_SOC_CRITICAL_MIN", "value": c["FDIR_SOC_CRITICAL_MIN"], "unit": "%",
         "source": "fdir_config.h; EKF 2-sigma ~0.6 % SoC (results/ekf_sigma_cv.json) sets the margin to 0 %",
         "margin": c["FDIR_SOC_CRITICAL_MIN"], "margin_vs": "0 % SoC", "cbmc": "P3a-c"},
        {"threshold": "Plausibility, cell voltage", "symbol": "FDIR_VCELL_PLAUSIBLE_MIN/MAX", "value": [c["FDIR_VCELL_PLAUSIBLE_MIN"], c["FDIR_VCELL_PLAUSIBLE_MAX"]], "unit": "V/cell",
         "source": "ADR-0017 (physically impossible readings)", "margin": c["FDIR_VCELL_PLAUSIBLE_MAX"] - CELL["v_max"], "margin_vs": "cell max 4.2 V", "cbmc": "P7, P9a-b"},
        {"threshold": "Plausibility, SoC", "symbol": "FDIR_SOC_PLAUSIBLE_MIN/MAX", "value": [c["FDIR_SOC_PLAUSIBLE_MIN"], c["FDIR_SOC_PLAUSIBLE_MAX"]], "unit": "%",
         "source": "ADR-0017 (small EKF overshoot tolerated)", "margin": None, "margin_vs": "-", "cbmc": "P8, P9a-b"},
        {"threshold": "Plausibility, temperature", "symbol": "FDIR_TBAT_PLAUSIBLE_MIN/MAX", "value": [c["FDIR_TBAT_PLAUSIBLE_MIN"], c["FDIR_TBAT_PLAUSIBLE_MAX"]], "unit": "degC",
         "source": "ADR-0017; NASA RW18 -4099 degC channel (ISS-021) and XJTU T_Load1 210 degC spike (ISS-031) are rejected", "margin": None, "margin_vs": "-", "cbmc": "P7, P9a-b"},
        {"threshold": "EKF accepted current", "symbol": "FDIR_EKF_C_RATE_ABS_MAX", "value": c["FDIR_EKF_C_RATE_ABS_MAX"], "unit": "C",
         "source": "fdir_config.h (EKF guard G1)", "margin": None, "margin_vs": "-", "cbmc": "E4 (rejected input leaves state unchanged)"},
        {"threshold": "EKF SoC guard band", "symbol": "FDIR_EKF_SOC_MIN/MAX", "value": [c["FDIR_EKF_SOC_MIN"], c["FDIR_EKF_SOC_MAX"]], "unit": "-",
         "source": "fdir_config.h (EKF guard G3)", "margin": None, "margin_vs": "-", "cbmc": "E2, E6"},
        {"threshold": "Orbit-to-probability conversion", "symbol": "P = 1 - exp(-lambda T_orb)", "value": "lambda T_orb for small lambda", "unit": "-",
         "source": "derived formula (item 6); T_orb from sim/env_v2.py ORBITS", "margin": None, "margin_vs": "-", "cbmc": "-"},
    ]
    res = {"cell_limits": CELL, "n_series": ns, "n_parallel": npar, "rows": rows,
           "pending": ["cell model and datasheet clauses (owner)", "ECSS-E-ST-20C clause numbers", "MIL-HDBK-217F lambda_g / pi_Q values",
                       "SPENVIS SEU rate per orbit"]}
    (ROOT / "results" / "threshold_table.json").write_text(json.dumps(res, indent=2))
    for r in rows:
        print(f"{r['threshold']:42s} {str(r['value']):14s} {r['unit']:7s} margin {r['margin']}  {r['cbmc']}")


if __name__ == "__main__":
    main()
