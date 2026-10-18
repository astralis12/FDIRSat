"""Report sections for the 17-18 Oct work list (items 1-6). Every number is read from
results/*.json; the 17 Oct 6-input / SGO v2 results are in results/tree6/."""
from __future__ import annotations

import html

esc = lambda s: html.escape(str(s))


def scatter(points, width=640, height=300, xmax=None, ymin=0.0):
    """points: [(label, x_false_action, y_coverage, css_class)]; one scale per axis."""
    pts = [p for p in points if p[1] is not None and p[2] is not None]
    if not pts:
        return '<p class="pending">pending</p>'
    left, right, top, bottom = 56, 150, 14, 36
    pw, ph = width - left - right, height - top - bottom
    xmax = xmax or max(p[1] for p in pts) * 1.1 or 0.01
    sx = lambda v: left + pw * v / xmax
    sy = lambda v: top + ph * (1 - (v - ymin) / (1 - ymin))
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" class="chart">']
    for k in range(5):
        v = ymin + (1 - ymin) * k / 4
        out.append(f'<line x1="{left}" y1="{sy(v):.1f}" x2="{left + pw}" y2="{sy(v):.1f}" class="grid"/>')
        out.append(f'<text x="{left - 6}" y="{sy(v) + 4:.1f}" class="tick" text-anchor="end">{100 * v:.0f} %</text>')
        xv = xmax * k / 4
        out.append(f'<text x="{sx(xv):.1f}" y="{height - 18}" class="tick" text-anchor="middle">{100 * xv:.1f} %</text>')
    out.append(f'<text x="{left + pw / 2:.1f}" y="{height - 2}" class="tick" text-anchor="middle">false-action rate (share of fault-free steps with an action)</text>')
    for i, (lab, x, y, cls) in enumerate(sorted(pts, key=lambda p: p[1])):
        out.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="4.5" class="dot {cls}"/>')
        out.append(f'<text x="{sx(x) + 7:.1f}" y="{sy(y) + 4 + (i % 2) * 10 - 5:.1f}" class="lab {cls}-t">{esc(lab)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def gates(load, cbmc):
    fi, ts, fb, rb, cm = load("fault_injection.json"), load("timing_sweep.json"), load("features_bitexact.json"), load("residual_risk_band.json"), load("compare_methods.json")
    c = lambda t: (f"{cbmc[t]['failed']} of {cbmc[t]['total']} failed" if t in cbmc and cbmc[t]["total"] else None, cbmc.get(t, {}).get("ok"))
    return [
        ("1", "SGO v3, overvoltage invariant (P10)", "proof success", *c("v3_worstcase")),
        ("2", "History features, ring-buffer proof", "proof success", *c("features")),
        ("2", "History features, C vs torch", "0 bit mismatches", fb["bit_mismatches"] if fb else None, fb["gate"]["pass"] if fb else None),
        ("5", "Tree evaluator, any table contents", "proof success", *c("tree_any")),
        ("3/4", "Baselines, curve, metric fixes", "tables filled", "3 orbits" if cm else None, True if cm else None),
        ("5", "Fault injection on the STM32F411", "0 SDC in ≥ 3000 injections", (f"{fi['summary']['sdc_gated']} SDC / {fi['summary']['injections_gated']}" if fi and "summary" in fi else None), fi["gate"]["pass"] if fi and "gate" in fi else None),
        ("5", "Timing and size sweep", "16–100 MHz measured", f"{len(ts['configs'])} configs" if ts else None, True if ts else None),
        ("6", "Residual-risk band, ranking", "band + rank stability", "3 orbits" if rb else None, True if rb else None),
    ]


def sections(load, fmeca):
    S = []
    # ---- item 1: SGO v3
    v2, v3, hc = load("tree6/residual_risk_sgo_v2.json"), load("residual_risk_sgo_v3.json"), load("hil_cases.json")
    if v2 and v3:
        rows = []
        for o in v3["orbits"]:
            a, b = v2["orbits"][o]["per_fault"]["5"], v3["orbits"][o]["per_fault"]["5"]
            n2, n3 = v2["orbits"][o]["per_fault"]["0"], v3["orbits"][o]["per_fault"]["0"]
            rows.append([esc(o), f"{100 * b['hazard_no_fdir']:.0f} %", f"{100 * a['hazard_deployed']:.1f} %", f"{100 * b['hazard_deployed']:.1f} %",
                         f"{100 * b['hazard_expert']:.1f} %", f"{100 * n2['hazard_deployed']:.1f} % → {100 * n3['hazard_deployed']:.1f} %",
                         f"{100 * n2['false_action_rate_before_onset']:.1f} % → {100 * n3['false_action_rate_before_onset']:.1f} %"])
        body = """<p>SGO v3 adds invariant 5 (ADR-0018): pack-average cell voltage above 4.20 V isolates the array and limits charge.
        Same 6-input tree, same scenarios; only the safety layer differs.</p>""" + _table(
            ["Orbit", "BCR short: no FDIR", "SGO v2", "SGO v3", "expert", "fault-free hazard v2 → v3", "false-action rate v2 → v3"], rows)
        if hc and "C2_sgo_v3_bcr_short" in hc:
            r = hc["C2_sgo_v3_bcr_short"]["rows"]["C2_f5"]
            body += f"""<p>On the STM32F411 (BCR short, {r['runs']} closed-loop runs): hazard in {r['hazard_pil']} of {r['runs']} runs on the board
            and {r['hazard_sil']} in software, action agreement {100 * r['agreement_mean']:.1f} %, overvoltage flag 0x{r['flags_pil']:02x} raised by the board.</p>"""
        if hc and "C1_sgo_v3" in hc:
            r = hc["C1_sgo_v3"]["rows"]["C1"]
            body += f"<p>Nominal orbits with v3 on the board: {r['hazard_pil']} hazards in {r['runs']} runs, flags 0x{r['flags_pil']:02x}.</p>"
        S.append(("v3", "1 · SGO v3", body))
    # ---- item 2: history
    fb, cm = load("features_bitexact.json"), load("compare_methods.json")
    body = """<p>Three physical features join the six inputs (ADR-0019): dv60 (cell-voltage change over 60 s), i_sa_sun (array current
    averaged over the sunlit phase, held in eclipse) and dsoc600 (SoC change over 10 min). The C ring buffers carry a CBMC bounds proof.</p>"""
    if fb:
        body += f"<p>C vs Python twin: {fb['steps']} steps with {fb['corrupted_samples']} corrupted samples, {fb['bit_mismatches']} bit mismatches.</p>"
    if cm:
        rows = []
        for o, d in cm["orbits"].items():
            a, b = d.get("tree6"), d.get("deployed")
            if a and b:
                rows.append([esc(o), f"{100 * a['coverage_mitigable_mean']:.1f} % → {100 * b['coverage_mitigable_mean']:.1f} %",
                             f"{100 * a['false_action_rate_mean']:.1f} % → {100 * b['false_action_rate_mean']:.1f} %",
                             f"{a['alarm_precision']:.2f} → {b['alarm_precision']:.2f}"])
        body += "<h4>6 inputs → 9 inputs (both with SGO v3)</h4>" + _table(["Orbit", "coverage (mitigable)", "false-action rate", "alarm precision"], rows)
        sa = [str(f) for f in (1, 2, 3, 7)]
        rows = []
        for f in sa:
            a = cm["orbits"]["xjtu"]["tree6"]["per_fault"][f]["coverage_mitigable"]
            b = cm["orbits"]["xjtu"]["deployed"]["per_fault"][f]["coverage_mitigable"]
            rows.append([esc(fmeca[f]["name"]), f"{100 * a:.0f} % → {100 * b:.0f} %"])
        body += "<h4>Modes the history targets (XJTU orbit)</h4>" + _table(["FMECA mode", "coverage 6 → 9 inputs"], rows)
    S.append(("hist", "2 · Input history", body))
    # ---- items 3 and 4
    if cm:
        name = {"deployed": "tree (9 in) + SGO v3", "tree6": "tree (6 in) + SGO v3", "M1": "M1 thresholds", "M2": "M2 hysteresis",
                "M6": "M6 SGO only", "expert": "expert", "none": "no FDIR"}
        o = cm["orbits"]["xjtu"]
        pts = []
        for k, v in o.items():
            cls = "s1" if k == "deployed" else ("s2" if k.startswith("nofm") else "s0")
            lab = name.get(k, k.replace("nofm_", "").replace("_", "-of-"))
            pts.append((lab, v["false_action_rate_mean"], v["coverage_mitigable_mean"], cls))
        body = """<p>All methods on the same scenarios (XJTU orbit shown). Coverage is over mitigable episodes; episodes where even the
        expert cannot avoid the hazard form their own class. N-of-M points: the tree's action must appear in N of the last M steps.</p>""" + scatter(pts)
        rows = [[esc(name.get(k, k.replace("nofm_", "").replace("_", "-of-") + " filter")), f"{100 * v['coverage_mitigable_mean']:.1f} %",
                 f"{100 * v['false_action_rate_mean']:.2f} %", (f"{v['alarm_precision']:.2f}" if v["alarm_precision"] is not None else "–"),
                 f"{100 * v['hazard_mean_faults']:.1f} %", f"{v['residual_risk_relative']:.2e}"] for k, v in o.items()]
        body += _table(["Method", "coverage (mitigable)", "false-action rate", "alarm precision", "hazard (faults)", "relative risk"], rows)
        um = [[esc(fmeca[f]["name"]), f"{100 * o['deployed']['per_fault'][f]['not_mitigable_share']:.0f} %"]
              for f in map(str, range(1, 18)) if o["deployed"]["per_fault"][f]["not_mitigable_share"] > 0.05]
        body += "<h4>Not mitigable by FDIR (hazard also under the expert)</h4>" + _table(["FMECA mode", "share of episodes"], um)
        gs = load("dagger_summary.json")
        if gs and "sw_dagger" in gs.get("modes", {}):
            m = gs["modes"]["sw_dagger"]
            a, f = m.get("adversary_failure_clusters"), m.get("fuzzing_failure_clusters")
            if a and a.get("mean") is not None:
                body += f"""<h4>Adversary vs fuzzing, distinct failure clusters</h4><p>Raw failing scenarios: adversary {m['adversary_failures_found']},
                fuzzing {m['fuzzing_failures_found']}. Distinct clusters (fault × onset decile × magnitude × initial SoC) per seed: adversary
                {a['mean']:.0f} ± {a['half_width'] or 0:.0f}, fuzzing {f['mean']:.0f} ± {f['half_width'] or 0:.0f}; found only by the adversary
                {m['clusters_only_adversary']['mean']:.0f}, only by fuzzing {m['clusters_only_fuzzing']['mean']:.0f}.</p>"""
        S.append(("base", "3–4 · Baselines and metrics", body))
    # ---- item 5
    fi, ts = load("fault_injection.json"), load("timing_sweep.json")
    if fi and "regions" in fi:
        rows = [[esc(k), sum(v.values()), v["detected"], v["masked"], v["latent"], v["sdc"]] for k, v in fi["regions"].items()]
        body = f"""<p>One bit flip per run, written into the running STM32F411's RAM over ST-Link between two frames, then
        {fi['frames_after_injection']} frames compared with a golden run. Tree tables live in RAM with a CRC scrub every frame;
        tree_noscrub is the same target with the scrub switched off (ablation, not gated).</p>""" + _table(["Region", "injections", "detected", "masked", "latent", "SDC"], rows)
        if "summary" in fi:
            s = fi["summary"]
            body += f"""<p>{s['sdc_gated']} silent data corruptions in {s['injections_gated']} gated injections
            {f"(95 % upper bound {s['sdc_rate_upper95_if_zero']:.1e} per upset)" if s['sdc_rate_upper95_if_zero'] else ''}.</p>"""
        S.append(("fi", "5 · Fault-injection campaign", body))
    if ts:
        rows = [[f"{c['f_cpu_mhz']} MHz", c["flash_wait_states"], "on" if c["art_accelerator"] else "off", c["step_cycles_max"],
                 f"{c['step_us_max']:.1f} µs", f"{c['scrub_us_max']:.0f} µs", f"{c['flash_bytes'] / 1024:.1f} KB", f"{c['ram_bytes'] / 1024:.1f} KB"]
                for c in ts["configs"].values()]
        body = f"""<p>The same firmware at each clock; flash wait states per {esc(ts['source_wait_states'])}. One step = EKF + history +
        tree + SGO v3 for one spacecraft. Power: {esc(ts['power'])}.</p>""" + _table(
            ["Clock", "wait states", "ART", "cycles / step", "time / step", "CRC scrub", "flash", "RAM"], rows)
        S.append(("swap", "5 · Timing and size", body))
    # ---- item 6
    rb, tt = load("residual_risk_band.json"), load("threshold_table.json")
    if rb:
        rows = [[esc(o), f"{d['residual_risk_band']['p5']:.1e}", f"{d['residual_risk_band']['p50']:.1e}", f"{d['residual_risk_band']['p95']:.1e}",
                 f"{d['not_mitigable_band']['p50']:.1e}", f"{d['kendall_tau_vs_nominal']['median']:.2f}", f"{100 * d['top3_unchanged_share']:.0f} %",
                 esc(", ".join(d["nominal_top3"]))] for o, d in rb["orbits"].items()]
        body = f"""<p>Rates from three layers (config/fmeca_rates.json: CubeSat Weibull prior × EPS share, split by part counts,
        SEU rate × sensitive bits × measured SDC fraction), each varied ±1 decade log-uniform in {rb['draws']} draws.
        λ_EPS = {rb['lambda_eps_per_h']:.2e} per hour.</p>""" + _table(
            ["Orbit", "risk p5", "p50", "p95", "not mitigable p50", "Kendall τ (median)", "top 3 unchanged", "nominal top 3"], rows)
        S.append(("band", "6 · Residual-risk band", body))
    if tt:
        rows = [[esc(r["threshold"]), esc(r["value"]), esc(r["unit"]), esc(r["source"]),
                 esc("–" if r["margin"] is None else f"{r['margin']:.2f} ({r['margin_vs']})"), esc(r["cbmc"])] for r in tt["rows"]]
        body = _table(["Threshold", "value", "unit", "source", "margin", "CBMC"], rows) + \
            f"<p class=\"note\">Pending: {esc('; '.join(tt['pending']))}.</p>"
        S.append(("thr", "6 · Threshold traceability", body))
    return S


def _table(head, rows):
    th = "".join(f"<th>{esc(h)}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'
