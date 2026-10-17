#include "fdir_sgo.h"

/* Experimental/unverified FDIR logic provided by external development teams */
// extern FDIRResult execute_onboard_fdir(HealthTelemetry* tele);

/**
 * @brief Intercepts the output of the standard FDIR algorithm and enforces
 *        non-negotiable hardware-level safe-guard invariants derived from ECSS specs.
 */
FDIRResult process_fdir_with_sgo(HealthTelemetry* telemetry)
{
    /* 1. Execute the primary telemetry-driven FDIR state machine logic */
    FDIRResult output = execute_onboard_fdir(telemetry);

    /* 2. CRITICAL PRE-PROCESSING SENSOR MONITOR: Isolate absolute sensor dropout */
    if (telemetry->solar_voltage == 0.0f && telemetry->battery_current == 0.0f &&
        telemetry->system_temperature == 0.0f && telemetry->battery_soc == 0.0f)
    {
        output.fault_flags |= FAULT_BIT_SENSOR_DROPOUT;
        /* Do not actuate on pure zero-dropout states to prevent catastrophic false actions;
           Flag for immediate ground control software line check */
        return output;
    }

    /* 3. HARD DETECT MATRIX: Re-verify physical parameters independently of the soft FDIR logic */
    if (telemetry->battery_soc < 5.0f || telemetry->solar_voltage < ECSS_VBAT_CRITICAL_MIN) {
        output.fault_flags |= FAULT_BIT_DEEP_DISCHARGE;
    }
    if (telemetry->solar_voltage < ECSS_VBAT_NOMINAL_MIN) {
        output.fault_flags |= FAULT_BIT_UNDERVOLTAGE;
    }

    if (telemetry->battery_current > ECSS_IBAT_CRITICAL_MAX) {
        output.fault_flags |= FAULT_BIT_OVERCURRENT;
    }

    if (telemetry->system_temperature > ECSS_TBAT_CRITICAL_MAX) {
        output.fault_flags |= FAULT_BIT_OVERTEMPERATURE;
    }

    /* ====================================================================== */
    /* DETERMINISTIC SAFE-GUARD OVERLAY SANITIZATION PATHS (ECSS MANDATORY)   */
    /* ====================================================================== */

    /* INVARIANT 1: Critically Low Energy State [ECSS-E-ST-20_0020199] */
    if (output.fault_flags & FAULT_BIT_DEEP_DISCHARGE) {
        /* Force total non-essential bus isolation regardless of soft logic decisions */
        output.load_shed_action      = ACT_SHED_ALL;
        output.solar_reconfig_action = ACT_SHED_SECONDARY; /* Maintain core trickle charge */
        output.battery_limit_action  = ACT_SHED_SECONDARY;
    }
    /* INVARIANT 2: Standard Bus Undervoltage Protection */
    else if (output.fault_flags & FAULT_BIT_UNDERVOLTAGE) {
        if (output.load_shed_action < ACT_SHED_SECONDARY) {
            output.load_shed_action = ACT_SHED_SECONDARY; /* Enforce secondary shed gate */
        }
    }

    /* INVARIANT 3: Overcurrent Mitigation */
    if (output.fault_flags & FAULT_BIT_OVERCURRENT) {
        if (output.battery_limit_action < ACT_ISOLATE_ARRAYS) {
            output.battery_limit_action = ACT_ISOLATE_ARRAYS; /* Restrict source charging current */
        }
        if (output.load_shed_action == ACT_NONE) {
            output.load_shed_action = ACT_SHED_SECONDARY;    /* Protect the primary bus distribution */
        }
    }

    /* INVARIANT 4: Thermal Emergency Envelope Protection [ECSS-E-ST-20_0020140] */
    if (output.fault_flags & FAULT_BIT_OVERTEMPERATURE) {
        output.solar_reconfig_action = ACT_ISOLATE_ARRAYS; /* Shut down solar array power injection */
        if (output.load_shed_action < ACT_SHED_PRIMARY) {
            output.load_shed_action = ACT_SHED_PRIMARY;     /* Power down high-thermal payload lines */
        }
    }

    return output;
}
