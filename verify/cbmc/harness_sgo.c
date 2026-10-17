/*
 * CBMC proof harness for the SGO safety layer (fdir_sgo.c).
 *
 * Model: the learned tree is treated as an UNTRUSTED black box (Simplex view).
 * execute_onboard_fdir() is replaced by a stub returning nondeterministic bytes.
 *   - TREE_CONTRACT defined   : tree output constrained to its contract
 *                               (actions in 0..3, flags in bits 0..4)
 *   - TREE_CONTRACT undefined : tree output is ANY byte (worst case)
 * Telemetry inputs are fully nondeterministic IEEE-754 floats,
 * including NaN, +/-Inf, denormals and -0.0.
 *
 * Run: see verify/cbmc/run_cbmc.sh
 */
#include <stdint.h>
#include "fdir_sgo.h"

#ifdef SGO_V2
#include "fdir_config.h"
#define TH_SOC_CRIT  FDIR_SOC_CRITICAL_MIN
#define TH_V_CRIT    FDIR_VBAT_CRITICAL_MIN
#define TH_V_NOM     FDIR_VBAT_NOMINAL_MIN
#define TH_I_CRIT    FDIR_IBAT_CRITICAL_MAX
#define TH_T_CRIT    FDIR_TBAT_CRITICAL_MAX
#define IN(x, lo, hi) ((x) >= (lo) && (x) <= (hi))
#define PLAUSIBLE(V, I, T, S) (IN(V, FDIR_VBAT_PLAUSIBLE_MIN, FDIR_VBAT_PLAUSIBLE_MAX) && \
    IN(I, -FDIR_IBAT_PLAUSIBLE_ABS, FDIR_IBAT_PLAUSIBLE_ABS) && \
    IN(T, FDIR_TBAT_PLAUSIBLE_MIN, FDIR_TBAT_PLAUSIBLE_MAX) && \
    IN(S, FDIR_SOC_PLAUSIBLE_MIN, FDIR_SOC_PLAUSIBLE_MAX))
#define SOC_LO FDIR_SOC_PLAUSIBLE_MIN
#define SOC_HI FDIR_SOC_PLAUSIBLE_MAX
#else
#define TH_SOC_CRIT  5.0f
#define TH_V_CRIT    ECSS_VBAT_CRITICAL_MIN
#define TH_V_NOM     ECSS_VBAT_NOMINAL_MIN
#define TH_I_CRIT    ECSS_IBAT_CRITICAL_MAX
#define TH_T_CRIT    ECSS_TBAT_CRITICAL_MAX
#define PLAUSIBLE(V, I, T, S) 1
#define SOC_LO 0.0f
#define SOC_HI 100.0f
#endif

/* CBMC 5.95 has no body for __builtin_isfinite (it would be treated as
 * nondeterministic and give spurious traces), so use CBMC primitives. */
#define NONFINITE(x) (__CPROVER_isnanf(x) || __CPROVER_isinff(x))

float   nondet_float(void);
uint8_t nondet_uint8(void);

FDIRResult execute_onboard_fdir(HealthTelemetry *t)
{
    (void)t;
    FDIRResult r;
    r.fault_flags           = nondet_uint8();
    r.solar_reconfig_action = nondet_uint8();
    r.battery_limit_action  = nondet_uint8();
    r.load_shed_action      = nondet_uint8();
#ifdef TREE_CONTRACT
    __CPROVER_assume(r.solar_reconfig_action <= 3u);
    __CPROVER_assume(r.battery_limit_action  <= 3u);
    __CPROVER_assume(r.load_shed_action      <= 3u);
    __CPROVER_assume((r.fault_flags & ~0x1Fu) == 0u);
#endif
    return r;
}

int main(void)
{
    HealthTelemetry t;
    t.solar_voltage      = nondet_float();
    t.battery_current    = nondet_float();
    t.system_temperature = nondet_float();
    t.battery_soc        = nondet_float();

    const float V = t.solar_voltage, I = t.battery_current;
    const float T = t.system_temperature, S = t.battery_soc;

    FDIRResult o = process_fdir_with_sgo(&t);

    const int dropout = (V == 0.0f && I == 0.0f && T == 0.0f && S == 0.0f);

    /* P1: every action field stays in the actuator range 0..3 */
    __CPROVER_assert(o.solar_reconfig_action <= 3u, "P1a solar action in 0..3");
    __CPROVER_assert(o.battery_limit_action  <= 3u, "P1b battery action in 0..3");
    __CPROVER_assert(o.load_shed_action      <= 3u, "P1c load action in 0..3");

    /* P2: fault mask uses only defined bits 0..4 */
    __CPROVER_assert((o.fault_flags & ~0x1Fu) == 0u, "P2 fault mask bits 0..4 only");

    const int plausible = PLAUSIBLE(V, I, T, S);

#ifdef SGO_V2
    /* P9: implausible (incl. non-finite) telemetry -> sensor bit + safe default */
    if (!dropout && !plausible) {
        __CPROVER_assert(o.fault_flags & FAULT_BIT_SENSOR_DROPOUT, "P9a implausible -> bit 4");
        __CPROVER_assert(o.solar_reconfig_action == 0u && o.battery_limit_action == 1u &&
                         o.load_shed_action == 1u, "P9b implausible -> safe default (0,1,1)");
    }
#endif

    if (!dropout && plausible) {
        /* P3: deep discharge -> full load shed, trickle-charge path kept (ADR-0015) */
        if (S < TH_SOC_CRIT || V < TH_V_CRIT) {
            __CPROVER_assert(o.load_shed_action == ACT_SHED_ALL, "P3a DD -> L=3");
            __CPROVER_assert(o.battery_limit_action >= 1u, "P3b DD -> B>=1 (charge path kept)");
            __CPROVER_assert(o.fault_flags & FAULT_BIT_DEEP_DISCHARGE, "P3c DD flagged");
        }
        /* P4: undervoltage -> at least secondary shed */
        if (V < TH_V_NOM) {
            __CPROVER_assert(o.load_shed_action >= ACT_SHED_SECONDARY, "P4 UV -> L>=1");
        }
        /* P5: overcurrent -> charge limited and load shed */
        if (I > TH_I_CRIT) {
            __CPROVER_assert(o.battery_limit_action >= ACT_ISOLATE_ARRAYS, "P5a OC -> B>=2");
            __CPROVER_assert(o.load_shed_action >= ACT_SHED_SECONDARY, "P5b OC -> L>=1");
        }
        /* P6: over-temperature -> arrays isolated, primary shed */
        if (T > TH_T_CRIT) {
            __CPROVER_assert(o.solar_reconfig_action == ACT_ISOLATE_ARRAYS, "P6a OT -> S=2");
            __CPROVER_assert(o.load_shed_action >= ACT_SHED_PRIMARY, "P6b OT -> L>=2");
        }
    }

    /* P7: a non-finite sensor reading must never pass silently */
    if (NONFINITE(V) || NONFINITE(I) || NONFINITE(T) || NONFINITE(S)) {
        __CPROVER_assert(o.fault_flags != 0u, "P7 non-finite input is flagged");
    }

    /* P8: physically impossible SoC (outside 0..100 %) must be flagged */
    if (!NONFINITE(S) && (S < SOC_LO || S > SOC_HI)) {
        __CPROVER_assert(o.fault_flags != 0u, "P8 SoC outside plausible band is flagged");
    }
    return 0;
}
