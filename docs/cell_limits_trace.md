# Cell limits trace — LG INR21700-M50 (reference) and Sony/Murata US18650VTC6 (bench)

Built 18 Oct 2026 for SGO v3.2 (ISS-059, owner approval). Every datasheet limit, where the
project enforces it, and the gap it had before v3.2. "Closed by" names the v3.2 change.

Sources
- **M50**: LG Chem Product Specification LRB-PS-CY18.2Wh-M50, 2017-09-22, Rev 0 "Tentative"
  (`docs/datasheets/LG_INR21700-M50_product_spec_2017-09-22.pdf`). 1C = 4,850 mA (spec 4.2.7 note: 0.3C = 1,455 mA).
- **VTC6**: Sony Technical Information Rev 0.1 (20 Jan 2015, owner's copy) + Murata SDS / product
  datasheet values supplied by the owner on 18 Oct 2026 (`docs/owner_inputs_2026-10-18.md`).

Enforcement layers: **SGO** = proven safety layer (`fdir_sgo_v3.c`); **BCR** = simulated charger
(`sim/env_v2.py`); **HAZ** = plant hazard used in every metric; **BENCH** = bench procedure (CLAUDE.md);
**BMS** = independent hardware BMS on the bench pack.

## LG INR21700-M50 (reference config, `fdir_config_ref.h`)

| # | Limit | Datasheet value | Clause | Before v3.2 | Gap | Closed by (v3.2) |
|---|---|---|---|---|---|---|
| M1 | Max charge voltage | 4.20 V | 2.5 | SGO inv 5 at 4.20 V (pack average, P10); BCR CV at 4.20 V per cell; HAZ overcharge > 4.25 V | Average only (one cell can be higher) | Open: cell taps (ISS-047, ADR-0018 part 2) |
| M2 | Discharge cut-off | 2.50 V | 2.6, 4.1.2 | SGO inv 1 at 3.0 V/cell (P3) | none (0.5 V margin) | – |
| M3 | Charge temperature | 0..45 °C | 2.7 | SGO inv 6 (P11), HAZ `charge_temp` | none | – |
| M4 | Discharge temperature | -20..55 °C (section 5 warning: -20..60) | 2.7 | SGO inv 4 at 55 °C (P6), HAZ `over_temp` 55 °C | Low end -20 °C not enforced | Inv 7 discharge band below -20 °C uses the lowest band; HAZ `out_of_envelope` counts it |
| M5 | Max continuous charge current | 0.3C at 0..25 °C, 0.7C at 25..45 °C | 4.2.7 | BCR 0.5C flat; SGO none | **BCR exceeds 0.3C below 25 °C; no SGO backup** | BCR derated per band; SGO inv 7a (P12) |
| M6 | Max continuous discharge current | 0.5C at -20..10, 3.0C at 10..25, 1.5C at 25..55 °C | 4.2.9 | SGO inv 3 at 1.5C flat (P5) | **1.5C exceeds 0.5C below 10 °C** | SGO inv 7b (P13) |
| M7 | Standard charge | 0.3C CC to 4.20 V, end 50 mA | 2.4, 4.1.1 | BCR CC-CV | none | – |
| M8 | Pulse discharge power | 80 W for 10 s at SoC 80 %, 25 °C | 4.2.8 | none | Not a continuous limit; not modelled | Logged only (pulses are inside inv 7 persistence) |
| M9 | Storage temperature | -20..55 °C (1 month) | 2.8 | none | Not an operating limit | – |

## Sony/Murata US18650VTC6 (bench config, `fdir_config_bench.h`)

| # | Limit | Datasheet value | Source | Before v3.2 | Gap | Closed by (v3.2) |
|---|---|---|---|---|---|---|
| V1 | Charge voltage | 4.2 ± 0.05 V (4.25 V max) | Sony TI Rev 0.1; Murata | SGO inv 5 at 4.25 V; charger CV fixed 4.2 V; BMS | Average only | Open: cell taps (ISS-047) |
| V2 | Discharge cut-off | 2.5 V (rating at 2.0 V) | Sony TI Rev 0.1 | SGO inv 1 at 3.0 V; BENCH floor 3.3 V | none | – |
| V3 | Charge temperature | 0..45 °C | Murata SDS | SGO inv 6 (P11) | none | – |
| V4 | Discharge temperature | -20..60 °C | Murata SDS | SGO inv 4 at 60 °C; BENCH stop 45 °C | none | – |
| V5 | Charge current | standard 3.0 A (1C); up to 5 A | Sony TI Rev 0.1; Murata | Charger rating; SGO none | No SGO backup | SGO inv 7a at 1C (P12) |
| V6 | Continuous discharge | 15 A without thermal cut-off (30 A with 80 °C cut-off) | Murata SDS | SGO inv 3 at 1.5C = 4.5 A | none (inv 3 is stricter) | Inv 7b at 5C (never binding; kept for one code path) |

## Measurement margins, persistence, hysteresis (both configs, `fdir_config.h`)

| Item | Value | Why |
|---|---|---|
| Current measurement margin | 5 % of the band limit (threshold = 0.95 × limit) | INA226 gain error ≤ 0.1 %, shunt tolerance 1 %, calibration residual; rounded up |
| Temperature margin for band selection | 1.0 °C (the lower of the two bands within ±1 °C is used) | DS18B20 ±0.5 °C + cell-to-sensor gradient |
| Persistence | 5 consecutive samples over the threshold (5 s at 1 Hz) | continuous ratings; rejects one-sample spikes (C3c) |
| Hysteresis (release) | 60 consecutive samples below 0.8 × threshold | avoids limit cycling when the action removes the current |

## Gaps that stay open after v3.2

- Per-cell voltage (M1, V1): needs cell taps (ISS-047).
- Pulse power (M8): not modelled.
- M50 spec is Rev 0 "Tentative" (2017): confirm no later revision.
- VTC6 capacity: placeholder 3.0 Ah until measured (bench config).
