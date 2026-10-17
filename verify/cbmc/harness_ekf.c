/*
 * CBMC proof harness for the onboard SoC EKF (firmware/src/soc_ekf.c), float build.
 *
 * One step from ANY state inside the guard band (fdir_config.h), with fully
 * nondeterministic IEEE-754 inputs (incl. NaN, +/-Inf, denormals, -0.0):
 *   E1  no runtime error: overflow, NaN, division by zero, bounds, pointers
 *       (checked by the run-script flags, not by assertions here)
 *   E2  the state after the step is inside the guard band again, so by
 *       induction from soc_ekf_init (E6) every reachable state is
 *   E3  innovation outputs are finite
 *   E4  rejected inputs leave the state unchanged
 *   E5  soc_ekf_std is finite and in [0, 1]
 *   E6  soc_ekf_init with a SoC inside the band gives a state inside the band
 *   E7  only defined status bits are returned
 * The tables are the generated ones (soc_ekf_tables.h), so the proof is for
 * the parameters that fly; regenerate tables -> re-run the proof.
 *
 * expf and sqrtf have no body in CBMC 5.95 (ISS-016). They are replaced by
 * contract models: the argument range is a proof obligation (assert) and the
 * result is any value in the true function's range on that interval (assume),
 * which over-approximates the library.
 *
 * Run: see verify/cbmc/run_cbmc.sh (needs -DSOC_EKF_EXP=cbmc_expf -DSOC_EKF_SQRT=cbmc_sqrtf)
 */
#include <stdint.h>
#include "soc_ekf.h"
#include "fdir_config.h"

float nondet_float(void);

/* exp(x) for x in [-DT_MAX / 0.5 s, 0]: result in (0, 1]; modelled as [0, 1].
 * The smallest table time constant is >= 1 s (fit bounds), 0.5 s leaves margin
 * for rounding of r * (tau / r). */
float cbmc_expf(float x)
{
    float r = nondet_float();
    __CPROVER_assert(x <= 0.0f && x >= -FDIR_EKF_DT_MAX_S / 0.5f, "M1 expf argument in modelled range");
    __CPROVER_assume(r >= 0.0f && r <= 1.0f);
    return r;
}

/* sqrt(x) for x in [0, P_MAX] with P_MAX <= 1: result in [0, 1]. */
float cbmc_sqrtf(float x)
{
    float r = nondet_float();
    __CPROVER_assert(x >= 0.0f && x <= FDIR_EKF_P_ABS_MAX, "M2 sqrtf argument in modelled range");
    __CPROVER_assume(r >= 0.0f && r <= 1.0f);
    return r;
}

#define IN(x, lo, hi) ((x) >= (lo) && (x) <= (hi))
#define FINITE(x) (!__CPROVER_isnanf(x) && !__CPROVER_isinff(x))

static int in_band(const SocEkf *e)
{
    int i, j, ok = IN(e->x[0], FDIR_EKF_SOC_MIN, FDIR_EKF_SOC_MAX)
                && IN(e->x[1], -FDIR_EKF_VRC_ABS_MAX, FDIR_EKF_VRC_ABS_MAX)
                && IN(e->x[2], -FDIR_EKF_VRC_ABS_MAX, FDIR_EKF_VRC_ABS_MAX);
    for (i = 0; i < 3; i++) {
        for (j = 0; j < 3; j++) {
            ok = ok && IN(e->P[i][j], -FDIR_EKF_P_ABS_MAX, FDIR_EKF_P_ABS_MAX);
        }
        ok = ok && (e->P[i][i] >= 0.0f);
    }
    return ok;
}

int main(void)
{
    SocEkf e, before;
    SocEkfStep out;
    float i_prev = nondet_float(), i_now = nondet_float(), v = nondet_float(), dt = nondet_float();
    float soc0 = nondet_float();
    int i, j, same;
    uint8_t st;

    /* E6: initialisation */
    if (IN(soc0, FDIR_EKF_SOC_MIN, FDIR_EKF_SOC_MAX)) {
        SocEkf e0;
        soc_ekf_init(&e0, soc0);
        __CPROVER_assert(in_band(&e0), "E6 init inside guard band");
    }

    /* arbitrary state inside the band (induction hypothesis) */
    for (i = 0; i < 3; i++) {
        e.x[i] = nondet_float();
        for (j = 0; j < 3; j++) {
            e.P[i][j] = nondet_float();
        }
    }
    __CPROVER_assume(in_band(&e));
    before = e;

    st = soc_ekf_step(&e, i_prev, i_now, v, dt, &out);

    __CPROVER_assert(in_band(&e), "E2 state inside guard band after step");
    __CPROVER_assert(FINITE(out.innov) && FINITE(out.innov_var), "E3 innovation outputs finite");
    if (st & SOC_EKF_ST_INPUT_REJECTED) {
        same = 1;
        for (i = 0; i < 3; i++) {
            same = same && (e.x[i] == before.x[i]);
            for (j = 0; j < 3; j++) {
                same = same && (e.P[i][j] == before.P[i][j]);
            }
        }
        __CPROVER_assert(same, "E4 rejected input leaves state unchanged");
    }
    {
        float s = soc_ekf_std(&e);
        __CPROVER_assert(FINITE(s) && s >= 0.0f && s <= 1.0f, "E5 std finite in [0, 1]");
    }
    __CPROVER_assert((st & ~0x0Fu) == 0u, "E7 status uses defined bits only");
    return 0;
}
