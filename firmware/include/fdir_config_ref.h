/*
 * fdir_config_ref.h — reference-mission configuration (default build).
 *
 * 8S4P flight-like pack of LG INR21700-M50 cells (NMC811, 5 Ah) — the same cell as
 * the DFN plant (PyBaMM Chen2020), so capacity, DFN parameters and limits come from
 * one cell (ADR-0023; XJTU-SPS does not publish its cell, ISS-030). Datasheet:
 * LG Chem INR21700 M50 Product Specification LRB-PS-CY18.2Wh-M50, 2017-09-22, Rev 0
 * (docs/datasheets/). Drives the simulation, residual risk and the CBMC "ref" proofs. Selected by fdir_config.h
 * unless FDIR_CONFIG_BENCH is defined. Owner decision 18 Oct 2026: this file
 * stays as it was; the bench pack has its own header (fdir_config_bench.h).
 */
#ifndef FDIR_CONFIG_REF_H
#define FDIR_CONFIG_REF_H

#define FDIR_CONFIG_NAME         "ref"
#ifndef FDIR_N_SERIES
#define FDIR_N_SERIES            8      /* 8S: 28.8 V nominal reference bus      */
#endif
#ifndef FDIR_N_PARALLEL
#define FDIR_N_PARALLEL          4      /* 8S4P flight-like                       */
#endif
#define FDIR_CELL_CAPACITY_AH    5.0f   /* M50 nominal 18.2 Wh / 3.63 V; spec 1C = 4.85 A (min) */

#define FDIR_VCELL_OV_MAX        4.20f  /* SGO v3 overvoltage, pack average (ADR-0018) */

/* Over-temperature (SGO invariant 4) [degC]: M50 discharge range -20..55 (spec 2.7;
 * the warning section says 60, the stricter 55 is used). */
#define FDIR_TBAT_CRITICAL_MAX   55.0f

/* Charge-temperature window (SGO v3 invariant 6, ISS-050) [degC]: M50 spec 2.7, 0..45. */
#define FDIR_TBAT_CHARGE_MIN     0.0f
#define FDIR_TBAT_CHARGE_MAX     45.0f
#define FDIR_TBAT_CHARGE_REL_MIN 2.0f   /* release (hysteresis), owner 18 Oct     */
#define FDIR_TBAT_CHARGE_REL_MAX 42.0f

#endif /* FDIR_CONFIG_REF_H */
