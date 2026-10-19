/*
 * CBMC harness for the state guard (firmware/src/fdir_state_guard.c, ISS-057, ADR-0021).
 *
 *   L0  the table-driven CRC byte step equals the bitwise CRC-16/CCITT-FALSE definition
 *   G1  fdir_soc_from_voltage: any float (NaN, Inf, denormal) -> SoC in [0, 1]
 *   L1  one CRC byte step, same register: different bytes -> different registers
 *   L2  one CRC byte step, same byte: different registers -> different registers
 *       L1 + L2 give G3 by induction over the bytes: a state that differs from the
 *       sealed one in any single bit of the guarded bytes (EKF, history, latch) has a
 *       different CRC (registers are equal before the flipped byte, differ after it by
 *       L1, and stay different by L2), so fdir_state_check() repairs it.
 *       (A symbolic 380-byte state in one proof exceeds the machine's memory.)
 *   G2  an initialised, sealed state passes the check unchanged (no false repair; start SoC 0.6)
 *   G2/G4 run on a concrete instance (SoC 0.6, v 3.8 V, linear OCV) with ANY wrong CRC value;
 *   G4  a state whose CRC does not match is rebuilt: check returns 1, SoC in [0, 1],
 *       latch = 1, and the rebuilt state is sealed
 * plus runtime safety (bounds, pointers, overflow, NaN, shifts) of the module.
 *
 * Run: see verify/cbmc/run_cbmc.sh (two runs: -DGUARD_LEMMAS and -DGUARD_STATE)
 */
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "fdir_state_guard.h"

float    nondet_float(void);
uint8_t  nondet_uint8(void);
uint16_t nondet_uint16(void);

/* The EKF is abstracted (soc_ekf.c has its own proof, verify/cbmc/harness_ekf.c):
 * the OCV table returns ANY float (NaN, Inf included), which makes G1 / G4 hold for any
 * OCV curve; soc_ekf_init is modelled by its effect on x and P (firmware/src/soc_ekf.c). */
#ifdef GUARD_STATE
/* concrete instance: linear OCV (a symbolic rebuilt SoC makes the CRC proof time out) */
ekf_real_t soc_ekf_ocv(ekf_real_t soc) { return 3.0f + 1.2f * soc; }
#else
ekf_real_t soc_ekf_ocv(ekf_real_t soc) { (void)soc; return nondet_float(); }
#endif
void soc_ekf_init(SocEkf *e, ekf_real_t soc0)
{
    int i, j;
    e->x[0] = soc0; e->x[1] = 0.0f; e->x[2] = 0.0f;
    for (i = 0; i < 3; i++) { for (j = 0; j < 3; j++) { e->P[i][j] = 0.0f; } }
    e->P[0][0] = 1e-4f; e->P[1][1] = 1e-6f; e->P[2][2] = 1e-6f;
}

static FdirGuardedState s;

/* bitwise reference (poly 0x1021) for L0 */
static uint16_t crc_bit_step(uint16_t c, uint8_t b)
{
    unsigned k;
    c = (uint16_t)(c ^ (uint16_t)((uint16_t)b << 8));
    for (k = 0U; k < 8U; k++) {
        c = (c & 0x8000U) ? (uint16_t)((uint16_t)(c << 1) ^ 0x1021U) : (uint16_t)(c << 1);
    }
    return c;
}

int main(void)
{
#ifdef GUARD_LEMMAS
    /* L0 */
    {
        const uint16_t c0 = nondet_uint16();
        const uint8_t b0 = nondet_uint8();
        __CPROVER_assert(fdir_crc16(&b0, 1U, c0) == crc_bit_step(c0, b0), "L0 table CRC step equals bitwise CRC-16/CCITT-FALSE");
    }
    /* G1 */
    const float v = nondet_float();
    const float q = fdir_soc_from_voltage(v);
    __CPROVER_assert(q >= 0.0f && q <= 1.0f, "G1 SoC from any voltage is in [0, 1]");

    /* L1 */
    const uint16_t c = nondet_uint16();
    const uint8_t b1 = nondet_uint8(), b2 = nondet_uint8();
    __CPROVER_assume(b1 != b2);
    __CPROVER_assert(fdir_crc16(&b1, 1U, c) != fdir_crc16(&b2, 1U, c), "L1 CRC step injective in the byte");

    /* L2 */
    const uint16_t c1 = nondet_uint16(), c2 = nondet_uint16();
    const uint8_t b = nondet_uint8();
    __CPROVER_assume(c1 != c2);
    __CPROVER_assert(fdir_crc16(&b, 1U, c1) != fdir_crc16(&b, 1U, c2), "L2 CRC step injective in the register");
#else
    /* concrete start state (the CRC over a symbolic 380-byte state does not finish in
     * 30 min); G2 / G4 do not depend on its value, and G3 is covered by L0-L2 for any state */
    const float soc0 = 0.6f;
    const float v = 3.8f;          /* concrete; G1 covers any voltage */
    fdir_state_init(&s, soc0);

    /* G2 */
    __CPROVER_assert(fdir_state_check(&s, v) == 0, "G2 sealed state passes the check");
    __CPROVER_assert(s.ekf.x[0] == soc0 && s.sgo.charge_inhibit == 0U && s.sgo.chg_on == 0U, "G2 check leaves a good state unchanged");

    /* G4 */
    const uint16_t bad = nondet_uint16();
    __CPROVER_assume(bad != s.crc);
    s.crc = bad;
    __CPROVER_assert(fdir_state_check(&s, v) == 1, "G4 CRC mismatch -> repair");
    __CPROVER_assert(s.ekf.x[0] >= 0.0f && s.ekf.x[0] <= 1.0f, "G4 rebuilt SoC in [0, 1]");
    __CPROVER_assert(s.sgo.charge_inhibit == 1U && s.sgo.chg_on == 1U && s.sgo.dis_on == 0U, "G4 rebuilt SGO state inhibits charging");
    __CPROVER_assert(fdir_state_crc(&s) == s.crc, "G4 rebuilt state is sealed");
    __CPROVER_assert(fdir_state_check(&s, v) == 0, "G4 rebuilt state passes the next check");
#endif
    return 0;
}
