/*
 * fdir_features.c — short input history for the tree (ADR-0019). See header.
 * Twin: sim/features.py performs the same float32 operations in the same order.
 */
#include "fdir_features.h"

static int plausible(float x, float lo, float hi)
{
    return (x >= lo) && (x <= hi);     /* false for NaN */
}

static float clampf_(float x, float lo, float hi)
{
    if (x > hi) {
        return hi;
    }
    if (x >= lo) {
        return x;
    }
    return lo;
}

void fdir_features_init(FdirFeatures *f)
{
    uint32_t k;
    for (k = 0; k < FDIR_FEAT_V_LEN; k++) {
        f->v_ring[k] = 3.7f;
    }
    for (k = 0; k < FDIR_FEAT_SOC_LEN; k++) {
        f->soc_ring[k] = 0.5f;
    }
    f->v_idx = 0U;
    f->v_cnt = 0U;
    f->soc_idx = 0U;
    f->soc_cnt = 0U;
    f->tick = 0U;
    f->v_last = 3.7f;
    f->soc_last = 0.5f;
    f->isa_last = 0.0f;
    f->sun_sum = 0.0f;        /* unused since the incremental mean; kept for layout stability */
    f->sun_mean = 1.0f;       /* nominal array before the first sunlit sample */
    f->sun_cnt = 0U;
    f->was_sun = 0U;
}

void fdir_features_step(FdirFeatures *f, float v_cell, float soc, float i_sa_pu, float out[3])
{
    float vs, ss, ia, dv, dsoc;
    uint32_t oldest;
    uint8_t sun;

    /* only plausible readings enter the history */
    vs = plausible(v_cell, 0.5f, 4.5f) ? v_cell : f->v_last;
    ss = plausible(soc, -0.2f, 1.2f) ? soc : f->soc_last;
    ia = plausible(i_sa_pu, 0.0f, 2.0f) ? i_sa_pu : f->isa_last;
    f->v_last = vs;
    f->soc_last = ss;
    f->isa_last = ia;

    /* dv60: against the oldest sample in the 60 s window */
    if (f->v_cnt == 0U) {
        dv = 0.0f;
    } else {
        oldest = (f->v_cnt < FDIR_FEAT_V_LEN) ? 0U : f->v_idx;
        dv = vs - f->v_ring[oldest];
    }
    f->v_ring[f->v_idx] = vs;
    f->v_idx = (f->v_idx + 1U) % FDIR_FEAT_V_LEN;
    if (f->v_cnt < FDIR_FEAT_V_LEN) {
        f->v_cnt++;
    }

    /* i_sa_sun: incremental mean over the current sunlit phase, held in eclipse */
    sun = (uint8_t)(ia > FDIR_FEAT_SUN_TH);
    if (sun && !f->was_sun) {
        f->sun_cnt = 0U;                               /* sunrise: new phase */
    }
    if (sun && f->sun_cnt < FDIR_FEAT_SUN_CAP) {
        f->sun_cnt++;
        f->sun_mean = clampf_(f->sun_mean + (ia - f->sun_mean) / (float)f->sun_cnt, 0.0f, 2.0f);
    }
    f->was_sun = sun;

    /* dsoc600: against the oldest of the per-minute SoC samples */
    if (f->soc_cnt == 0U) {
        dsoc = 0.0f;
    } else {
        oldest = (f->soc_cnt < FDIR_FEAT_SOC_LEN) ? 0U : f->soc_idx;
        dsoc = ss - f->soc_ring[oldest];
    }
    if (f->tick == 0U) {
        f->soc_ring[f->soc_idx] = ss;
        f->soc_idx = (f->soc_idx + 1U) % FDIR_FEAT_SOC_LEN;
        if (f->soc_cnt < FDIR_FEAT_SOC_LEN) {
            f->soc_cnt++;
        }
    }
    f->tick = (f->tick + 1U) % FDIR_FEAT_SOC_DEC;

    out[0] = dv;
    out[1] = f->sun_mean;
    out[2] = dsoc;
}
