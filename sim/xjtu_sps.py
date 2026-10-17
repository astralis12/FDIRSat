"""XJTU-SPS loaders (RUNBOOK 4.3): MR, F/R, FD (FL), AD; per-unit conversion;
FMECA label map. Dataset: Di et al., arXiv:2601.12667; https://diyi1999.github.io/XJTU-SPS/

Layout (data/xjtu_sps = the downloaded DATASET folder):
  XJTU-SPS for F or R/  *_{4,18,24,34,90,94}cycles.csv        orbit telemetry, 1 Hz
  XJTU-SPS for MR/      same CSVs (byte-identical data) + *_ModeLabel.csv (work modes 1-6)
  XJTU-SPS for FD/<class>/<class>_{Train,Test}.csv           fault rows only, one folder per class
  XJTU-SPS for AD/      _Train.csv (normal), _Test.csv + _Test_AnomalyLabel.csv
Only CSVs are read. The .pkl copies are never loaded (unpickling runs code).

Facts verified by `python sim/xjtu_sps.py --check` (results/xjtu_sps_ranges.json):
  - U_BAT2..4 are pack voltages of a 4S string (ISS-020): per-unit divides by N_S = 4.
  - Battery current: positive = discharge (paper, App. C), same as this project.
  - Fault ids 0-17 follow fault_dict of the dataset's generation code.
  - AD Test = one normal run followed by fault runs 1..17 in id order; each
    boundary is a time jump > 60 s, so every AD row gets its fault id.
  - Leakage (ISS-028): FD Normal = orbit file 4; the normal prefix of AD Test =
    orbit file 24; AD Train = orbit file 34; every FD fault row is also in AD Test.
    Training orbits therefore exclude 4 and 24, and FD and AD are one validation pool.

Per-unit inputs (CLAUDE.md): V per cell, C-rate, T, SoC, I_SA per-unit, U_bus per-unit.
SoC is not in the data; the environment (4.4) gets it from the onboard EKF.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "xjtu_sps"
FMECA = ROOT / "config" / "fmeca_xjtu_sps.json"

COLUMNS = ["Time", "U_SA", "I_SA", "P_SA", "U_Load_output", "I_Load_output", "P_Load_output",
           "U_BCR", "I_BCR", "P_BCR", "U_BAT2", "I_BAT2", "T_BAT2", "U_BAT3", "I_BAT3", "T_BAT3",
           "U_BAT4", "I_BAT4", "T_BAT4", "U_Bus", "I_Bus", "P_Bus", "U_Load1", "I_Load1", "T_Load1",
           "P_Load1", "U_Load2", "I_Load2", "T_Load2", "P_Load2", "U_Load3", "I_Load3", "T_Load3", "P_Load3"]
BATTERIES = (2, 3, 4)
FAULT_CLASSES = {0: "Normal", 1: "SA_partial component or branch open circuit",
                 2: "SA_partial component or branch short circuit", 3: "SA_component deterioration",
                 4: "BCR_open circuit", 5: "BCR_short circuit", 6: "BCR_increased power loss",
                 7: "BAT_degradation", 8: "BAT_open circuit", 9: "Bus_open circuit", 10: "Bus_short circuit",
                 11: "Bus_insulation breakdown", 12: "PDM1_open circuit or short circuit",
                 13: "PDM2_open circuit or short circuit", 14: "PDM3_open circuit or short circuit",
                 15: "Load1_open circuit", 16: "Load2_open circuit", 17: "Load3_open circuit"}
ORBIT_FILES = (4, 18, 24, 34, 90, 94)
TRAIN_ORBITS = (18, 34, 90, 94)        # 4 and 24 hold FD / AD normal data (ISS-028)
ORBIT_PERIOD_S = 95 * 60               # 60 min sunlit + 35 min shadow (project page)

# Pack and per-unit bases
N_S = 4                                # cells in series per battery group (ISS-020)
V_CELL_MAX = 4.2                       # NCM full-charge voltage; U_bus base = N_S * V_CELL_MAX
Q_STRING_AH = 2.0                      # ASSUMED: not published; = FDIR_CELL_CAPACITY_AH (1P). See --check
AD_JUMP_S = 60.0                       # AD Test segment boundary


def _csv(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path)
    if list(d.columns) != COLUMNS:
        raise ValueError(f"{path.name}: unexpected columns")
    d["Time"] = pd.to_datetime(d["Time"])
    d.insert(1, "t_s", (d["Time"] - d["Time"].iloc[0]).dt.total_seconds())
    return d


# ---------------------------------------------------------------- loaders
def load_orbits(cycles: int, modes: bool = True, data: Path = DATA) -> pd.DataFrame:
    """One orbit file (F/R telemetry), optionally with the MR work-mode label."""
    if cycles not in ORBIT_FILES:
        raise ValueError(f"no orbit file with {cycles} cycles")
    d = _csv(data / "XJTU-SPS for F or R" / f"XJTU-SPS for F or R_{cycles}cycles.csv")
    if modes:
        m = pd.read_csv(data / "XJTU-SPS for MR" / f"XJTU-SPS for MR_{cycles}cycles_ModeLabel.csv")
        if len(m) != len(d) or not (pd.to_datetime(m["Time"]).values == d["Time"].values).all():
            raise ValueError(f"mode labels not aligned with orbit file {cycles}")
        d["mode"] = m["ModeLabel"].astype(int).values
    d["orbit_file"] = cycles
    return d


def load_training_orbits(cycles=TRAIN_ORBITS, data: Path = DATA) -> pd.DataFrame:
    """Orbit files allowed for training. Refuses the files that overlap FD/AD."""
    bad = set(cycles) - set(TRAIN_ORBITS)
    if bad:
        raise ValueError(f"orbit files {sorted(bad)} overlap the FD/AD validation data (ISS-028)")
    return pd.concat([load_orbits(c, data=data) for c in cycles], ignore_index=True)


def load_fd(split: str, classes=None, data: Path = DATA) -> pd.DataFrame:
    """FD (fault localisation) rows of the given split ('Train' or 'Test') with fault_id."""
    if split not in ("Train", "Test"):
        raise ValueError("split is 'Train' or 'Test'")
    out = []
    for k in (classes if classes is not None else FAULT_CLASSES):
        name = FAULT_CLASSES[k]
        d = _csv(data / "XJTU-SPS for FD" / name / f"{name}_{split}.csv")
        d["fault_id"] = k
        out.append(d)
    return pd.concat(out, ignore_index=True)


def load_ad(split: str, data: Path = DATA) -> pd.DataFrame:
    """AD rows. Test has 'anomaly' (0/1, from the dataset) and 'fault_id', recovered
    from the segment order: segment 0 is normal, segment k is fault run k."""
    base = data / "XJTU-SPS for AD"
    if split == "Train":
        d = _csv(base / "XJTU-SPS for AD_Train.csv")
        d["anomaly"], d["fault_id"], d["segment"] = 0, 0, 0
        return d
    if split != "Test":
        raise ValueError("split is 'Train' or 'Test'")
    d = _csv(base / "XJTU-SPS for AD_Test.csv")
    lab = pd.read_csv(base / "XJTU-SPS for AD_Test_AnomalyLabel.csv")
    if len(lab) != len(d) or not (pd.to_datetime(lab["Time"]).values == d["Time"].values).all():
        raise ValueError("AD labels not aligned")
    jump = np.abs(np.diff(d["Time"].values.astype("datetime64[s]").astype(np.int64))) > AD_JUMP_S
    seg = np.concatenate([[0], np.cumsum(jump)])
    if seg.max() != len(FAULT_CLASSES) - 1:
        raise ValueError(f"AD Test has {seg.max() + 1} segments, expected {len(FAULT_CLASSES)}")
    d["anomaly"] = lab["AnomalyLabel"].astype(int).values
    if d.loc[seg == 0, "anomaly"].any():
        raise ValueError("AD normal segment contains anomaly labels")
    d["segment"] = seg
    d["fault_id"] = np.where(d["anomaly"] == 1, seg, 0)
    return d


# ---------------------------------------------------------------- per-unit
@lru_cache(maxsize=1)
def i_sa_base(data: Path = DATA) -> float:
    """I_SA per-unit base: 99.9th percentile of I_SA over the training orbits
    (no published array rating; training data only, so no validation leakage)."""
    return float(np.percentile(load_training_orbits(data=data)["I_SA"], 99.9))


def per_unit(d: pd.DataFrame, bat: int | str = 2, data: Path = DATA) -> pd.DataFrame:
    """The measured per-unit inputs. bat = 2, 3, 4 or 'all' (one row per group,
    stacked, with a 'bat' column)."""
    if bat == "all":
        return pd.concat([per_unit(d, b, data).assign(bat=b) for b in BATTERIES], ignore_index=True)
    if bat not in BATTERIES:
        raise ValueError("bat is 2, 3, 4 or 'all'")
    keep = [c for c in ("Time", "t_s", "mode", "fault_id", "anomaly", "segment", "orbit_file") if c in d]
    out = d[keep].copy()
    out["v_cell"] = d[f"U_BAT{bat}"] / N_S
    out["c_rate"] = d[f"I_BAT{bat}"] / Q_STRING_AH          # > 0 = discharge
    out["t_bat"] = d[f"T_BAT{bat}"]
    out["i_sa_pu"] = d["I_SA"] / i_sa_base(data)
    out["u_bus_pu"] = d["U_Bus"] / (N_S * V_CELL_MAX)
    return out


def fmeca_map(path: Path = FMECA) -> dict:
    """FMECA label map (config/fmeca_xjtu_sps.json), keyed by integer fault id."""
    m = json.load(open(path, encoding="utf-8"))
    return {int(k): v for k, v in m["fault_classes"].items()}


def severity(fault_id, path: Path = FMECA):
    """Proposed ECSS severity category per row (NaN for normal). Accepts arrays."""
    sev = {k: (np.nan if v["severity"] is None else v["severity"]) for k, v in fmeca_map(path).items()}
    return np.vectorize(sev.get, otypes=[float])(np.asarray(fault_id))


# ---------------------------------------------------------------- check (gate)
def _row_hash(d: pd.DataFrame) -> np.ndarray:
    return pd.util.hash_pandas_object(d[COLUMNS[1:]].round(6), index=False).values


def _ranges(d: pd.DataFrame) -> dict:
    return {c: [float(d[c].min()), float(d[c].max())] for c in COLUMNS[1:]}


def check(data: Path = DATA) -> dict:
    p = Path(data).absolute()           # not resolve(): data/xjtu_sps may be a junction
    rel = p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else p.name
    res, ok = {"dataset": rel, "files": {}, "checks": {}}, {}
    orbits = {c: load_orbits(c, data=data) for c in ORBIT_FILES}
    fd = {s: load_fd(s, data=data) for s in ("Train", "Test")}
    ad = {s: load_ad(s, data=data) for s in ("Train", "Test")}
    frames = {**{f"orbits_{c}": d for c, d in orbits.items()}, "fd_train": fd["Train"], "fd_test": fd["Test"],
              "ad_train": ad["Train"], "ad_test": ad["Test"]}
    for name, d in frames.items():
        dt = np.diff(d["Time"].values.astype("datetime64[s]").astype(np.int64))
        res["files"][name] = {"rows": int(len(d)), "nan": int(d[COLUMNS[1:]].isna().sum().sum()),
                              "dt_1s_frac": float(np.mean(dt == 1)), "dt_0_count": int(np.sum(dt == 0)),
                              "ranges": _ranges(d)}
    ok["C1 no NaN in any file"] = all(f["nan"] == 0 for f in res["files"].values())

    # per-cell voltage in normal orbits; battery current against the published window
    nrm = pd.concat(orbits.values(), ignore_index=True)
    u = np.concatenate([nrm[f"U_BAT{b}"].values for b in BATTERIES]) / N_S
    i = np.concatenate([nrm[f"I_BAT{b}"].values for b in BATTERIES])
    res["per_cell_V_normal"] = [float(u.min()), float(u.max())]
    ok["C2 per-cell V in [2.5, 4.25] V (normal orbits, 4S)"] = bool(u.min() >= 2.5 and u.max() <= 4.25)
    res["i_bat_normal_A"] = [float(i.min()), float(i.max())]
    ok["C3 I_BAT within paper window [-0.7, 0.5] A (normal orbits)"] = bool(i.min() >= -0.7 and i.max() <= 0.5)
    ok["C4 U_SA within paper range [0, 32] V"] = bool(nrm["U_SA"].min() >= 0 and nrm["U_SA"].max() <= 32)
    ok["C5 I_BCR within paper range [0, 3.5] A"] = bool(nrm["I_BCR"].min() >= 0 and nrm["I_BCR"].max() <= 3.5)

    # temperatures: plausibility (same idea as the SGO v2 gate)
    temps = [c for c in COLUMNS if c.startswith("T_")]
    implaus = {}
    for name, d in frames.items():
        for c in temps:
            bad = int(((d[c] < -40) | (d[c] > 100)).sum())
            if bad:
                implaus[f"{name}.{c}"] = {"samples": bad, "max": float(d[c].max())}
    res["implausible_temperatures"] = implaus
    ok["C6 battery temperatures plausible everywhere"] = not any(k.split(".")[1].startswith("T_BAT") for k in implaus)

    # orbit period from sunlit/shadow transitions (U_SA > 1 V, paper sec. 2.1)
    d94 = orbits[94]
    sun = (d94["U_SA"] > 1.0).astype(int).values
    rise = d94["t_s"].values[1:][np.diff(sun) == 1]
    per = np.diff(rise)
    res["orbit_period_s"] = {"median": float(np.median(per)), "n": int(per.size)}
    ok["C7 orbit period 95 min (+-1 min)"] = bool(abs(np.median(per) - ORBIT_PERIOD_S) <= 60)

    # sign convention by work mode: fast charge < 0, discharge > 0
    by_mode = nrm.groupby("mode")[[f"I_BAT{b}" for b in BATTERIES]].mean().mean(axis=1)
    res["i_bat_mean_by_mode_A"] = {int(k): float(v) for k, v in by_mode.items()}
    ok["C8 sign: discharge mode I_BAT > 0, fast-charge mode < 0"] = bool(by_mode[6] > 0 and by_mode[1] < 0)

    # AD segment -> fault id mapping: FD class k rows must lie in AD segment k
    adh = _row_hash(ad["Test"])
    seg_of = dict(zip(adh.tolist(), ad["Test"]["segment"].tolist()))
    mapping = {}
    for k in range(1, len(FAULT_CLASSES)):
        h = _row_hash(pd.concat([fd["Train"], fd["Test"]]).query("fault_id == @k"))
        segs = pd.Series([seg_of.get(x, -1) for x in h.tolist()]).value_counts()
        mapping[k] = {"fd_rows": int(h.size), "in_segment_k": int(segs.get(k, 0))}
    res["ad_fault_mapping"] = mapping
    ok["C9 AD segment k holds every FD row of class k"] = all(m["in_segment_k"] == m["fd_rows"] for m in mapping.values())

    # leakage between training orbits and validation data
    train_h = set(np.concatenate([_row_hash(orbits[c]) for c in TRAIN_ORBITS]).tolist())
    excl_h = {c: set(_row_hash(orbits[c]).tolist()) for c in ORBIT_FILES if c not in TRAIN_ORBITS}
    def frac(h, s):
        return float(np.mean([x in s for x in h.tolist()]))
    val = {"fd_all": _row_hash(pd.concat([fd["Train"], fd["Test"]])), "ad_test": adh}
    res["overlap"] = {
        "train_orbits_vs": {k: frac(h, train_h) for k, h in val.items()},
        "excluded_orbits_vs": {f"orbits_{c}": {k: frac(h, s) for k, h in val.items()} for c, s in excl_h.items()},
        "ad_train_in_orbits_34": frac(_row_hash(ad["Train"]), set(_row_hash(orbits[34]).tolist())),
        "fd_faults_in_ad_test": frac(_row_hash(pd.concat([fd["Train"], fd["Test"]]).query("fault_id > 0")), set(adh.tolist())),
    }
    ok["C10 training orbits share no row with FD or AD Test"] = all(v == 0 for v in res["overlap"]["train_orbits_vs"].values())

    # per-unit ranges (training orbits, all groups) against the SGO v2 plausibility band
    pu = per_unit(load_training_orbits(data=data), "all", data)
    res["per_unit_ranges_train"] = {c: [float(pu[c].min()), float(pu[c].max())]
                                    for c in ("v_cell", "c_rate", "t_bat", "i_sa_pu", "u_bus_pu")}
    res["per_unit_bases"] = {"N_S": N_S, "V_CELL_MAX": V_CELL_MAX, "Q_STRING_AH_assumed": Q_STRING_AH,
                             "I_SA_base_A": i_sa_base(data), "U_bus_base_V": N_S * V_CELL_MAX}
    ok["C11 per-unit v_cell in SGO plausibility band [0.5, 4.5]"] = bool(pu["v_cell"].between(0.5, 4.5).all())

    # capacity assumption: charge per eclipse vs the per-cell voltage swing
    shadow = d94["U_SA"] <= 1.0
    eclipse_ah = []
    for b in BATTERIES:
        q = (d94[f"I_BAT{b}"].clip(lower=0) * shadow).values
        orbit = (d94["t_s"].values // ORBIT_PERIOD_S).astype(int)
        eclipse_ah.append(pd.Series(q).groupby(orbit).sum().values[1:-1] / 3600.0)
    eclipse_ah = np.concatenate(eclipse_ah)
    res["capacity_check"] = {"eclipse_discharge_Ah_median": float(np.median(eclipse_ah)),
                             "depth_of_discharge_if_Q_assumed": float(np.median(eclipse_ah) / Q_STRING_AH),
                             "note": "DoD per eclipse at the assumed capacity; LEO practice is roughly 10-30 %"}
    res["fmeca_map_sha256"] = hashlib.sha256(FMECA.read_bytes()).hexdigest()[:16]
    m = fmeca_map()
    ok["C12 FMECA map covers every fault id"] = sorted(m) == sorted(FAULT_CLASSES)

    # data-derived detection signature per fault class (FD Train vs FD Normal Train)
    tr = fd["Train"]
    ref = tr[tr.fault_id == 0][COLUMNS[1:]]
    mu, sd = ref.mean(), ref.std().replace(0, np.nan)
    res["fault_signature_top3"] = {}
    for k in range(1, len(FAULT_CLASSES)):
        z = ((tr[tr.fault_id == k][COLUMNS[1:]].mean() - mu) / sd).abs().dropna().sort_values(ascending=False)
        res["fault_signature_top3"][k] = {c: round(float(v), 2) for c, v in z.head(3).items()}

    # temperature confound (ISS-029): each recording sits at its own battery temperature
    res["t_bat2_mean_by_fd_class"] = {int(k): float(v) for k, v in tr.groupby("fault_id")["T_BAT2"].mean().items()}
    res["t_bat2_mean_by_orbit_file"] = {int(c): float(d["T_BAT2"].mean()) for c, d in orbits.items()}
    res["t_bat2_std_within_recording_median"] = float(np.median(
        list(tr.groupby("fault_id")["T_BAT2"].std().values) + [d["T_BAT2"].std() for d in orbits.values()]))

    # C3 compares with a window quoted in the paper text ("-0.7 to +0.5 A"), not a
    # rating; it is reported, not gated (decided after the first run, ISS-029).
    reported = {"C3 I_BAT within paper window [-0.7, 0.5] A (normal orbits)"}
    res["i_bat_outside_paper_window"] = {
        f"I_BAT{b}": int(((nrm[f"I_BAT{b}"] < -0.7) | (nrm[f"I_BAT{b}"] > 0.5)).sum()) for b in BATTERIES}
    res["checks"] = ok
    res["reported_not_gated"] = sorted(reported)
    gated = {k: v for k, v in ok.items() if k not in reported}
    res["gate"] = {"metric": "column-range and consistency checks (C3 reported only)", "pass": all(gated.values()),
                   "failed": [k for k, v in gated.items() if not v]}
    # kept for the ISS-020 evidence: the 94-orbit battery and bus columns
    res["columns_94"] = {c: {"min": float(d94[c].min()), "median": float(d94[c].median()), "max": float(d94[c].max())}
                         for c in ("U_BAT2", "I_BAT2", "T_BAT2", "U_BAT3", "I_BAT3", "T_BAT3",
                                   "U_BAT4", "I_BAT4", "T_BAT4", "U_Bus", "I_Bus")}
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify ranges and consistency, write results")
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--out", default=str(ROOT / "results" / "xjtu_sps_ranges.json"))
    a = ap.parse_args()
    if not a.check:
        ap.print_help()
        return
    res = check(Path(a.data))
    Path(a.out).write_text(json.dumps(res, indent=2))
    for k, v in res["checks"].items():
        print(("PASS " if v else "FAIL ") + k)
    print("GATE", res["gate"])


if __name__ == "__main__":
    main()
