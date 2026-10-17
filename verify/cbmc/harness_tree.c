/*
 * CBMC proof harness for the generated decision tree (firmware/src/fdir_tree.c).
 *
 * For ANY six IEEE-754 inputs (NaN, +/-Inf, denormals included):
 *   T1  every output action is in 0..3
 *   T2  no runtime error (bounds, pointers, overflow; run-script flags)
 *   T3  the traversal terminates within FDIR_TREE_DEPTH steps (unwinding assertions)
 * T1 is exactly the TREE_CONTRACT assumption of harness_sgo.c, so the SGO v2
 * contract-mode proof (P1-P9) holds for the deployed tree + SGO.
 */
#include <stdint.h>
#include "fdir_tree.h"

float nondet_float(void);

int main(void)
{
    float in[FDIR_TREE_INPUTS];
    uint8_t out[3];
    int k;
    for (k = 0; k < FDIR_TREE_INPUTS; k++) {
        in[k] = nondet_float();
    }
    fdir_tree_eval(in, out);
    __CPROVER_assert(out[0] <= 3u, "T1a S in 0..3");
    __CPROVER_assert(out[1] <= 3u, "T1b B in 0..3");
    __CPROVER_assert(out[2] <= 3u, "T1c L in 0..3");
    return 0;
}
