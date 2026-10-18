/*
 * fdir_sgo_v3.c — SGO safety layer, revision 3 (ADR-0018, accepted 17 Oct 2026).
 *
 * v3 = v2 + invariant 5: pack-average cell voltage above FDIR_VCELL_OV_MAX ->
 * arrays isolated (S=2), charge limited (B>=2), flag bit 5. Applied last, so it
 * only raises actions set by invariants 1-4. Closes the BCR-short gap (ISS-041).
 * Everything below this paragraph is unchanged from v2.
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

static int in_range(float x, float lo, float hi)
{
    /* false for NaN: every comparison with NaN is false */
    return (x >= lo) && (x <= hi);
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
    /* INVARIANT 5 (v3): cell overvoltage, pack average */
    if (telemetry->solar_voltage > FDIR_VBAT_OV_MAX) {
        output.fault_flags |= FAULT_BIT_OVERVOLTAGE;
        output.solar_reconfig_action = ACT_ISOLATE_ARRAYS;
        if (output.battery_limit_action < ACT_ISOLATE_ARRAYS) {
            output.battery_limit_action = ACT_ISOLATE_ARRAYS;
        }
    }
    return output;
}
