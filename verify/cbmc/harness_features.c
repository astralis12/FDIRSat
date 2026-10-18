/*
 * CBMC proof harness for the tree's input history (firmware/src/fdir_features.c).
 *
 * One step from ANY state that satisfies the invariant below, with fully
 * nondeterministic float inputs (NaN, +/-Inf included):
 *   H1  no runtime error: ring-buffer bounds, overflow, NaN, division by zero
 *       (run-script flags)
 *   H2  the invariant holds again after the step (so, by induction from
 *       fdir_features_init (H3), for every reachable state)
 *   H3  init establishes the invariant
 *   H4  all three outputs are finite and bounded
 * Invariant: indices < buffer length, counts <= length, tick < decimation,
 * sun_cnt <= cap, buffer contents and *_last values in their plausible ranges,
 * sun_mean in [0, 2].
 */
#include <stdint.h>
#include "fdir_features.h"

float nondet_float(void);
uint32_t nondet_uint32(void);
uint8_t nondet_uint8(void);

#define IN(x, lo, hi) ((x) >= (lo) && (x) <= (hi))

static int inv(const FdirFeatures *f)
{
    uint32_t k;
    int ok = f->v_idx < FDIR_FEAT_V_LEN && f->v_cnt <= FDIR_FEAT_V_LEN
          && f->soc_idx < FDIR_FEAT_SOC_LEN && f->soc_cnt <= FDIR_FEAT_SOC_LEN
          && f->tick < FDIR_FEAT_SOC_DEC && f->sun_cnt <= FDIR_FEAT_SUN_CAP
          && IN(f->v_last, 0.5f, 4.5f) && IN(f->soc_last, -0.2f, 1.2f) && IN(f->isa_last, 0.0f, 2.0f)
          && IN(f->sun_mean, 0.0f, 2.0f) && f->was_sun <= 1U;
    for (k = 0; k < FDIR_FEAT_V_LEN; k++) {
        ok = ok && IN(f->v_ring[k], 0.5f, 4.5f);
    }
    for (k = 0; k < FDIR_FEAT_SOC_LEN; k++) {
        ok = ok && IN(f->soc_ring[k], -0.2f, 1.2f);
    }
    return ok;
}

int main(void)
{
    FdirFeatures f, g;
    float out[3];
    uint32_t k;

    fdir_features_init(&g);
    __CPROVER_assert(inv(&g), "H3 init establishes the invariant");

    for (k = 0; k < FDIR_FEAT_V_LEN; k++) {
        f.v_ring[k] = nondet_float();
    }
    for (k = 0; k < FDIR_FEAT_SOC_LEN; k++) {
        f.soc_ring[k] = nondet_float();
    }
    f.v_idx = nondet_uint32();
    f.v_cnt = nondet_uint32();
    f.soc_idx = nondet_uint32();
    f.soc_cnt = nondet_uint32();
    f.tick = nondet_uint32();
    f.sun_cnt = nondet_uint32();
    f.v_last = nondet_float();
    f.soc_last = nondet_float();
    f.isa_last = nondet_float();
    f.sun_sum = nondet_float();
    f.sun_mean = nondet_float();
    f.was_sun = nondet_uint8();
    __CPROVER_assume(inv(&f));

    fdir_features_step(&f, nondet_float(), nondet_float(), nondet_float(), out);

    __CPROVER_assert(inv(&f), "H2 invariant holds after the step");
    __CPROVER_assert(IN(out[0], -4.0f, 4.0f), "H4a dv60 finite, |dv| <= 4 V");
    __CPROVER_assert(IN(out[1], 0.0f, 2.0f), "H4b i_sa_sun finite in [0, 2]");
    __CPROVER_assert(IN(out[2], -1.5f, 1.5f), "H4c dsoc600 finite, |dsoc| <= 1.5");   /* range width 1.4 + rounding */
    return 0;
}
