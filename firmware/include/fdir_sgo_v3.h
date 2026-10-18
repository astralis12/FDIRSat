/*
 * fdir_sgo_v3.h — additions of SGO v3 (ADR-0018, ISS-050) to the unchanged fdir_sgo.h interface.
 * Bits 5 and 6 of fault_flags are new; bits 0..4 keep their v2 meaning. The tree may
 * still set only bits 0..4 (its output contract is unchanged).
 */
#ifndef FDIR_SGO_V3_H
#define FDIR_SGO_V3_H

#include <stdint.h>
#include "fdir_sgo.h"

#define FAULT_BIT_OVERVOLTAGE    (1U << 5)
#define FAULT_BIT_CHARGE_TEMP    (1U << 6)   /* charge inhibited: outside the charge-temperature window */
#define FDIR_FLAG_MASK_V3        0x7FU

/* Charge-temperature latch (invariant 6). 1 = charging inhibited. Set when the
 * battery is outside [FDIR_TBAT_CHARGE_MIN, FDIR_TBAT_CHARGE_MAX]; cleared only
 * inside [FDIR_TBAT_CHARGE_REL_MIN, FDIR_TBAT_CHARGE_REL_MAX] (hysteresis).
 * Starts at 0; process_fdir_with_sgo() updates it (the DUT keeps one copy per
 * simulated spacecraft and swaps it in, firmware/pio/dut/src/main.c). */
extern uint8_t fdir_sgo_v3_charge_inhibit;

/* Median-of-3 battery temperature vote. Readings that are non-finite or outside
 * the plausible range are discarded. 3 valid: *t_hi = *t_lo = median. 2 valid:
 * *t_hi = max, *t_lo = min of the two (conservative for both limits). 0 or 1
 * valid: both FDIR_TBAT_VOTE_INVALID, an implausible value, so the SGO flags
 * bit 4 and drives the safe default.
 * Returns 1 if a single sensor is lost or disagrees with the median by more
 * than FDIR_TBAT_SENSOR_DISAGREE, else 0. Also arms the SGO's next call with
 * *t_lo for the low-temperature check (the telemetry carries *t_hi). */
int fdir_temp_vote3(const float t[3], float *t_hi, float *t_lo);

#endif /* FDIR_SGO_V3_H */
