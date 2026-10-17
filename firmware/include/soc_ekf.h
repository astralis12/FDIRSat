/*
 * soc_ekf.h — onboard SoC EKF (RUNBOOK 4.2), C port of sim/ecm_ekf.py SoCEKF.
 *
 * State x = [SoC, V1, V2] of a 2RC ECM whose parameters are piecewise-linear
 * in SoC (N4 scheduling, sim/fit_ecm_scheduled.py). Tables and noise come from
 * the generated header soc_ekf_tables.h (codegen/emit_ekf_tables.py); do not
 * edit that header by hand.
 *
 * Units are per cell: current [A] (discharge > 0), voltage [V], time [s].
 *
 * Precision: float by default (Cortex-M4F single-precision FPU). Define
 * SOC_EKF_DOUBLE for the double build used to check the equations against
 * the Python reference. Build with -ffp-contract=off so that results do not
 * depend on whether the compiler fuses multiply-adds.
 */
#ifndef SOC_EKF_H
#define SOC_EKF_H

#include <stdint.h>

#ifdef SOC_EKF_DOUBLE
typedef double ekf_real_t;
#else
typedef float ekf_real_t;
#endif

/* Status bits returned by soc_ekf_step */
#define SOC_EKF_ST_OK             0x00U
#define SOC_EKF_ST_INPUT_REJECTED 0x01U  /* i or dt non-finite / out of range: state unchanged */
#define SOC_EKF_ST_NO_UPDATE      0x02U  /* v non-finite / out of range: predict only          */
#define SOC_EKF_ST_COV_RESET      0x04U  /* covariance unusable: reset to its initial value    */
#define SOC_EKF_ST_STATE_CLAMPED  0x08U  /* SoC or RC voltage outside the guard band: clamped  */

typedef struct {
    ekf_real_t x[3];      /* SoC [-], V1 [V], V2 [V] */
    ekf_real_t P[3][3];
} SocEkf;

typedef struct {
    ekf_real_t innov;     /* v_meas - predicted voltage [V]; 0 when not updated */
    ekf_real_t innov_var; /* innovation variance [V^2];      0 when not updated */
} SocEkfStep;

void       soc_ekf_init(SocEkf *e, ekf_real_t soc0);

/* One step: predict over dt with i_prev, update with v_meas at i_now.
 * out may be NULL. Returns a combination of SOC_EKF_ST_* bits. */
uint8_t    soc_ekf_step(SocEkf *e, ekf_real_t i_prev, ekf_real_t i_now,
                        ekf_real_t v_meas, ekf_real_t dt, SocEkfStep *out);

ekf_real_t soc_ekf_soc(const SocEkf *e);
ekf_real_t soc_ekf_std(const SocEkf *e);   /* sqrt(max(P[0][0], 0)) */

/* Table lookups, exposed for tests */
ekf_real_t soc_ekf_ocv(ekf_real_t soc);
ekf_real_t soc_ekf_ocv_slope(ekf_real_t soc);

#endif /* SOC_EKF_H */
