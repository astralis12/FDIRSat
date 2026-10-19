/*
 * fdir_sgo_v3.h — additions of SGO v3 (ADR-0018, ADR-0020, ADR-0024) to the unchanged fdir_sgo.h
 * interface. Bits 5..7 of fault_flags are new; bits 0..4 keep their v2 meaning. The tree may
 * still set only bits 0..4 (its output contract is unchanged).
 */
#ifndef FDIR_SGO_V3_H
#define FDIR_SGO_V3_H

#include <stdint.h>
#include "fdir_sgo.h"

#define FAULT_BIT_OVERVOLTAGE    (1U << 5)
#define FAULT_BIT_CHARGE_TEMP    (1U << 6)   /* charge inhibited: outside the charge-temperature window */
#define FAULT_BIT_CURRENT_LIMIT  (1U << 7)   /* v3.2: cell current above its temperature-band limit */
#define FDIR_FLAG_MASK_V3        0xFFU

/* SGO v3 state (one per spacecraft). Only process_fdir_with_sgo() updates it; the DUT keeps
 * one copy per simulated spacecraft inside the CRC guard and swaps it in
 * (firmware/pio/dut/src/main.c, fdir_state_guard.h).
 *   charge_inhibit  invariant 6 latch (ADR-0020): set outside [CHARGE_MIN, CHARGE_MAX],
 *                   cleared only inside [CHARGE_REL_MIN, CHARGE_REL_MAX]
 *   chg_on, dis_on  invariant 7 latches (ADR-0024): set after FDIR_ILIM_PERSIST consecutive
 *                   samples over the band threshold, cleared after FDIR_ILIM_RELEASE
 *                   consecutive samples below FDIR_ILIM_REL_FRAC x threshold
 *   *_cnt, *_rel    the persistence and release counters (saturating) */
typedef struct {
    uint8_t charge_inhibit;
    uint8_t chg_on, chg_cnt, chg_rel;
    uint8_t dis_on, dis_cnt, dis_rel;
} FdirSgoState;

extern FdirSgoState fdir_sgo_v3_state;
#define fdir_sgo_v3_charge_inhibit (fdir_sgo_v3_state.charge_inhibit)

/* Median-of-3 battery temperature vote. Readings that are non-finite or outside
 * the plausible range are discarded. 3 valid: *t_hi = *t_lo = median. 2 valid:
 * *t_hi = max, *t_lo = min of the two (conservative for both limits). 0 or 1
 * valid: both FDIR_TBAT_VOTE_INVALID, an implausible value, so the SGO flags
 * bit 4 and drives the safe default.
 * Returns 1 if a single sensor is lost or disagrees with the median by more
 * than FDIR_TBAT_SENSOR_DISAGREE, else 0. Also arms the SGO's next call with
 * *t_lo for the low-temperature checks (the telemetry carries *t_hi). */
int fdir_temp_vote3(const float t[3], float *t_hi, float *t_lo);

/* Invariant 7 band limits [C] (exposed for tests and proofs): the lowest limit over the
 * temperature interval [a, b]. Charge: 0 outside all charge bands. */
float fdir_ilim_charge_c(float a, float b);
float fdir_ilim_discharge_c(float a, float b);

#ifdef FDIR_PROOF_HOOKS
/* output after invariants 1-4, for the monotone-composition property (P14) */
extern FDIRResult fdir_sgo_v3_base;
#endif

#endif /* FDIR_SGO_V3_H */
