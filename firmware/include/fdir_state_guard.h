/*
 * fdir_state_guard.h — CRC-16 guard on the per-spacecraft mutable state (ISS-057, ADR-0021).
 *
 * The CRC scrub protects only the tree tables. The EKF state, the history buffers and the
 * SGO v3 charge latch are rewritten every step, so they get a check value instead: the CRC
 * is written after every step and checked before the next. A mismatch means the state was
 * corrupted between steps (SEU): it is rebuilt (EKF from the measured cell voltage, history
 * cleared, charge latch set so the SGO re-decides from the window) and the step reports
 * FDIR_GUARD_ST_REPAIRED in its status byte.
 */
#ifndef FDIR_STATE_GUARD_H
#define FDIR_STATE_GUARD_H

#include <stdint.h>
#include "soc_ekf.h"
#include "fdir_features.h"
#include "fdir_sgo_v3.h"

#define FDIR_GUARD_ST_REPAIRED   0x20U   /* status bit: state CRC mismatch, state rebuilt */

typedef struct {
    SocEkf       ekf;
    FdirFeatures feat;
    FdirSgoState sgo;        /* SGO v3 latches and counters of this spacecraft (7 B) */
    uint16_t     crc;        /* CRC-16/CCITT-FALSE over ekf, feat and sgo         */
} FdirGuardedState;

/* CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) over n bytes. */
uint16_t fdir_crc16(const uint8_t *p, uint32_t n, uint16_t c);

/* CRC over the guarded fields (not the crc field itself). */
uint16_t fdir_state_crc(const FdirGuardedState *s);

/* SoC in [0, 1] whose table OCV is closest to v_cell (bisection; any float input,
 * including NaN, gives a value in [0, 1]). Used to rebuild the EKF after a repair. */
float fdir_soc_from_voltage(float v_cell);

/* Initialise all fields and the CRC. */
void fdir_state_init(FdirGuardedState *s, float soc0);

/* Before a step: 1 if the CRC does not match; the state is then rebuilt from v_cell. */
int fdir_state_check(FdirGuardedState *s, float v_cell);

/* After a step: store the new CRC. */
void fdir_state_seal(FdirGuardedState *s);

#endif /* FDIR_STATE_GUARD_H */
