"""Loader for NASA PCoE Randomized Battery Usage data (RW17-RW20, skewed-high,
room temperature). Source: NASA Open Data Portal, PCoE. Cite: Bole, Kulkarni,
Daigle, "Adaptation of an Electrochemistry-based Li-Ion Battery Model to Account
for Deterioration Observed Under Randomized Use", PHM Society 2014.

Data: data/nasa_rw/RWxx.mat, struct `data.step[]` with fields comment, type,
relativeTime, time, voltage, current, temperature, date.
Sign convention here: current > 0 = discharge (the NASA files use the same).

Functions return plain numpy arrays so the ECM/EKF code is dataset-agnostic.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import scipy.io as sio

DATA = Path(__file__).resolve().parents[1] / "data" / "nasa_rw"


def load(cell: str):
    m = sio.loadmat(DATA / f"{cell}.mat", squeeze_me=True, struct_as_record=False)
    return m["data"].step


def _arr(x):
    return np.atleast_1d(np.asarray(x, dtype=float))


def _date(s):
    return datetime.strptime(s, "%d-%b-%Y %H:%M:%S")


def reference_capacity(steps) -> tuple[np.ndarray, np.ndarray]:
    """Capacity [Ah] from each 1 A reference discharge (4.2 V -> 3.2 V), and
    days since the first one. This is the state-of-health trajectory."""
    days, cap = [], []
    t0 = None
    for s in steps:
        if s.comment == "reference discharge":
            t, i = _arr(s.relativeTime), _arr(s.current)
            d = _date(s.date)
            t0 = t0 or d
            days.append((d - t0).total_seconds() / 86400.0)
            cap.append(np.trapezoid(i, t) / 3600.0)
    return np.asarray(days), np.asarray(cap)


def stitched(steps, idx: list[int]) -> dict:
    """Concatenate consecutive steps into one time series (absolute `time` field)."""
    t = np.concatenate([_arr(steps[k].time) for k in idx])
    v = np.concatenate([_arr(steps[k].voltage) for k in idx])
    i = np.concatenate([_arr(steps[k].current) for k in idx])
    T = np.concatenate([_arr(steps[k].temperature) for k in idx])
    order = np.argsort(t, kind="stable")
    t, v, i, T = t[order], v[order], i[order], T[order]
    keep = np.concatenate([[True], np.diff(t) > 0])
    t, v, i, T = t[keep], v[keep], i[keep], T[keep]
    return {"t": t - t[0], "v": v, "i": i, "temp": T}


def pulsed_load_blocks(steps) -> list[list[int]]:
    """Indices of each pulsed-load characterisation block (1 A for 10 min,
    20 min rest, repeated from full charge). Used to fit the 2RC ECM."""
    blocks, cur = [], []
    names = {"pulsed load (discharge)", "pulsed load (rest)", "rest post pulsed load"}
    for k, s in enumerate(steps):
        if s.comment in names:
            if cur and k != cur[-1] + 1:
                blocks.append(cur)
                cur = []
            cur.append(k)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return [b for b in blocks if len(b) > 4]


def rw_cycles(steps) -> list[list[int]]:
    """Indices of each random-walk discharge cycle (discharge + interleaved rests)."""
    names = {"discharge (random walk)", "rest (random walk)"}
    cyc, cur = [], []
    for k, s in enumerate(steps):
        if s.comment in names:
            if cur and k != cur[-1] + 1:
                cyc.append(cur)
                cur = []
            cur.append(k)
        elif cur:
            cyc.append(cur)
            cur = []
    if cur:
        cyc.append(cur)
    return [c for c in cyc if len(c) > 10]


if __name__ == "__main__":
    import json

    summary = {}
    for cell in ["RW17", "RW18", "RW19", "RW20"]:
        st = load(cell)
        days, cap = reference_capacity(st)
        pb = pulsed_load_blocks(st)
        rw = rw_cycles(st)
        first = stitched(st, rw[0])
        temps = np.concatenate([_arr(s.temperature) for s in st[: min(len(st), 4000)]])
        summary[cell] = {
            "reference_discharges": int(cap.size),
            "capacity_first_Ah": round(float(cap[0]), 3),
            "capacity_last_Ah": round(float(cap[-1]), 3),
            "capacity_retention_pct": round(float(100 * cap[-1] / cap[0]), 1),
            "span_days": round(float(days[-1]), 1),
            "pulsed_load_blocks": len(pb),
            "rw_cycles": len(rw),
            "first_rw_cycle_minutes": round(float(first["t"][-1] / 60), 1),
            "temp_range_C_first4000steps": [round(float(np.nanmin(temps)), 1), round(float(np.nanmax(temps)), 1)],
        }
    print(json.dumps(summary, indent=2))
    (DATA.parents[1] / "results").mkdir(exist_ok=True)
    (DATA.parents[1] / "results" / "nasa_rw_summary.json").write_text(json.dumps(summary, indent=2))
