/*
 * fdir_onboard.h — glue between the 6-input tree and the SGO v2 interface.
 *
 * The SGO keeps its HealthTelemetry interface (fdir_sgo.h, unchanged); the
 * tree needs six per-unit inputs. fdir_onboard_step() stores the six inputs,
 * builds pack-level telemetry from them (fdir_config.h: N_S, nP, cell Ah) and
 * runs process_fdir_with_sgo(), whose execute_onboard_fdir() evaluates the
 * tree on the stored inputs.
 *
 * Note ISS-019: HealthTelemetry.solar_voltage carries the battery/bus voltage.
 */
#ifndef FDIR_ONBOARD_H
#define FDIR_ONBOARD_H

#include "fdir_sgo.h"
#include "fdir_tree.h"

FDIRResult fdir_onboard_step(const float in[FDIR_TREE_INPUTS]);

#endif /* FDIR_ONBOARD_H */
