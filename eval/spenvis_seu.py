"""SPENVIS long-term SEU rates (item 6, ISS-048) -> results/seu_rates.json.

Reads the saved SPENVIS 'Long-term Single Event Upset Rates' pages (spenvis_nuopl.html) in
data/spenvis/html. SPENVIS reports total mission upsets per bit; the runs are 1 year long
(data/spenvis/run*/inputs.yaml), so rate = total / 365.25 per bit per day.
Run 0 (GEO, 100 mil Al, solar minimum) checks the device inputs against ST's published 65 nm
SRAM rates (Roche 2009): pass if D_mid is within a factor of 3 of the reference.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "data" / "spenvis" / "html"
DAYS = 365.25
RUNS = {  # file tag -> (run id, orbit key in sim/env_v2.py, shielding mm Al)
    "GEO": ("run0_geo_check", "geo", 2.54),
    "XJTU": ("run1_xjtu", "xjtu", 2.0),
    "sso_1030": ("run2_o2_sso_1030", "o2_sso_1030", 2.0),
    "dawn_dusk": ("run3_o3_dawn_dusk", "o3_dawn_dusk", 2.0),
    "1mm": ("run4_o3_1mm", "o3_dawn_dusk", 1.0),
    "4mm": ("run5_o3_4mm", "o3_dawn_dusk", 4.0),
}
GEO_REF = {"direct": 1.66e-7, "proton": 2.56e-8,
           "source": "Roche, P. (2009) SEE and TID Radiation Test Results on ST Circuits in 65nm CMOS Technologies, ESTEC 18799/04/NL/AG"}


def parse(path: Path) -> dict:
    t = path.read_text(encoding="cp1252")
    m = re.search(r"<b>(\d{2}-\w{3}-\d{4})(?:&nbsp;)+(\d{2}:\d{2}:\d{2})</b>", t)
    head = {
        "spenvis_version": re.search(r"SPENVIS&nbsp;([\d.]+)", t).group(1),
        "run_time": f"{m.group(1)} {m.group(2)}",
        "models": re.findall(r"(AP-8 \w+|ESP-PSYCHIC [\w ]+?fluence|ISO 15390)", t),
        "shielding": re.sub(r"\s+", " ", re.search(r"Al equivalent\) :</b>([^<]+)", t).group(1)).strip(),
    }
    devs = {}
    for mm in re.finditer(r"<th rowspan=\"3\">(STM32F411_\w+).*?Total</td>\s*<td align=\"right\">\s*[\dE.+-]+", t, re.S):
        v = [float(x) for x in re.findall(r"<td align=\"right\">\s*([\dE.+-]+)", mm.group(0))]
        key = "D_" + mm.group(1).split("_")[1]
        devs[key] = {"direct_per_bit_mission": v[0], "proton_per_bit_mission": v[1], "total_per_bit_mission": v[2],
                     "direct_per_bit_day": v[0] / DAYS, "proton_per_bit_day": v[1] / DAYS, "total_per_bit_day": v[2] / DAYS}
    # L0 as SPENVIS echoed it (the inputs.yaml may differ, see ISS-051)
    head["L0_echo"] = [float(x) for x in re.findall(r"L<sub>0</sub> =\s*([\d.]+)", t)]
    head["devices"] = devs
    return head


def main() -> None:
    out = {"_source": "SPENVIS 4.6 long-term SEU (spenvis_nuopl.html), saved by the owner 18 Oct 2026; 1-year missions from 2027-01-01",
           "mission_days": DAYS, "runs": {}}
    for tag, (rid, orbit, mm) in RUNS.items():
        f = HTML / f"Long-term Single Event Upset Rates ({tag}).html"
        r = parse(f)
        r.update({"run_id": rid, "orbit": orbit, "shielding_mm_al": mm, "file": str(f.relative_to(ROOT)).replace("\\", "/")})
        out["runs"][rid] = r
    g = out["runs"]["run0_geo_check"]["devices"]["D_mid"]
    ratio = {"direct": g["direct_per_bit_day"] / GEO_REF["direct"], "proton": g["proton_per_bit_day"] / GEO_REF["proton"]}
    out["geo_check"] = {"reference": GEO_REF, "D_mid_over_reference": ratio,
                        "pass": all(1 / 3 <= x <= 3 for x in ratio.values()), "criterion": "within a factor of 3"}
    (ROOT / "results" / "seu_rates.json").write_text(json.dumps(out, indent=2))
    print(f"GEO check D_mid/ref: direct {ratio['direct']:.2f}, proton {ratio['proton']:.2f} -> pass={out['geo_check']['pass']}")
    for rid, r in out["runs"].items():
        d = r["devices"]
        print(f"{rid:20s} {r['shielding_mm_al']:4.2f} mm  /bit/day  low {d['D_low']['total_per_bit_day']:.2e}  "
              f"mid {d['D_mid']['total_per_bit_day']:.2e}  high {d['D_high']['total_per_bit_day']:.2e}  L0 {r['L0_echo']}")


if __name__ == "__main__":
    main()
