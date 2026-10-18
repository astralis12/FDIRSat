/*
 * fdir_features.h — short input history for the tree (ADR-0019), computed at 1 Hz.
 *
 *   out[0] dv60      v_cell now minus v_cell 60 s ago            [V per cell]
 *   out[1] i_sa_sun  mean i_sa_pu over the current sunlit phase, held in eclipse
 *   out[2] dsoc600   soc_ekf now minus soc_ekf 600 s ago (1 sample per minute)
 *
 * Only plausible readings enter the buffers; otherwise the last stored value is
 * repeated, so every output is finite (CBMC: verify/cbmc/harness_features.c).
 * Twin for training and evaluation: sim/features.py (same float32 arithmetic).
 */
#ifndef FDIR_FEATURES_H
#define FDIR_FEATURES_H

#include <stdint.h>

#define FDIR_FEAT_V_LEN    60U      /* 60 s at 1 Hz                    */
#define FDIR_FEAT_SOC_LEN  10U      /* 10 samples, one per minute      */
#define FDIR_FEAT_SOC_DEC  60U      /* decimation: 1 SoC sample / 60 s */
#define FDIR_FEAT_SUN_TH   0.02f    /* i_sa_pu above: sunlit           */
#define FDIR_FEAT_SUN_CAP  86400U   /* sample counter saturates (1 day) */

typedef struct {
    float    v_ring[FDIR_FEAT_V_LEN];
    float    soc_ring[FDIR_FEAT_SOC_LEN];
    uint32_t v_idx, v_cnt;          /* next write slot, samples stored (<= LEN) */
    uint32_t soc_idx, soc_cnt, tick;
    float    v_last, soc_last, isa_last;
    float    sun_sum, sun_mean;
    uint32_t sun_cnt;
    uint8_t  was_sun;
} FdirFeatures;

void fdir_features_init(FdirFeatures *f);
void fdir_features_step(FdirFeatures *f, float v_cell, float soc, float i_sa_pu, float out[3]);

#endif /* FDIR_FEATURES_H */
