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
/* Same, with three battery temperature sensors: in[2] is replaced by the
 * median-of-3 vote; a single lost or disagreeing sensor sets bit 4 (ISS-050). */
FDIRResult fdir_onboard_step3(const float in[FDIR_TREE_INPUTS], const float tbat[3]);

extern int fdir_tree_last_status;   /* 1 if the last tree evaluation hit a structural fault */

#endif /* FDIR_ONBOARD_H */
