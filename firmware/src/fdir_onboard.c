/*
 * fdir_onboard.c — glue between the 6-input tree and the SGO v2 (see header).
 */
#include <string.h>
#include "fdir_onboard.h"
#include "fdir_config.h"

static float g_in[FDIR_TREE_INPUTS];

/* The SGO's "raw FDIR policy": the generated tree on the stored inputs. */
FDIRResult execute_onboard_fdir(HealthTelemetry *telemetry)
{
    FDIRResult r;
    uint8_t a[3];
    (void)telemetry;
    fdir_tree_eval(g_in, a);
    r.fault_flags = 0U;
    r.solar_reconfig_action = a[0];
    r.battery_limit_action = a[1];
    r.load_shed_action = a[2];
    return r;
}

FDIRResult fdir_onboard_step(const float in[FDIR_TREE_INPUTS])
{
    HealthTelemetry t;
    memcpy(g_in, in, sizeof g_in);
    t.solar_voltage      = in[0] * (float)FDIR_N_SERIES;                                   /* pack V */
    t.battery_current    = in[1] * FDIR_CELL_CAPACITY_AH * (float)FDIR_N_PARALLEL;          /* pack A */
    t.system_temperature = in[2];
    t.battery_soc        = in[3] * 100.0f;                                                  /* % */
    return process_fdir_with_sgo(&t);
}
