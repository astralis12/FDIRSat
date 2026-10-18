"""Threshold traceability table (item 6): value, source, margin, verifying CBMC property.

Values are parsed from firmware/include/fdir_config_{ref,bench}.h + fdir_config.h (never retyped).
Cells (ADR-0023: one reference cell for the DFN plant, the capacity and the limits):
  ref    LG INR21700-M50 (PyBaMM Chen2020, the DFN plant cell). LG Chem Product Specification
         LRB-PS-CY18.2Wh-M50, 2017-09-22, Rev 0 (Tentative), docs/datasheets/
  bench  Sony/Murata US18650VTC6: Sony Technical Information Rev 0.1 (20 Jan 2015) + Murata SDS
         values supplied by the owner 18 Oct 2026
  JSC    JSC 20793 Rev C (Jan 2014), clauses checked in the NTRS PDF
  ECSS   clause mapping supplied by the owner 18 Oct 2026; clause text not checked here
Margin = distance from the threshold to the cell limit it protects (positive = safe side),
for the ref config against the M50 and for the bench config against the VTC6.
Writes results/threshold_table.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

M50 = {"model": "LG INR21700-M50", "doc": "LG Chem Product Specification LRB-PS-CY18.2Wh-M50, 2017-09-22, Rev 0 (Tentative)",
       "v_max": 4.20, "v_charge_tol": 0.0, "v_min": 2.50, "v_nominal": 3.63, "q_ah": 5.0, "q_1c_spec_a": 4.85,
       "i_charge_max_c": {"0..25": 0.3, "25..45": 0.7}, "i_discharge_max_c": {"-20..10": 0.5, "10..25": 3.0, "25..55": 1.5},
       "t_charge_c": [0.0, 45.0], "t_discharge_c": [-20.0, 55.0],
       "clauses": {"energy": "2.1 (18.2 Wh nominal, 17.6 Wh min)", "v_nominal": "2.2 (3.63 V)", "charge": "2.4/4.1.1 (0.3 C CC to 4.20 V, 50 mA end)",
                   "v_max": "2.5 (4.20 V)", "cutoff": "2.6/4.1.2 (2.50 V)", "temperature": "2.7 (charge 0..45, discharge -20..55 degC; section 5 warning says -20..60)",
                   "i_charge": "4.2.7 (0.3 C at 0..25, 0.7 C at 25..45 degC)", "i_discharge": "4.2.9 (0.5 C at -20..10, 3.0 C at 10..25, 1.5 C at 25..55 degC)"}}
VTC6 = {"model": "Sony/Murata US18650VTC6", "doc": "Sony Technical Information Rev 0.1 (20 Jan 2015) + Murata SDS (owner, 18 Oct 2026)",
        "v_max": 4.20, "v_charge_tol": 0.05, "v_min": 2.50, "v_nominal": 3.6, "q_ah": 3.0,
        "i_cont_no_cutoff_a": 15.0, "i_cont_80c_cutoff_a": 30.0, "t_charge_c": [0.0, 45.0], "t_discharge_c": [-20.0, 60.0]}
ECSS = {
    "system_fdir": "ECSS-Q-ST-30 Annex F (referenced by 6.4.2.10a): FDIR analysis at system level",
    "eps": "ECSS-E-ST-20-20C clause 1: single-point-failure-free power system, one failure loses at most one LCL/RLCL/HLCL line",
    "hierarchy": "ECSS-E-ST-70-11C section 5: FDIR levels, failures isolated at the lower level; deterministic end configuration",
    "thermal": "ECSS-E-ST-70-11C 5.9.6 (thermal control)",
    "aocs": "ECSS-E-ST-60-30C 5.1 (functional and FDIR requirements)",
}


def config(which: str = "ref") -> dict:
    """Numeric #defines of fdir_config_<which>.h followed by the shared fdir_config.h."""
    vals = {}
    for f in (f"fdir_config_{which}.h", "fdir_config.h"):
        txt = (ROOT / "firmware" / "include" / f).read_text(encoding="utf-8")
        for m in re.finditer(r"#define\s+(FDIR_\w+)\s+\(?(-?[0-9.]+)f?\)?", txt):
            vals.setdefault(m.group(1), float(m.group(2)))
    return vals


def rows_for(c: dict, cell: dict) -> list[dict]:
    """Margins of one configuration against its own cell."""
    q, npar = c["FDIR_CELL_CAPACITY_AH"], c["FDIR_N_PARALLEL"]
    i_lim = cell["i_discharge_max_c"]["25..55"] * cell["q_ah"] if "i_discharge_max_c" in cell else cell["i_cont_no_cutoff_a"]
    return [
        {"threshold": "Deep discharge, cell voltage", "symbol": "FDIR_VCELL_CRITICAL_MIN", "value": c["FDIR_VCELL_CRITICAL_MIN"], "unit": "V/cell",
         "margin": c["FDIR_VCELL_CRITICAL_MIN"] - cell["v_min"], "margin_vs": f"{cell['model']} discharge cut-off {cell['v_min']} V", "cbmc": "P3a-c"},
        {"threshold": "Undervoltage, cell voltage", "symbol": "FDIR_VCELL_NOMINAL_MIN", "value": c["FDIR_VCELL_NOMINAL_MIN"], "unit": "V/cell",
         "margin": c["FDIR_VCELL_NOMINAL_MIN"] - c["FDIR_VCELL_CRITICAL_MIN"], "margin_vs": "deep-discharge threshold", "cbmc": "P4"},
        {"threshold": "Overvoltage, pack-average cell voltage", "symbol": "FDIR_VCELL_OV_MAX", "value": c["FDIR_VCELL_OV_MAX"], "unit": "V/cell",
         "margin": cell["v_max"] + cell["v_charge_tol"] - c["FDIR_VCELL_OV_MAX"],
         "margin_vs": f"{cell['model']} max charge voltage {cell['v_max']} V (+{cell['v_charge_tol']} V charger tolerance); average, one cell may be higher", "cbmc": "P10a-c"},
        {"threshold": "Overcurrent, battery", "symbol": "FDIR_C_RATE_MAX", "value": c["FDIR_C_RATE_MAX"], "unit": "C",
         "pack_value": c["FDIR_C_RATE_MAX"] * q * npar, "pack_unit": f"A ({int(c['FDIR_N_SERIES'])}S{int(npar)}P)",
         "margin": i_lim - c["FDIR_C_RATE_MAX"] * cell["q_ah"], "margin_vs": f"{cell['model']} max continuous discharge per cell (A; M50 at 25..55 degC)", "cbmc": "P5a-b"},
        {"threshold": "Over-temperature, battery", "symbol": "FDIR_TBAT_CRITICAL_MAX", "value": c["FDIR_TBAT_CRITICAL_MAX"], "unit": "degC",
         "margin": cell["t_discharge_c"][1] - c["FDIR_TBAT_CRITICAL_MAX"], "margin_vs": f"{cell['model']} discharge max {cell['t_discharge_c'][1]} degC", "cbmc": "P6a-b"},
        {"threshold": "Charge-temperature window (charge inhibit)", "symbol": "FDIR_TBAT_CHARGE_MIN/MAX", "value": [c["FDIR_TBAT_CHARGE_MIN"], c["FDIR_TBAT_CHARGE_MAX"]], "unit": "degC",
         "margin": [c["FDIR_TBAT_CHARGE_MIN"] - cell["t_charge_c"][0], cell["t_charge_c"][1] - c["FDIR_TBAT_CHARGE_MAX"]],
         "margin_vs": f"{cell['model']} charge window {cell['t_charge_c']}", "cbmc": "P11a-d"},
        {"threshold": "Charge-inhibit release (hysteresis)", "symbol": "FDIR_TBAT_CHARGE_REL_MIN/MAX", "value": [c["FDIR_TBAT_CHARGE_REL_MIN"], c["FDIR_TBAT_CHARGE_REL_MAX"]], "unit": "degC",
         "margin": [c["FDIR_TBAT_CHARGE_REL_MIN"] - c["FDIR_TBAT_CHARGE_MIN"], c["FDIR_TBAT_CHARGE_MAX"] - c["FDIR_TBAT_CHARGE_REL_MAX"]],
         "margin_vs": "hysteresis width to the window (owner 18 Oct: 2 / 42 degC)", "cbmc": "P11b-c"},
    ]


SOURCES = {
    "FDIR_VCELL_CRITICAL_MIN": "fdir_config.h (3.0 V/cell); JSC 20793 Rev C 4.4.3a (detect and control under-voltage of any cell)",
    "FDIR_VCELL_NOMINAL_MIN": "derived: 3.6 V/cell = original 10.8 V / 3S nominal minimum; ECSS-E-ST-20-20C clause 1 (owner mapping)",
    "FDIR_VCELL_OV_MAX": "ADR-0018; ref 4.20 V = M50 spec 2.5; bench 4.25 V (charger CV fixed 4.2 +/- 0.05 V, ISS-049); JSC 20793 Rev C 5.1.2.1 a, e, i; 4.4.3a needs cell taps (ISS-047)",
    "FDIR_C_RATE_MAX": "fdir_config.h (1.5 C); M50 spec 4.2.9 (1.5 C at 25..55 degC, 0.5 C below 10 degC: not temperature-scheduled here, ISS-059); VTC6 15 A continuous; JSC 20793 Rev C 4.4.3b",
    "FDIR_TBAT_CRITICAL_MAX": "ref 55 degC = M50 spec 2.7 discharge max (ADR-0023, was 60); bench 60 degC = VTC6; JSC 20793 Rev C 4.4.3c",
    "FDIR_TBAT_CHARGE_MIN/MAX": "ISS-050 / ADR-0020; ref M50 spec 2.7 (0..45 degC); bench VTC6 Murata SDS (0..45 degC)",
    "FDIR_TBAT_CHARGE_REL_MIN/MAX": "owner 18 Oct 2026 (2 / 42 degC)",
}


def main() -> None:
    c, cb = config("ref"), config("bench")
    ref, bench = rows_for(c, M50), rows_for(cb, VTC6)
    rows = []
    for r, b in zip(ref, bench):
        r["source"] = SOURCES[r["symbol"]]
        r["bench"] = {"value": b["value"], "margin": b["margin"], "margin_vs": b["margin_vs"], **({"pack_value": b["pack_value"], "pack_unit": b["pack_unit"]} if "pack_value" in b else {})}
        rows.append(r)
    rows += [
        {"threshold": "Temperature sensor disagreement (median-of-3)", "symbol": "FDIR_TBAT_SENSOR_DISAGREE", "value": c["FDIR_TBAT_SENSOR_DISAGREE"], "unit": "degC",
         "source": "PROPOSED (ISS-050); DS18B20 +/-0.5 degC, sensors on one pack", "margin": None, "margin_vs": "-", "cbmc": "P11e-g"},
        {"threshold": "Deep discharge, SoC", "symbol": "FDIR_SOC_CRITICAL_MIN", "value": c["FDIR_SOC_CRITICAL_MIN"], "unit": "%",
         "source": "fdir_config.h; EKF 2-sigma ~0.6 % SoC (results/ekf_sigma_cv.json)", "margin": c["FDIR_SOC_CRITICAL_MIN"], "margin_vs": "0 % SoC", "cbmc": "P3a-c"},
        {"threshold": "Plausibility, cell voltage", "symbol": "FDIR_VCELL_PLAUSIBLE_MIN/MAX", "value": [c["FDIR_VCELL_PLAUSIBLE_MIN"], c["FDIR_VCELL_PLAUSIBLE_MAX"]], "unit": "V/cell",
         "source": "ADR-0017 (physically impossible readings)", "margin": c["FDIR_VCELL_PLAUSIBLE_MAX"] - (VTC6["v_max"] + VTC6["v_charge_tol"]),
         "margin_vs": "highest charge voltage of either cell (VTC6 4.25 V)", "cbmc": "P7, P9a-b"},
        {"threshold": "Plausibility, SoC", "symbol": "FDIR_SOC_PLAUSIBLE_MIN/MAX", "value": [c["FDIR_SOC_PLAUSIBLE_MIN"], c["FDIR_SOC_PLAUSIBLE_MAX"]], "unit": "%",
         "source": "ADR-0017 (small EKF overshoot tolerated)", "margin": None, "margin_vs": "-", "cbmc": "P8, P9a-b"},
        {"threshold": "Plausibility, temperature", "symbol": "FDIR_TBAT_PLAUSIBLE_MIN/MAX", "value": [c["FDIR_TBAT_PLAUSIBLE_MIN"], c["FDIR_TBAT_PLAUSIBLE_MAX"]], "unit": "degC",
         "source": "ADR-0017; NASA RW18 -4099 degC (ISS-021) and XJTU T_Load1 210 degC (ISS-031) rejected", "margin": None, "margin_vs": "-", "cbmc": "P7, P9a-b"},
        {"threshold": "EKF accepted current", "symbol": "FDIR_EKF_C_RATE_ABS_MAX", "value": c["FDIR_EKF_C_RATE_ABS_MAX"], "unit": "C",
         "source": "fdir_config.h (EKF guard G1); EKF tables use the DFN (M50) capacity 5.0 Ah", "margin": None, "margin_vs": "-", "cbmc": "E4"},
        {"threshold": "EKF SoC guard band", "symbol": "FDIR_EKF_SOC_MIN/MAX", "value": [c["FDIR_EKF_SOC_MIN"], c["FDIR_EKF_SOC_MAX"]], "unit": "-",
         "source": "fdir_config.h (EKF guard G3)", "margin": None, "margin_vs": "-", "cbmc": "E2, E6"},
        {"threshold": "Orbit-to-probability conversion", "symbol": "P = 1 - exp(-lambda T_orb)", "value": "lambda T_orb for small lambda", "unit": "-",
         "source": "derived formula (item 6); T_orb from sim/env_v2.py ORBITS", "margin": None, "margin_vs": "-", "cbmc": "-"},
    ]
    configs = {k: {"cell": cell["model"], "n_series": v["FDIR_N_SERIES"], "n_parallel": v["FDIR_N_PARALLEL"], "q_ah": v["FDIR_CELL_CAPACITY_AH"],
                   "pack_ah": v["FDIR_CELL_CAPACITY_AH"] * v["FDIR_N_PARALLEL"], "vcell_ov_max": v["FDIR_VCELL_OV_MAX"],
                   "ibat_critical_a": v["FDIR_C_RATE_MAX"] * v["FDIR_CELL_CAPACITY_AH"] * v["FDIR_N_PARALLEL"],
                   "tbat_critical_max": v["FDIR_TBAT_CRITICAL_MAX"], "tbat_charge": [v["FDIR_TBAT_CHARGE_MIN"], v["FDIR_TBAT_CHARGE_MAX"]]}
               for k, v, cell in (("ref", c, M50), ("bench", cb, VTC6))}
    configs["bench"]["note"] = "Q is a PLACEHOLDER (VTC6 rated 3.0 Ah) until the C/5 capacity in the 4.20-3.3 V bench window is measured"
    configs["bench"]["current_scale_to_ref"] = configs["ref"]["pack_ah"] / configs["bench"]["pack_ah"]
    res = {"cells": {"ref": M50, "bench": VTC6}, "ecss": ECSS, "configs": configs, "rows": rows,
           "pending": ["bench cell capacity measured at C/5 in the 4.20-3.3 V window",
                       "MIL-HDBK-217F part list with C1, C2, pi_T, pi_E (SF), pi_Q, pi_L per part (sections 5, 6, 9-15)",
                       "ECSS clause text check (mapping is owner-supplied)",
                       "M50 spec is Rev 0 'Tentative' (2017); confirm no later revision changes the limits"]}
    (ROOT / "results" / "threshold_table.json").write_text(json.dumps(res, indent=2))
    for r in rows:
        print(f"{r['threshold']:44s} {str(r['value']):14s} {r['unit']:7s} margin {r['margin']}  bench {r.get('bench', {}).get('margin')}  {r['cbmc']}")
    print({k: {kk: v[kk] for kk in ("cell", "pack_ah", "ibat_critical_a", "tbat_critical_max")} for k, v in configs.items()},
          "scale", round(configs["bench"]["current_scale_to_ref"], 2))


if __name__ == "__main__":
    main()
