/*
 * fdir_sgo_v3.c — SGO safety layer, revision 3 (ADR-0018, accepted 17 Oct 2026).
 *
 * v3 = v2 + invariant 5: pack-average cell voltage above FDIR_VCELL_OV_MAX ->
 * arrays isolated (S=2), charge limited (B>=2), flag bit 5. Applied last, so it
 * only raises actions set by invariants 1-4. Closes the BCR-short gap (ISS-041).
 *
 * v3 also has invariant 6 (ISS-050, approved 18 Oct 2026): battery outside the
 * charge-temperature window [FDIR_TBAT_CHARGE_MIN, _MAX] -> charging inhibited
 * (B>=2: the BCR stops charging, arrays keep supplying the loads), flag bit 6.
 * Latched with hysteresis: released only inside [_REL_MIN, _REL_MAX]. The low
 * check uses the lowest credible reading of the median-of-3 vote
 * (fdir_temp_vote3), the high checks the highest. Applied after invariant 5.
 *
 * v3.2 adds invariant 7 (ISS-059, ADR-0024, approved 19 Oct 2026): cell current above its
 * temperature-band continuous limit (fdir_config_{ref,bench}.h, docs/cell_limits_trace.md),
 * with a measurement margin, a temperature margin for band selection, persistence and
 * hysteresis. Charge over limit -> B>=2; discharge over limit -> L>=2; flag bit 7.
 * Invariants 5-7 only raise actions (P14, monotone composition).
 * Everything below this paragraph, up to invariant 5, is unchanged from v2.
 *
 * (v2 header follows)
 * fdir_sgo_v2.c — SGO safety layer, revision 2 (PROPOSED, see ADR-0017 draft).
 *
 * Changes vs fdir_sgo.c, each closing a CBMC counterexample (verify/cbmc/logs):
 *   C1  Output sanitisation: any tree action > 3 or flag outside bits 0..4 is
 *       treated as a corrupted tree output (e.g. SEU in the tree tables) and
 *       replaced by the safe default before the invariants run.          [P1, P2]
 *   C2  Plausibility check: NaN/Inf or out-of-range telemetry sets the sensor
 *       fault bit and drives the safe default instead of passing silently. [P7, P8]
 *   C3  Thresholds come from fdir_config.h (28 V / 8S reference), not literals.
 * Unchanged: invariant order and actions (incl. ADR-0015 DD+OT behaviour).
 *
 * Interface is unchanged (same FDIRResult layout), so main.c and the host
 * decoder need no change. Bit 4 now means "sensor dropout OR invalid reading".
 */
#include <math.h>
#include "fdir_sgo.h"
#include "fdir_sgo_v3.h"
#include "fdir_config.h"

/* Safe default when telemetry or tree output cannot be trusted:
 * keep arrays connected (S=0), trickle-charge path only (B=1, ADR-0015),
 * shed secondary loads (L=1). PROPOSED — confirm in FMECA (M2). */
#define SAFE_S  ACT_NONE
#define SAFE_B  ACT_SHED_SECONDARY
#define SAFE_L  ACT_SHED_SECONDARY

FdirSgoState fdir_sgo_v3_state;
#ifdef FDIR_PROOF_HOOKS
FDIRResult fdir_sgo_v3_base;
#endif

static const float k_chg_e[FDIR_CHG_BAND_N + 1] = FDIR_CHG_BAND_EDGES;
static const float k_chg_c[FDIR_CHG_BAND_N] = FDIR_CHG_BAND_C;
static const float k_dis_e[FDIR_DIS_BAND_N > 1 ? FDIR_DIS_BAND_N - 1 : 1] = FDIR_DIS_BAND_EDGES;
static const float k_dis_c[FDIR_DIS_BAND_N] = FDIR_DIS_BAND_C;

static float fmin2(float x, float y) { return (y < x) ? y : x; }

/* charge limit at one temperature: band k = [e[k], e[k+1]) (last band closed), else 0 */
static float chg_band_at(float t)
{
    unsigned k;
    for (k = 0U; k < (unsigned)FDIR_CHG_BAND_N; k++) {
        const int last = (k + 1U == (unsigned)FDIR_CHG_BAND_N);
        if (t >= k_chg_e[k] && (t < k_chg_e[k + 1U] || (last && t <= k_chg_e[k + 1U]))) {
            return k_chg_c[k];
        }
    }
    return 0.0f;                       /* outside every charge band (also NaN) */
}

/* discharge limit at one temperature: band 0 below e[0], band k in [e[k-1], e[k]), last above */
static float dis_band_at(float t)
{
    unsigned k = 0U;
    while (k + 1U < (unsigned)FDIR_DIS_BAND_N && t >= k_dis_e[k]) {
        k++;
    }
    return k_dis_c[k];                 /* NaN -> band 0 (coldest; lowest limit for the M50) */
}

float fdir_ilim_charge_c(float a, float b)
{
    float m;
    unsigned k;
    if (!(a <= b)) {
        return 0.0f;                   /* NaN or inverted interval: no charge allowed */
    }
    m = fmin2(chg_band_at(a), chg_band_at(b));
    for (k = 0U; k <= (unsigned)FDIR_CHG_BAND_N; k++) {
        if (k_chg_e[k] > a && k_chg_e[k] <= b) {
            m = fmin2(m, chg_band_at(k_chg_e[k]));
        }
    }
    return m;
}

float fdir_ilim_discharge_c(float a, float b)
{
    float m;
    unsigned k;
    if (!(a <= b)) {
        return k_dis_c[0];
    }
    m = fmin2(dis_band_at(a), dis_band_at(b));
    for (k = 0U; k + 1U < (unsigned)FDIR_DIS_BAND_N; k++) {
        if (k_dis_e[k] > a && k_dis_e[k] <= b) {
            m = fmin2(m, dis_band_at(k_dis_e[k]));
        }
    }
    return m;
}

/* persistence + hysteresis latch of invariant 7 */
static void ilim_update(uint8_t *on, uint8_t *cnt, uint8_t *rel, int over, int below)
{
    if (over) {
        if (*cnt < FDIR_ILIM_PERSIST) {
            (*cnt)++;
        }
    } else {
        *cnt = 0U;
    }
    if (*cnt >= FDIR_ILIM_PERSIST) {
        *on = 1U;
        *rel = 0U;
    } else if (*on != 0U) {
        if (below) {
            if (*rel < FDIR_ILIM_RELEASE) {
                (*rel)++;
            }
        } else {
            *rel = 0U;
        }
        if (*rel >= FDIR_ILIM_RELEASE) {
            *on = 0U;
            *rel = 0U;
        }
    } else {
        *rel = 0U;
    }
}
static uint8_t g_tlo_armed = 0U;   /* 1: g_tlo holds the vote for the next SGO call */
static float g_tlo;

static int in_range(float x, float lo, float hi)
{
    /* false for NaN: every comparison with NaN is false */
    return (x >= lo) && (x <= hi);
}

int fdir_temp_vote3(const float t[3], float *t_hi, float *t_lo)
{
    float v[3];
    unsigned n = 0U, k;
    int single_fault = 0;
    for (k = 0U; k < 3U; k++) {
        if (in_range(t[k], FDIR_TBAT_PLAUSIBLE_MIN, FDIR_TBAT_PLAUSIBLE_MAX)) {
            v[n++] = t[k];
        }
    }
    if (n == 3U) {
        float a = v[0], b = v[1], c = v[2], m;
        if (a > b) { m = a; a = b; b = m; }
        if (b > c) { m = b; b = c; c = m; }
        if (a > b) { m = a; a = b; b = m; }
        *t_hi = b;
        *t_lo = b;
        single_fault = (b - a > FDIR_TBAT_SENSOR_DISAGREE) || (c - b > FDIR_TBAT_SENSOR_DISAGREE);
    } else if (n == 2U) {
        *t_hi = (v[0] > v[1]) ? v[0] : v[1];
        *t_lo = (v[0] > v[1]) ? v[1] : v[0];
        single_fault = 1;
    } else {
        *t_hi = FDIR_TBAT_VOTE_INVALID;
        *t_lo = FDIR_TBAT_VOTE_INVALID;
        single_fault = 1;
    }
    g_tlo = *t_lo;
    g_tlo_armed = 1U;
    return single_fault;
}

static int telemetry_plausible(const HealthTelemetry *t)
{
    return in_range(t->solar_voltage,      FDIR_VBAT_PLAUSIBLE_MIN, FDIR_VBAT_PLAUSIBLE_MAX)
        && in_range(t->battery_current,    -FDIR_IBAT_PLAUSIBLE_ABS, FDIR_IBAT_PLAUSIBLE_ABS)
        && in_range(t->system_temperature, FDIR_TBAT_PLAUSIBLE_MIN, FDIR_TBAT_PLAUSIBLE_MAX)
        && in_range(t->battery_soc,        FDIR_SOC_PLAUSIBLE_MIN,  FDIR_SOC_PLAUSIBLE_MAX);
}

static int tree_output_valid(const FDIRResult *r)
{
    return (r->solar_reconfig_action <= ACT_SHED_ALL)
        && (r->battery_limit_action  <= ACT_SHED_ALL)
        && (r->load_shed_action      <= ACT_SHED_ALL)
        && ((r->fault_flags & ~0x1Fu) == 0u);
}

static void apply_safe_default(FDIRResult *o)
{
    o->solar_reconfig_action = SAFE_S;
    o->battery_limit_action  = SAFE_B;
    o->load_shed_action      = SAFE_L;
}

FDIRResult process_fdir_with_sgo(HealthTelemetry *telemetry)
{
    FDIRResult output = execute_onboard_fdir(telemetry);
    /* low-temperature reading for invariant 6: the vote's t_lo if armed, else T */
    const float t_lo = g_tlo_armed ? g_tlo : telemetry->system_temperature;
    g_tlo_armed = 0U;

    /* C1: corrupted tree output -> safe default, keep only defined flag bits */
    if (!tree_output_valid(&output)) {
        output.fault_flags &= 0x1Fu;
        apply_safe_default(&output);
    }

    /* Sensor dropout (all channels exactly zero) — unchanged semantics */
    if (telemetry->solar_voltage == 0.0f && telemetry->battery_current == 0.0f &&
        telemetry->system_temperature == 0.0f && telemetry->battery_soc == 0.0f)
    {
        output.fault_flags |= FAULT_BIT_SENSOR_DROPOUT;
        return output;
    }

    /* C2: implausible or non-finite telemetry -> flag + safe default */
    if (!telemetry_plausible(telemetry)) {
        output.fault_flags |= FAULT_BIT_SENSOR_DROPOUT;
        apply_safe_default(&output);
        return output;
    }

    /* Hard detect matrix (C3: thresholds from config) */
    if (telemetry->battery_soc < FDIR_SOC_CRITICAL_MIN ||
        telemetry->solar_voltage < FDIR_VBAT_CRITICAL_MIN) {
        output.fault_flags |= FAULT_BIT_DEEP_DISCHARGE;
    }
    if (telemetry->solar_voltage < FDIR_VBAT_NOMINAL_MIN) {
        output.fault_flags |= FAULT_BIT_UNDERVOLTAGE;
    }
    if (telemetry->battery_current > FDIR_IBAT_CRITICAL_MAX) {
        output.fault_flags |= FAULT_BIT_OVERCURRENT;
    }
    if (telemetry->system_temperature > FDIR_TBAT_CRITICAL_MAX) {
        output.fault_flags |= FAULT_BIT_OVERTEMPERATURE;
    }

    /* INVARIANT 1: deep discharge */
    if (output.fault_flags & FAULT_BIT_DEEP_DISCHARGE) {
        output.load_shed_action      = ACT_SHED_ALL;
        output.solar_reconfig_action = ACT_SHED_SECONDARY;
        output.battery_limit_action  = ACT_SHED_SECONDARY;
    }
    /* INVARIANT 2: undervoltage */
    else if (output.fault_flags & FAULT_BIT_UNDERVOLTAGE) {
        if (output.load_shed_action < ACT_SHED_SECONDARY) {
            output.load_shed_action = ACT_SHED_SECONDARY;
        }
    }
    /* INVARIANT 3: overcurrent */
    if (output.fault_flags & FAULT_BIT_OVERCURRENT) {
        if (output.battery_limit_action < ACT_ISOLATE_ARRAYS) {
            output.battery_limit_action = ACT_ISOLATE_ARRAYS;
        }
        if (output.load_shed_action == ACT_NONE) {
            output.load_shed_action = ACT_SHED_SECONDARY;
        }
    }
    /* INVARIANT 4: over-temperature */
    if (output.fault_flags & FAULT_BIT_OVERTEMPERATURE) {
        output.solar_reconfig_action = ACT_ISOLATE_ARRAYS;
        if (output.load_shed_action < ACT_SHED_PRIMARY) {
            output.load_shed_action = ACT_SHED_PRIMARY;
        }
    }
#ifdef FDIR_PROOF_HOOKS
    fdir_sgo_v3_base = output;
#endif
    /* INVARIANT 5 (v3): cell overvoltage, pack average */
    if (telemetry->solar_voltage > FDIR_VBAT_OV_MAX) {
        output.fault_flags |= FAULT_BIT_OVERVOLTAGE;
        if (output.solar_reconfig_action < ACT_ISOLATE_ARRAYS) {   /* raise-only (P14; was a set) */
            output.solar_reconfig_action = ACT_ISOLATE_ARRAYS;
        }
        if (output.battery_limit_action < ACT_ISOLATE_ARRAYS) {
            output.battery_limit_action = ACT_ISOLATE_ARRAYS;
        }
    }
    /* INVARIANT 6 (v3, ISS-050): charge-temperature window, latched with hysteresis.
     * T (= t_hi) is plausible here. If t_lo were NaN, every comparison with it is
     * false, so the latch could only be set by t_hi and never released. */
    {
        const float t_hi = telemetry->system_temperature;
        if (t_hi > FDIR_TBAT_CHARGE_MAX || t_lo < FDIR_TBAT_CHARGE_MIN) {
            fdir_sgo_v3_charge_inhibit = 1U;
        } else if (t_hi < FDIR_TBAT_CHARGE_REL_MAX && t_lo > FDIR_TBAT_CHARGE_REL_MIN) {
            fdir_sgo_v3_charge_inhibit = 0U;
        } else if (fdir_sgo_v3_charge_inhibit != 0U) {
            fdir_sgo_v3_charge_inhibit = 1U;   /* hold inside the hysteresis band */
        }
        if (fdir_sgo_v3_charge_inhibit != 0U) {
            output.fault_flags |= FAULT_BIT_CHARGE_TEMP;
            if (output.battery_limit_action < ACT_ISOLATE_ARRAYS) {
                output.battery_limit_action = ACT_ISOLATE_ARRAYS;
            }
        }
    }
    /* INVARIANT 7 (v3.2, ISS-059): temperature-band cell current limits. The band is the
     * lowest over [t_lo - m, t_hi + m]; thresholds in pack amps with the measurement margin. */
    {
        const float q = FDIR_CELL_CAPACITY_AH * (float)FDIR_N_PARALLEL;
        const float a = t_lo - FDIR_ILIM_T_MARGIN;
        const float b = telemetry->system_temperature + FDIR_ILIM_T_MARGIN;
        const float thr_c = ((1.0f - FDIR_ILIM_MEAS_MARGIN) * fdir_ilim_charge_c(a, b)) * q;
        const float thr_d = ((1.0f - FDIR_ILIM_MEAS_MARGIN) * fdir_ilim_discharge_c(a, b)) * q;
        const float i = telemetry->battery_current;          /* discharge > 0 */
        ilim_update(&fdir_sgo_v3_state.chg_on, &fdir_sgo_v3_state.chg_cnt, &fdir_sgo_v3_state.chg_rel,
                    -i > thr_c, -i < FDIR_ILIM_REL_FRAC * thr_c);
        ilim_update(&fdir_sgo_v3_state.dis_on, &fdir_sgo_v3_state.dis_cnt, &fdir_sgo_v3_state.dis_rel,
                    i > thr_d, i < FDIR_ILIM_REL_FRAC * thr_d);
        if (fdir_sgo_v3_state.chg_on != 0U) {
            output.fault_flags |= FAULT_BIT_CURRENT_LIMIT;
            if (output.battery_limit_action < ACT_ISOLATE_ARRAYS) {
                output.battery_limit_action = ACT_ISOLATE_ARRAYS;
            }
        }
        if (fdir_sgo_v3_state.dis_on != 0U) {
            output.fault_flags |= FAULT_BIT_CURRENT_LIMIT;
            if (output.load_shed_action < ACT_SHED_PRIMARY) {
                output.load_shed_action = ACT_SHED_PRIMARY;
            }
        }
    }
    return output;
}
