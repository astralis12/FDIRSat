# Owner inputs, 18 Oct 2026 (item 6)

Supplied in chat by the owner. Recorded here as given; the "Check" notes are things to verify
against the primary documents before any value goes into `config/fmeca_rates.json` or a result.

## 1. Cell: Sony/Murata US18650VTC6

- Datasheet in hand: Sony "Technical Information" Rev 0.1, 20 Jan 2015 (owner's Downloads,
  not committed). Already traced in `results/threshold_table.json` (commit 37cbae8).
- Additional values from the owner (sources: Murata SDS and product datasheet, Keeppower page):
  - Max continuous discharge: 30 A with an 80 degC temperature cut-off; 15-20 A without one
  - Discharge temperature (ambient): -20 to +60 degC; cell body must not exceed 80-90 degC
  - **Charge temperature (ambient): 0 to 45 degC**
  - Standard charge: 3.0-5.0 A CC/CV to 4.2 V (4.25 V max)
  - URLs: murata.com us18650vtc6-sds.ashx, murata.com us18650vtc6-product-datasheet.ashx,
    keeppower.com product page

Check / consequences:
- `FDIR_TBAT_CRITICAL_MAX` is 60 degC. That is above the 45 degC charge limit, so overheating
  during charge is not covered. Candidate: a charge-temperature invariant (T > 45 degC and
  I_bat charging -> B >= 2 / S = 2), plus a low-temperature charge inhibit (T < 0 degC).
  This needs an ADR, CBMC properties and a full `run_cbmc.sh`. Log it as ISS-050.
- Use 15 A (no thermal cut-off in our pack) as the max continuous current margin in the
  threshold table, not 30 A.
- The "4.25 V max" charge voltage confirms ISS-049 (charger at the upper tolerance trips the
  4.20 V pack-average invariant).

## 2. ECSS clause mapping (owner, earlier message)

Already in `eval/threshold_table.py` (`ECSS` dict): ECSS-Q-ST-30 Annex F (6.4.2.10a);
ECSS-E-ST-20-20C clause 1; ECSS-E-ST-70-11C section 5 and 5.9.6; ECSS-E-ST-60-30C 5.1.
Not checked against the standard texts.

## 3. MIL-HDBK-217F values (owner summary)

Models given:
- Monolithic MOS (5.1.2.2): lambda_p = pi_Q (C1 pi_T + C2 pi_E) pi_L  [failures / 1e6 h]
- VLSI (5.1.2.5): lambda_p = pi_Q (0.0615 pi_T + C3 pi_E) pi_L
- MOSFET (6.4): lambda_p = lambda_b pi_T pi_A pi_Q pi_E, lambda_b = 0.012
- Diodes lambda_b: fast-recovery rectifier 0.005, Zener 0.0033, Schottky 0.047
- Resistors (9): lambda_b pi_T pi_R pi_Q pi_E; capacitors (10): lambda_b pi_T pi_C pi_V pi_SR pi_Q pi_E
- Tables given: C1 by gate/transistor count; C2 hermetic DIP 2.8e-4 Np^1.23, flatpack/can
  3.0e-5 Np^1.82; pi_L 10 (new) / 1.0 (stable > 6 months); pi_T Arrhenius 0.1 exp(-A(1/(Tj+273) - 1/298))
- pi_E (SF): microcircuits 0.90, discretes / resistors / capacitors 0.5
- pi_Q microcircuits: S/B 0.03, R 0.1, P 0.3, Q 1.0, non-ER 3.0, commercial 10.0
- MOSFET pi_Q: JANTXV 0.7, JANTX 1.0, JAN 2.4, lower 5.5, plastic 8.0
- Worked STM32F411 example: 0.03 x (0.03 x 1.5 + 0.03 x 0.90) x 1.0 = 0.00216 / 1e6 h

Check before use (these disagree with how the handbook is usually applied; verify against the
MIL-HDBK-217F Notice 2 PDF, do not use the example number as it stands):
- The STM32F411 is a 32-bit MOS microprocessor, so the microprocessor C1 row (by bit width)
  applies, not ~10,000 gates.
- The STM32F411 is in a plastic LQFP/UFQFPN package, so the nonhermetic C2 formula applies,
  not the hermetic one.
- The part is commercial, not class S/B, so the commercial pi_Q applies. The microcircuit
  pi_Q table in 217F (S 0.25, B 1.0, B-1 2.0, ...) differs from the values above.
- pi_E for SF and pi_T need the table rows for this technology (A value), not an interpolation.
- Report a commercial-part value and a space-grade value as two rows, each with its table number.

## 4. SPENVIS

Inputs are in `results/spenvis_inputs.json` (`python eval/spenvis_inputs.py`). The SEU rate
from SPENVIS is still pending.
