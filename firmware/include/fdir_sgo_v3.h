/*
 * fdir_sgo_v3.h — additions of SGO v3 (ADR-0018) to the unchanged fdir_sgo.h interface.
 * Bit 5 of fault_flags is new; bits 0..4 keep their v2 meaning. The tree may
 * still set only bits 0..4 (its output contract is unchanged).
 */
#ifndef FDIR_SGO_V3_H
#define FDIR_SGO_V3_H

#include "fdir_sgo.h"

#define FAULT_BIT_OVERVOLTAGE    (1U << 5)
#define FDIR_FLAG_MASK_V3        0x3FU

#endif /* FDIR_SGO_V3_H */
