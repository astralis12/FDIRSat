/*
 * CBMC proof harness: the tree evaluator is safe for ANY table contents (item 5).
 *
 * Built with -DFDIR_TREE_IN_RAM -DFDIR_TREE_PROOF_HAVOC: fdir_tree_init() copies the
 * tables to RAM and then havocs every RAM table (any bytes, as after arbitrary
 * SEUs). For any such tables and any six-to-nine float inputs:
 *   A1  every output action is in 0..3
 *   A2  no runtime error: out-of-bounds table or input access, overflow (run-script flags)
 *   A3  the walk terminates within FDIR_TREE_DEPTH + 1 steps (unwinding assertions)
 *   A4  status 1 (structural fault) gives the SGO safe default S=0, B=1, L=1
 * A1-A2 mean an upset in the tree tables can change the decision but never crash
 * the evaluator or leave the SGO's tree contract; the CRC scrub then restores them.
 */
#include <stdint.h>
#include "fdir_tree.h"

float nondet_float(void);
uint32_t nondet_uint32(void);

uint32_t fdir_tree_crc32(const void *data, uint32_t len)
{
    (void)data;
    (void)len;
    return nondet_uint32();                      /* the scrub value is irrelevant here */
}

int main(void)
{
    float in[FDIR_TREE_INPUTS];
    uint8_t out[3];
    int k, st;
    fdir_tree_init();
    for (k = 0; k < FDIR_TREE_INPUTS; k++) {
        in[k] = nondet_float();
    }
    st = fdir_tree_eval(in, out);
    __CPROVER_assert(out[0] <= 3u && out[1] <= 3u && out[2] <= 3u, "A1 outputs in 0..3 for any tables");
    if (st != 0) {
        __CPROVER_assert(out[0] == 0u && out[1] == 1u && out[2] == 1u, "A4 structural fault -> safe default");
    }
    return 0;
}
