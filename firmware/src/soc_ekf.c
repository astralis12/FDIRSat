/*
 * soc_ekf.c — onboard SoC EKF (RUNBOOK 4.2). Same equations, in the same
 * order, as SoCEKF.step in sim/ecm_ekf.py with a ScheduledECM:
 *
 *   p      = table(SoC before the step)          r0, r1, tau1, r2, tau2
 *   a_k    = exp(-dt / (r_k * c_k)),  c_k = tau_k / r_k
 *   x     <- A x + B i_prev,  A = diag(1, a1, a2),
 *             B = [-dt / (3600 Q), r1 (1 - a1), r2 (1 - a2)]
 *   P     <- A P A^T + Q
 *   H      = [OCV'(SoC), -1, -1],  y = OCV(SoC) - V1 - V2 - r0 i_now
 *   S      = H P H^T + R,  K = P H^T / S
 *   x     <- x + K (v_meas - y),  P <- (I - K H) P
 *
 * Added for onboard use (none of them acts in nominal operation):
 *   G1  inputs i, dt and v are checked for finiteness and range first;
 *   G2  the update is skipped and P reset if S is not usable;
 *   G3  after the step, SoC and RC voltages are clamped to the guard band and
 *       P is reset if any entry is non-finite or out of range.
 * G1-G3 make the state bounds inductive, which is what the CBMC proof uses
 * (verify/cbmc/harness_ekf.c). Thresholds live in fdir_config.h.
 */
#include <math.h>
#include <stddef.h>
#include "soc_ekf.h"
#include "soc_ekf_tables.h"
#include "fdir_config.h"

/* Math functions can be replaced by contract models in the CBMC harness:
 * -DSOC_EKF_MATH_MODELS -DSOC_EKF_EXP=cbmc_expf -DSOC_EKF_SQRT=cbmc_sqrtf */
#ifdef SOC_EKF_MATH_MODELS
ekf_real_t SOC_EKF_EXP(ekf_real_t x);
ekf_real_t SOC_EKF_SQRT(ekf_real_t x);
#endif
#ifndef SOC_EKF_EXP
#  ifdef SOC_EKF_DOUBLE
#    define SOC_EKF_EXP(x)  exp(x)
#  else
#    define SOC_EKF_EXP(x)  expf(x)
#  endif
#endif
#ifndef SOC_EKF_SQRT
#  ifdef SOC_EKF_DOUBLE
#    define SOC_EKF_SQRT(x) sqrt(x)
#  else
#    define SOC_EKF_SQRT(x) sqrtf(x)
#  endif
#endif

#define R(x) ((ekf_real_t)(x))

static int in_range(ekf_real_t x, ekf_real_t lo, ekf_real_t hi)
{
    /* false for NaN: every comparison with NaN is false */
    return (x >= lo) && (x <= hi);
}

static ekf_real_t clamp(ekf_real_t x, ekf_real_t lo, ekf_real_t hi)
{
    if (x > hi) {
        return hi;
    }
    if (x >= lo) {
        return x;
    }
    return lo;  /* below range or NaN */
}

/* numpy.interp for strictly increasing xp: fp[0] below, fp[n-1] above. */
static ekf_real_t interp(ekf_real_t x, const ekf_real_t *xp, const ekf_real_t *fp, int n)
{
    int lo = 0;
    int hi = n - 1;
    ekf_real_t slope;

    if (!(x > xp[0])) {
        return fp[0];
    }
    if (x >= xp[n - 1]) {
        return fp[n - 1];
    }
    while (hi - lo > 1) {  /* invariant: xp[lo] <= x < xp[hi] */
        int mid = lo + (hi - lo) / 2;
        if (x >= xp[mid]) {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    slope = (fp[lo + 1] - fp[lo]) / (xp[lo + 1] - xp[lo]);
    return slope * (x - xp[lo]) + fp[lo];
}

ekf_real_t soc_ekf_ocv(ekf_real_t soc)
{
    return interp(soc, SOC_EKF_OCV_SOC, SOC_EKF_OCV_V, SOC_EKF_OCV_N);
}

ekf_real_t soc_ekf_ocv_slope(ekf_real_t soc)
{
    return interp(soc, SOC_EKF_OCV_SOC, SOC_EKF_OCV_DV, SOC_EKF_OCV_N);
}

static void reset_cov(SocEkf *e)
{
    int i, j;
    for (i = 0; i < 3; i++) {
        for (j = 0; j < 3; j++) {
            e->P[i][j] = R(0.0);
        }
    }
    e->P[0][0] = SOC_EKF_P0_SOC;
    e->P[1][1] = SOC_EKF_P0_RC;
    e->P[2][2] = SOC_EKF_P0_RC;
}

void soc_ekf_init(SocEkf *e, ekf_real_t soc0)
{
    e->x[0] = soc0;
    e->x[1] = R(0.0);
    e->x[2] = R(0.0);
    reset_cov(e);
}

/* G3: clamp the state and reset P if it left the guard band. */
static uint8_t guard_state(SocEkf *e)
{
    uint8_t st = SOC_EKF_ST_OK;
    int i, j, cov_ok = 1;

    if (!in_range(e->x[0], R(FDIR_EKF_SOC_MIN), R(FDIR_EKF_SOC_MAX))) {
        e->x[0] = clamp(e->x[0], R(FDIR_EKF_SOC_MIN), R(FDIR_EKF_SOC_MAX));
        st |= SOC_EKF_ST_STATE_CLAMPED;
    }
    for (i = 1; i < 3; i++) {
        if (!in_range(e->x[i], -R(FDIR_EKF_VRC_ABS_MAX), R(FDIR_EKF_VRC_ABS_MAX))) {
            e->x[i] = clamp(e->x[i], -R(FDIR_EKF_VRC_ABS_MAX), R(FDIR_EKF_VRC_ABS_MAX));
            st |= SOC_EKF_ST_STATE_CLAMPED;
        }
    }
    for (i = 0; i < 3; i++) {
        for (j = 0; j < 3; j++) {
            if (!in_range(e->P[i][j], -R(FDIR_EKF_P_ABS_MAX), R(FDIR_EKF_P_ABS_MAX))) {
                cov_ok = 0;
            }
        }
        if (!(e->P[i][i] >= R(0.0))) {
            cov_ok = 0;
        }
    }
    if (!cov_ok) {
        reset_cov(e);
        st |= SOC_EKF_ST_COV_RESET;
    }
    return st;
}

uint8_t soc_ekf_step(SocEkf *e, ekf_real_t i_prev, ekf_real_t i_now,
                     ekf_real_t v_meas, ekf_real_t dt, SocEkfStep *out)
{
    const ekf_real_t i_max = R(FDIR_EKF_C_RATE_ABS_MAX) * SOC_EKF_Q_AH;
    ekf_real_t r0, r1, tau1, r2, tau2, c1, c2, a[3], b[3];
    ekf_real_t H[3], hp[3], K[3], Pn[3][3];
    ekf_real_t soc, y_hat, S, innov;
    uint8_t st = SOC_EKF_ST_OK;
    int i, j;

    if (out != NULL) {
        out->innov = R(0.0);
        out->innov_var = R(0.0);
    }
    /* G1: current and time step */
    if (!in_range(i_prev, -i_max, i_max) || !in_range(i_now, -i_max, i_max) ||
        !in_range(dt, R(0.0), R(FDIR_EKF_DT_MAX_S))) {
        return SOC_EKF_ST_INPUT_REJECTED;
    }

    /* scheduled parameters at the SoC before the step */
    soc  = e->x[0];
    r0   = interp(soc, SOC_EKF_KNOT_SOC, SOC_EKF_KNOT_R0,   SOC_EKF_KNOT_N);
    r1   = interp(soc, SOC_EKF_KNOT_SOC, SOC_EKF_KNOT_R1,   SOC_EKF_KNOT_N);
    tau1 = interp(soc, SOC_EKF_KNOT_SOC, SOC_EKF_KNOT_TAU1, SOC_EKF_KNOT_N);
    r2   = interp(soc, SOC_EKF_KNOT_SOC, SOC_EKF_KNOT_R2,   SOC_EKF_KNOT_N);
    tau2 = interp(soc, SOC_EKF_KNOT_SOC, SOC_EKF_KNOT_TAU2, SOC_EKF_KNOT_N);
    c1 = tau1 / r1;
    c2 = tau2 / r2;
    a[0] = R(1.0);
    a[1] = SOC_EKF_EXP(-dt / (r1 * c1));
    a[2] = SOC_EKF_EXP(-dt / (r2 * c2));
    b[0] = -dt / (R(3600.0) * SOC_EKF_Q_AH);
    b[1] = r1 * (R(1.0) - a[1]);
    b[2] = r2 * (R(1.0) - a[2]);

    /* predict */
    for (i = 0; i < 3; i++) {
        e->x[i] = a[i] * e->x[i] + b[i] * i_prev;
    }
    for (i = 0; i < 3; i++) {
        for (j = 0; j < 3; j++) {
            e->P[i][j] = (a[i] * e->P[i][j]) * a[j];
        }
    }
    e->P[0][0] += SOC_EKF_Q_SOC;
    e->P[1][1] += SOC_EKF_Q_RC;
    e->P[2][2] += SOC_EKF_Q_RC;

    /* update (G1: only with a plausible voltage) */
    if (!in_range(v_meas, R(FDIR_VCELL_PLAUSIBLE_MIN), R(FDIR_VCELL_PLAUSIBLE_MAX))) {
        st |= SOC_EKF_ST_NO_UPDATE;
        return (uint8_t)(st | guard_state(e));
    }
    soc = e->x[0];
    H[0] = soc_ekf_ocv_slope(soc);
    H[1] = R(-1.0);
    H[2] = R(-1.0);
    y_hat = soc_ekf_ocv(soc) - e->x[1] - e->x[2] - r0 * i_now;
    for (j = 0; j < 3; j++) {
        hp[j] = H[0] * e->P[0][j] + H[1] * e->P[1][j] + H[2] * e->P[2][j];
    }
    S = hp[0] * H[0] + hp[1] * H[1] + hp[2] * H[2] + SOC_EKF_R_V;
    /* G2: for a valid covariance S >= R; anything clearly below is not usable */
    if (!(S >= R(0.5) * SOC_EKF_R_V)) {
        reset_cov(e);
        return (uint8_t)(st | SOC_EKF_ST_COV_RESET | guard_state(e));
    }
    for (i = 0; i < 3; i++) {
        K[i] = (e->P[i][0] * H[0] + e->P[i][1] * H[1] + e->P[i][2] * H[2]) / S;
    }
    innov = v_meas - y_hat;
    for (i = 0; i < 3; i++) {
        e->x[i] = e->x[i] + K[i] * innov;
    }
    for (i = 0; i < 3; i++) {
        for (j = 0; j < 3; j++) {
            Pn[i][j] = (((i == 0) ? R(1.0) : R(0.0)) - K[i] * H[0]) * e->P[0][j]
                     + (((i == 1) ? R(1.0) : R(0.0)) - K[i] * H[1]) * e->P[1][j]
                     + (((i == 2) ? R(1.0) : R(0.0)) - K[i] * H[2]) * e->P[2][j];
        }
    }
    for (i = 0; i < 3; i++) {
        for (j = 0; j < 3; j++) {
            e->P[i][j] = Pn[i][j];
        }
    }
    if (out != NULL) {
        out->innov = innov;
        out->innov_var = S;
    }
    return (uint8_t)(st | guard_state(e));
}

ekf_real_t soc_ekf_soc(const SocEkf *e)
{
    return e->x[0];
}

ekf_real_t soc_ekf_std(const SocEkf *e)
{
    ekf_real_t p = e->P[0][0];
    return SOC_EKF_SQRT((p > R(0.0)) ? p : R(0.0));
}
