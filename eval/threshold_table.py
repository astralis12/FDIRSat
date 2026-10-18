"""Threshold traceability table (item 6): value, source, margin, verifying CBMC property.

Values are parsed from firmware/include/fdir_config.h (never retyped). Limits:
  cell     bench pack cell Sony US18650VTC6, Technical Information Rev 0.1 (20 Jan 2015),
           supplied by the owner 18 Oct 2026. The firmware still uses the XJTU-type 2.0 Ah
           reference cell (FDIR_CELL_CAPACITY_AH); see ISS-049.
  JSC      JSC 20793 Rev C (Jan 2014), clauses checked in the NTRS PDF
  ECSS     clause mapping supplied by the owner 18 Oct 2026 (ECSS dict below); clause text not
           checked here
Margin = distance from the threshold to the limit it protects (sign: positive = safe side).
Writes results/threshold_table.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DS = "Sony US18650VTC6 Technical Information Rev 0.1"
CELL = {"model": "Sony US18650VTC6", "v_max": 4.2, "v_charge_tol": 0.05, "v_min": 2.5, "v_min_rating": 2.0,
        "q_ah": 3.0, "q_nominal_ah": 3.13, "v_nominal": 3.6, "i_charge_std_a": 3.0, "i_discharge_tested_a": 20.0,
        "i_cycle_rated_a": 15.0, "i_cont_no_cutoff_a": 15.0, "i_cont_80c_cutoff_a": 30.0,
        "t_discharge_char_c": [-20.0, 60.0], "t_charge_c": [0.0, 45.0],
        "src_owner_18oct": "owner 18 Oct 2026 from the Murata SDS and product datasheet: 30 A with 80 degC cut-off, 15-20 A without; discharge -20..60 degC; charge 0..45 degC; CC/CV to 4.2 V (4.25 V max)",
        "src": f"{DS}: charge 4.2 +/- 0.05 V CC/CV 3.0 A; rated 3000 mAh (0.2 C to 2.0 V); 2.5 V cut-off at 1 C and 10 A; "
               "discharge curves -20..60 degC; cycle life at 7.5 A and 15 A (charged to 4.15 V); load curves to 20 A",
        "firmware_q_ah": 2.0}
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


def main() -> None:
    c = config("ref")
    cb = config("bench")
    ns, npar = c["FDIR_N_SERIES"], c["FDIR_N_PARALLEL"]
    rows = [
        {"threshold": "Deep discharge, cell voltage", "symbol": "FDIR_VCELL_CRITICAL_MIN", "value": c["FDIR_VCELL_CRITICAL_MIN"], "unit": "V/cell",
         "source": f"{CELL['src']}; JSC 20793 Rev C 4.4.3a (detect and control under-voltage of any cell)",
         "margin": c["FDIR_VCELL_CRITICAL_MIN"] - CELL["v_min"], "margin_vs": "VTC6 discharge cut-off 2.5 V (rating uses 2.0 V)", "cbmc": "P3a-c (SGO v2/v3)"},
        {"threshold": "Undervoltage, cell voltage", "symbol": "FDIR_VCELL_NOMINAL_MIN", "value": c["FDIR_VCELL_NOMINAL_MIN"], "unit": "V/cell",
         "source": f"derived: 3.6 V/cell = original 10.8 V / 3S nominal minimum (fdir_config.h header) = VTC6 nominal voltage 3.6 V ({DS}); {ECSS['eps']}",
         "margin": c["FDIR_VCELL_NOMINAL_MIN"] - c["FDIR_VCELL_CRITICAL_MIN"], "margin_vs": "deep-discharge threshold", "cbmc": "P4"},
        {"threshold": "Overvoltage, pack-average cell voltage", "symbol": "FDIR_VCELL_OV_MAX", "value": c["FDIR_VCELL_OV_MAX"], "unit": "V/cell",
         "source": f"ADR-0018; {CELL['src']}; JSC 20793 Rev C 5.1.2.1 a, e, i (overcharge failure tolerance, no uncontrolled charging); 4.4.3a requires per-cell monitoring (taps)",
         "margin": CELL["v_max"] - c["FDIR_VCELL_OV_MAX"], "margin_vs": "VTC6 charge voltage 4.2 V; the charger tolerance is +/-0.05 V, so a charger at the upper tolerance (4.25 V) trips the invariant at end of charge (ISS-049)",
         "margin_vs_charger_hi": c["FDIR_VCELL_OV_MAX"] - (CELL["v_max"] + CELL["v_charge_tol"]), "cbmc": "P10a-c (SGO v3)"},
        {"threshold": "Overcurrent, battery", "symbol": "FDIR_C_RATE_MAX", "value": c["FDIR_C_RATE_MAX"], "unit": "C",
         "source": f"fdir_config.h (1.5 C); JSC 20793 Rev C 4.4.3b (battery-level current monitoring); {DS} cycle-life rating at 15 A; Murata SDS 15 A continuous without thermal cut-off",
         "margin": CELL["i_cont_no_cutoff_a"] - c["FDIR_C_RATE_MAX"] * CELL["q_ah"], "margin_vs": "VTC6 15 A continuous without thermal cut-off, per cell (A)", "cbmc": "P5a-b",
         "pack_value": c["FDIR_C_RATE_MAX"] * CELL["q_ah"] * npar, "pack_unit": "A (8S4P VTC6)",
         "pack_value_firmware": c["FDIR_IBAT_CRITICAL_MAX"] if "FDIR_IBAT_CRITICAL_MAX" in c else c["FDIR_C_RATE_MAX"] * CELL["firmware_q_ah"] * npar},
        {"threshold": "Over-temperature, battery", "symbol": "FDIR_TBAT_CRITICAL_MAX", "value": c["FDIR_TBAT_CRITICAL_MAX"], "unit": "degC",
         "source": "fdir_sgo.h v1 (ECSS_TBAT_CRITICAL_MAX, cell qualification envelope); JSC 20793 Rev C 4.4.3c (temperature monitoring); bench rule 45 degC (CLAUDE.md)",
         "margin": CELL["t_discharge_char_c"][1] - c["FDIR_TBAT_CRITICAL_MAX"], "margin_vs": "top of the VTC6 discharge range 60 degC", "cbmc": "P6a-b"},
        {"threshold": "Charge-temperature window (charge inhibit)", "symbol": "FDIR_TBAT_CHARGE_MIN/MAX", "value": [c["FDIR_TBAT_CHARGE_MIN"], c["FDIR_TBAT_CHARGE_MAX"]], "unit": "degC",
         "source": "ISS-050 (owner approval 18 Oct 2026); VTC6 charge 0..45 degC (Murata SDS); ref config ASSUMED same until the XJTU cell datasheet is traced; JSC 20793 Rev C 4.4.3c",
         "margin": [c["FDIR_TBAT_CHARGE_MIN"] - CELL["t_charge_c"][0], CELL["t_charge_c"][1] - c["FDIR_TBAT_CHARGE_MAX"]], "margin_vs": "VTC6 charge window (low, high)", "cbmc": "P11a-d (SGO v3)"},
        {"threshold": "Charge-inhibit release (hysteresis)", "symbol": "FDIR_TBAT_CHARGE_REL_MIN/MAX", "value": [c["FDIR_TBAT_CHARGE_REL_MIN"], c["FDIR_TBAT_CHARGE_REL_MAX"]], "unit": "degC",
         "source": "owner 18 Oct 2026 (2 / 42 degC)", "margin": [c["FDIR_TBAT_CHARGE_REL_MIN"] - c["FDIR_TBAT_CHARGE_MIN"], c["FDIR_TBAT_CHARGE_MAX"] - c["FDIR_TBAT_CHARGE_REL_MAX"]],
         "margin_vs": "hysteresis width to the window", "cbmc": "P11b-c"},
        {"threshold": "Temperature sensor disagreement (median-of-3)", "symbol": "FDIR_TBAT_SENSOR_DISAGREE", "value": c["FDIR_TBAT_SENSOR_DISAGREE"], "unit": "degC",
         "source": "PROPOSED (ISS-050); DS18B20 +/-0.5 degC accuracy, sensors on one pack", "margin": None, "margin_vs": "-", "cbmc": "P11e-g (vote bounded by any two valid sensors)"},
        {"threshold": "Deep discharge, SoC", "symbol": "FDIR_SOC_CRITICAL_MIN", "value": c["FDIR_SOC_CRITICAL_MIN"], "unit": "%",
         "source": "fdir_config.h; EKF 2-sigma ~0.6 % SoC (results/ekf_sigma_cv.json) sets the margin to 0 %",
         "margin": c["FDIR_SOC_CRITICAL_MIN"], "margin_vs": "0 % SoC", "cbmc": "P3a-c"},
        {"threshold": "Plausibility, cell voltage", "symbol": "FDIR_VCELL_PLAUSIBLE_MIN/MAX", "value": [c["FDIR_VCELL_PLAUSIBLE_MIN"], c["FDIR_VCELL_PLAUSIBLE_MAX"]], "unit": "V/cell",
         "source": "ADR-0017 (physically impossible readings)", "margin": c["FDIR_VCELL_PLAUSIBLE_MAX"] - (CELL["v_max"] + CELL["v_charge_tol"]), "margin_vs": "VTC6 charge voltage at upper tolerance 4.25 V", "cbmc": "P7, P9a-b"},
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
    configs = {k: {"n_series": v["FDIR_N_SERIES"], "n_parallel": v["FDIR_N_PARALLEL"], "q_ah": v["FDIR_CELL_CAPACITY_AH"],
                   "vcell_ov_max": v["FDIR_VCELL_OV_MAX"], "ibat_critical_a": v["FDIR_C_RATE_MAX"] * v["FDIR_CELL_CAPACITY_AH"] * v["FDIR_N_PARALLEL"],
                   "tbat_charge": [v["FDIR_TBAT_CHARGE_MIN"], v["FDIR_TBAT_CHARGE_MAX"]]}
               for k, v in (("ref", c), ("bench", cb))}
    configs["bench"]["note"] = "Q is a PLACEHOLDER (VTC6 rated 3.0 Ah) until the C/5 capacity in the 4.20-3.3 V bench window is measured; OV 4.25 V because the charger CV is fixed at 4.2 +/- 0.05 V (ISS-049)"
    configs["bench"]["current_scale_to_ref"] = configs["ref"]["q_ah"] * configs["ref"]["n_parallel"] / (configs["bench"]["q_ah"] * configs["bench"]["n_parallel"])
    res = {"cell_limits": CELL, "ecss": ECSS, "configs": configs, "n_series": ns, "n_parallel": npar, "rows": rows,
           "pending": [
                       "MIL-HDBK-217F part list with C1, C2, pi_T, pi_E (SF), pi_Q, pi_L per part (sections 5, 6, 9-15)",
                       "XJTU reference cell datasheet (charge window, ref config ASSUMED 0..45 degC)",
                       "bench cell capacity measured at C/5 in the 4.20-3.3 V window",
                       "ECSS clause text check (mapping is owner-supplied)"]}
    (ROOT / "results" / "threshold_table.json").write_text(json.dumps(res, indent=2))
    for r in rows:
        print(f"{r['threshold']:42s} {str(r['value']):14s} {r['unit']:7s} margin {r['margin']}  {r['cbmc']}")


if __name__ == "__main__":
    main()
