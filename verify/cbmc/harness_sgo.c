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
#ifdef SGO_V3
#include "fdir_sgo_v3.h"
#endif

#if defined(SGO_V2) || defined(SGO_V3)
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
int     nondet_int(void);

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

#ifdef SGO_V3
    /* Invariant 6 state: any latch value before the call (0 or 1, its only values) */
    const uint8_t latch0 = nondet_uint8();
    __CPROVER_assume(latch0 <= 1u);
    fdir_sgo_v3_charge_inhibit = latch0;
    /* Either the glue's median-of-3 vote supplies T (= t_hi) and t_lo, or a single
     * sensor is used (t_lo = T), as in fdir_onboard_step3 / fdir_onboard_step. */
    float tb[3], th = 0.0f, tl;
    tb[0] = nondet_float(); tb[1] = nondet_float(); tb[2] = nondet_float();
    const int use3 = nondet_int();
    if (use3) {
        (void)fdir_temp_vote3(tb, &th, &tl);
        t.system_temperature = th;
    } else {
        tl = t.system_temperature;
    }
#endif

    const float V = t.solar_voltage, I = t.battery_current;
    const float T = t.system_temperature, S = t.battery_soc;

    FDIRResult o = process_fdir_with_sgo(&t);

    const int dropout = (V == 0.0f && I == 0.0f && T == 0.0f && S == 0.0f);

    /* P1: every action field stays in the actuator range 0..3 */
    __CPROVER_assert(o.solar_reconfig_action <= 3u, "P1a solar action in 0..3");
    __CPROVER_assert(o.battery_limit_action  <= 3u, "P1b battery action in 0..3");
    __CPROVER_assert(o.load_shed_action      <= 3u, "P1c load action in 0..3");

#ifdef SGO_V3
    /* P2 (v3): fault mask uses only defined bits 0..6 */
    __CPROVER_assert((o.fault_flags & ~0x7Fu) == 0u, "P2 fault mask bits 0..6 only");
#else
    /* P2: fault mask uses only defined bits 0..4 */
    __CPROVER_assert((o.fault_flags & ~0x1Fu) == 0u, "P2 fault mask bits 0..4 only");
#endif

    const int plausible = PLAUSIBLE(V, I, T, S);

#if defined(SGO_V2) || defined(SGO_V3)
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

#ifdef SGO_V3
    /* P10 (v3): cell overvoltage (pack average) -> arrays isolated, charge limited, flagged */
    if (!dropout && plausible && V > FDIR_VBAT_OV_MAX) {
        __CPROVER_assert(o.solar_reconfig_action == ACT_ISOLATE_ARRAYS, "P10a OV -> S=2");
        __CPROVER_assert(o.battery_limit_action >= ACT_ISOLATE_ARRAYS, "P10b OV -> B>=2");
        __CPROVER_assert(o.fault_flags & (1U << 5), "P10c OV flagged (bit 5)");
    }

    /* P11 (v3, ISS-050): charge-temperature window with hysteresis */
    __CPROVER_assert(fdir_sgo_v3_charge_inhibit <= 1u, "P11d latch stays 0 or 1");
    if (!dropout && plausible) {
        const int out_win = (T > FDIR_TBAT_CHARGE_MAX) || (tl < FDIR_TBAT_CHARGE_MIN);
        const int in_rel  = (T < FDIR_TBAT_CHARGE_REL_MAX) && (tl > FDIR_TBAT_CHARGE_REL_MIN);
        if (out_win) {
            __CPROVER_assert(fdir_sgo_v3_charge_inhibit == 1u, "P11a outside window -> latch set");
            __CPROVER_assert(o.battery_limit_action >= ACT_ISOLATE_ARRAYS, "P11a outside window -> B>=2");
            __CPROVER_assert(o.fault_flags & (1U << 6), "P11a outside window flagged (bit 6)");
        }
        if (latch0 == 1u && !in_rel) {
            __CPROVER_assert(fdir_sgo_v3_charge_inhibit == 1u, "P11b hysteresis band -> latch held");
            __CPROVER_assert(o.battery_limit_action >= ACT_ISOLATE_ARRAYS, "P11b latch held -> B>=2");
        }
        if (!out_win && in_rel) {
            __CPROVER_assert(fdir_sgo_v3_charge_inhibit == 0u, "P11c inside release band -> latch cleared");
        }
    }
    /* P11e-g: median-of-3 vote (fdir_temp_vote3) */
    if (use3) {
        int nv = 0, i, j;
        for (i = 0; i < 3; i++) {
            nv += IN(tb[i], FDIR_TBAT_PLAUSIBLE_MIN, FDIR_TBAT_PLAUSIBLE_MAX);
        }
        for (i = 0; i < 3; i++) {
            for (j = i + 1; j < 3; j++) {
                if (IN(tb[i], FDIR_TBAT_PLAUSIBLE_MIN, FDIR_TBAT_PLAUSIBLE_MAX) &&
                    IN(tb[j], FDIR_TBAT_PLAUSIBLE_MIN, FDIR_TBAT_PLAUSIBLE_MAX)) {
                    const float mx = tb[i] > tb[j] ? tb[i] : tb[j];
                    const float mn = tb[i] > tb[j] ? tb[j] : tb[i];
                    __CPROVER_assert(th <= mx && tl >= mn, "P11e vote bounded by any two valid sensors (one-fault tolerant)");
                }
            }
        }
        if (nv < 2) {
            __CPROVER_assert(o.fault_flags & FAULT_BIT_SENSOR_DROPOUT, "P11f fewer than 2 valid sensors -> bit 4");
            __CPROVER_assert(o.solar_reconfig_action == 0u && o.battery_limit_action == 1u &&
                             o.load_shed_action == 1u, "P11f fewer than 2 valid sensors -> safe default");
        }
        if (nv == 3) {
            __CPROVER_assert(th == tl, "P11g three valid sensors -> single median value");
        }
    }
#endif

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
