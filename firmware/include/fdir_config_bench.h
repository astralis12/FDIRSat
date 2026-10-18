/*
 * fdir_config_bench.h — bench-pack configuration (build flag -DFDIR_CONFIG_BENCH).
 *
 * 8S1P of Sony/Murata US18650VTC6. Same source code, tree and proofs as the
 * reference; only these values change (the tree takes per-unit inputs, so only
 * Q x N_p, which converts amps to C-rate, matters). Owner decision 18 Oct 2026.
 * Bench current scale to the reference: 20 Ah (8S4P M50) / (Q x 1) ~ 1:6.7 at Q = 3.0 Ah.
 */
#ifndef FDIR_CONFIG_BENCH_H
#define FDIR_CONFIG_BENCH_H

#define FDIR_CONFIG_NAME         "bench"
#ifndef FDIR_N_SERIES
#define FDIR_N_SERIES            8
#endif
#ifndef FDIR_N_PARALLEL
#define FDIR_N_PARALLEL          1      /* bench pack 8S1P                        */
#endif
/* PLACEHOLDER: VTC6 rated 3.0 Ah. Replace with the capacity measured at C/5
 * from 4.20 V to the 3.3 V bench floor (RUNBOOK 5.4); SoC and the bench OCV /
 * resistance tables use the same window. */
#define FDIR_CELL_CAPACITY_AH    3.0f

/* The bench charger CV is fixed at 4.2 V +/- 0.05 V per cell and cannot be set
 * lower (owner, 18 Oct), so the pack-average invariant sits at the VTC6 upper
 * charge-voltage limit 4.25 V (Sony Technical Information Rev 0.1; ISS-049).
 * With cell taps the per-cell form of ADR-0018 part 2 replaces this. */
#define FDIR_VCELL_OV_MAX        4.25f

/* Over-temperature: VTC6 discharge range -20..60 degC (Murata SDS). */
#define FDIR_TBAT_CRITICAL_MAX   60.0f

/* VTC6 charge window 0..45 degC (Murata SDS), release at 2 / 42 degC. */
#define FDIR_TBAT_CHARGE_MIN     0.0f
#define FDIR_TBAT_CHARGE_MAX     45.0f
#define FDIR_TBAT_CHARGE_REL_MIN 2.0f
#define FDIR_TBAT_CHARGE_REL_MAX 42.0f

#endif /* FDIR_CONFIG_BENCH_H */
