"""SPENVIS orbit inputs (item 6, ISS-048): altitude, inclination, LTAN, start date per sim orbit.

The simulator only fixes period and eclipse (sim/env_v2.py ORBITS). This script turns them into
the circular-orbit inputs SPENVIS asks for, so the SEU rate is computed for the same orbits:
  altitude    from the period (Kepler, circular)
  inclination sun-synchronous condition (J2 nodal precession = 360 deg / 365.2422 d)
  LTAN        chosen so the beta angle reproduces the simulated eclipse (checked below)
  start date  ASSUMED mission start (solar activity phase), same for all orbits
Writes results/spenvis_inputs.json.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.env_v2 import ORBITS  # noqa: E402

MU = 398600.4418      # km^3/s^2
RE = 6378.137         # km
J2 = 1.08262668e-3
SSO_RATE = 2 * math.pi / (365.2422 * 86400)   # rad/s
START = "2027-01-01"  # ASSUMED mission start (declining phase of solar cycle 25)
# LTAN per orbit: xjtu and o2 have ~37 % eclipse (beta near 0), o3 is dawn-dusk
LTAN = {"xjtu": "12:00", "o2_sso_1030": "10:30", "o3_dawn_dusk": "06:00"}


def sso_incl(a: float) -> float:
    n = math.sqrt(MU / a ** 3)
    c = -SSO_RATE / (1.5 * n * J2 * (RE / a) ** 2)
    return math.degrees(math.acos(c))


def eclipse_min(a: float, beta_deg: float) -> float:
    """Cylindrical-shadow eclipse duration of a circular orbit at beta angle beta."""
    h = a - RE
    x = math.sqrt(h * h + 2 * RE * h) / (a * math.cos(math.radians(beta_deg)))
    if x >= 1:
        return 0.0
    return math.degrees(math.acos(x)) / 180.0 * 2 * math.pi * math.sqrt(a ** 3 / MU) / 60


def main() -> None:
    rows = []
    for k, o in ORBITS.items():
        a = (MU * o.period_s ** 2 / (4 * math.pi ** 2)) ** (1 / 3)
        rows.append({"orbit": k, "period_min": o.period_s / 60, "eclipse_min_sim": o.eclipse_s / 60,
                     "altitude_km": round(a - RE, 1), "inclination_deg": round(sso_incl(a), 2),
                     "ltan": LTAN[k], "start_date": START, "eccentricity": 0.0,
                     "eclipse_min_beta0": round(eclipse_min(a, 0.0), 1),
                     "note": "period and eclipse ASSUMED in sim/env_v2.py; altitude/inclination derived"})
    out = {"rows": rows, "spenvis_models": "trapped AP-8/AE-8 (or AP-9) solar max/min per start date, "
           "CREME96 GCR + solar particles, 100 mil Al shielding, STM32F411 SRAM cross-section (PENDING)",
           "start_date_source": "ASSUMED", "constants": {"mu": MU, "re": RE, "j2": J2}}
    (ROOT / "results" / "spenvis_inputs.json").write_text(json.dumps(out, indent=2))
    for r in rows:
        print(f"{r['orbit']:14s} alt {r['altitude_km']:6.1f} km  incl {r['inclination_deg']:6.2f} deg  "
              f"LTAN {r['ltan']}  start {r['start_date']}  e=0  "
              f"(eclipse sim {r['eclipse_min_sim']:.1f} min, beta=0 max {r['eclipse_min_beta0']} min)")


if __name__ == "__main__":
    main()
