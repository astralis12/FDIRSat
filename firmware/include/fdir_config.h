/*
 * fdir_config.h — reference-mission parameters for the SGO.
 *
 * STATUS: PROPOSED (17 Oct 2026); split into ref / bench headers 18 Oct 2026. Every value must be traced to a requirement
 * or datasheet in the M2 threshold traceability matrix before freeze.
 *
 * Derivation keeps the per-cell values implied by the original 3S/12 V header
 * (9.0 V / 3 = 3.0 V/cell critical, 10.8 V / 3 = 3.6 V/cell nominal-min) and
 * scales them to the 28 V reference bus (8S).
 */
#ifndef FDIR_CONFIG_H
#define FDIR_CONFIG_H

/* Cell- and pack-specific values: one header per build configuration
 * (owner decision 18 Oct 2026). Everything below is shared by both. */
#if defined(FDIR_CONFIG_BENCH)
#include "fdir_config_bench.h"
#else
#include "fdir_config_ref.h"
#endif

/* Per-cell voltage thresholds [V/cell] — TRACE IN M2 */
#define FDIR_VCELL_CRITICAL_MIN  3.0f   /* deep-discharge protection             */
#define FDIR_VCELL_NOMINAL_MIN   3.6f   /* undervoltage (secondary shed)         */
#define FDIR_VCELL_PLAUSIBLE_MIN 0.5f   /* below: reading treated as invalid     */
#define FDIR_VCELL_PLAUSIBLE_MAX 4.5f   /* above: reading treated as invalid     */

/* Pack thresholds derived from per-cell values */
#define FDIR_VBAT_CRITICAL_MIN   (FDIR_VCELL_CRITICAL_MIN  * (float)FDIR_N_SERIES)
#define FDIR_VBAT_NOMINAL_MIN    (FDIR_VCELL_NOMINAL_MIN   * (float)FDIR_N_SERIES)
#define FDIR_VBAT_OV_MAX         (FDIR_VCELL_OV_MAX        * (float)FDIR_N_SERIES)
#define FDIR_VBAT_PLAUSIBLE_MIN  (FDIR_VCELL_PLAUSIBLE_MIN * (float)FDIR_N_SERIES)
#define FDIR_VBAT_PLAUSIBLE_MAX  (FDIR_VCELL_PLAUSIBLE_MAX * (float)FDIR_N_SERIES)

/* Current thresholds [A] — C-rate based so they scale with nP. TRACE IN M2 */
#define FDIR_C_RATE_MAX          1.5f
#define FDIR_IBAT_CRITICAL_MAX   (FDIR_C_RATE_MAX * FDIR_CELL_CAPACITY_AH * (float)FDIR_N_PARALLEL)
#define FDIR_IBAT_PLAUSIBLE_ABS  (10.0f * FDIR_CELL_CAPACITY_AH * (float)FDIR_N_PARALLEL)

/* Temperature thresholds [degC] — carried over from fdir_sgo.h. TRACE IN M2 */
/* FDIR_TBAT_CRITICAL_MAX is per configuration (cell discharge limit) */
#define FDIR_TBAT_SENSOR_DISAGREE 5.0f  /* one of 3 sensors this far from the median -> bit 4 */
/* SGO v3.2 invariant 7 (ISS-059): margins, persistence, hysteresis (docs/cell_limits_trace.md) */
#define FDIR_ILIM_MEAS_MARGIN    0.05f  /* threshold = (1 - margin) x band limit        */
#define FDIR_ILIM_T_MARGIN       1.0f   /* band chosen over [t_lo - m, t_hi + m] (min)   */
#define FDIR_ILIM_PERSIST        5U     /* consecutive samples over threshold -> act    */
#define FDIR_ILIM_RELEASE        60U    /* consecutive samples below REL_FRAC x thr     */
#define FDIR_ILIM_REL_FRAC       0.8f
#define FDIR_TBAT_VOTE_INVALID   1000.0f  /* vote result with < 2 valid sensors (implausible) */
#define FDIR_TBAT_PLAUSIBLE_MIN  (-60.0f)
#define FDIR_TBAT_PLAUSIBLE_MAX  150.0f

/* SoC thresholds [%] */
#define FDIR_SOC_CRITICAL_MIN    5.0f
#define FDIR_SOC_PLAUSIBLE_MIN   (-5.0f)   /* small EKF overshoot tolerated */
#define FDIR_SOC_PLAUSIBLE_MAX   105.0f

/* SoC EKF guards (soc_ekf.c), per cell — PROPOSED, TRACE IN M2.
 * Inputs outside these ranges are rejected; a state outside them after a step
 * is clamped (SoC, RC voltages) or the covariance is reset to its initial value.
 * In nominal operation none of them is reached (verify/test_ekf_bitexact.py). */
#define FDIR_EKF_DT_MAX_S        60.0f  /* longest accepted sample interval      */
#define FDIR_EKF_C_RATE_ABS_MAX  10.0f  /* accepted |I| = this x table capacity  */
#define FDIR_EKF_SOC_MIN         (-0.2f)
#define FDIR_EKF_SOC_MAX         1.2f
#define FDIR_EKF_VRC_ABS_MAX     1.0f   /* RC branch voltage [V]                  */
#define FDIR_EKF_P_ABS_MAX       1.0f   /* any covariance entry                   */

#endif /* FDIR_CONFIG_H */
