/*
 * fdir_config_ref.h — reference-mission configuration (default build).
 *
 * 8S4P flight-like pack of XJTU-type 18650 NCM cells. Drives the simulation,
 * the residual-risk numbers and the CBMC "ref" proofs. Selected by fdir_config.h
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
#define FDIR_CELL_CAPACITY_AH    2.0f   /* XJTU-type 18650 NCM, 2.0 Ah            */

#define FDIR_VCELL_OV_MAX        4.20f  /* SGO v3 overvoltage, pack average (ADR-0018) */

/* Charge-temperature window (SGO v3 invariant 6, ISS-050) [degC].
 * ASSUMED generic Li-ion NCM 18650 window until the XJTU cell datasheet is
 * traced; same values as the bench VTC6 (Murata SDS 0..45 degC). */
#define FDIR_TBAT_CHARGE_MIN     0.0f
#define FDIR_TBAT_CHARGE_MAX     45.0f
#define FDIR_TBAT_CHARGE_REL_MIN 2.0f   /* release (hysteresis), owner 18 Oct     */
#define FDIR_TBAT_CHARGE_REL_MAX 42.0f

#endif /* FDIR_CONFIG_REF_H */
