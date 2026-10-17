/*
 * tree_driver.c — host driver for verify/test_tree_bitexact.py.
 * Input (binary): int32 n, then n x 6 float32. Output: n x 3 uint8 (S, B, L).
 * Usage: tree_driver in.bin out.bin
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include "fdir_tree.h"

int main(int argc, char **argv)
{
    FILE *fi, *fo;
    int32_t n, k;
    float *x;
    uint8_t a[3];
    if (argc != 3 || (fi = fopen(argv[1], "rb")) == NULL || (fo = fopen(argv[2], "wb")) == NULL) {
        return 2;
    }
    if (fread(&n, sizeof n, 1, fi) != 1 || n < 1) {
        return 3;
    }
    x = malloc((size_t)n * FDIR_TREE_INPUTS * sizeof(float));
    if (x == NULL || fread(x, sizeof(float), (size_t)n * FDIR_TREE_INPUTS, fi) != (size_t)n * FDIR_TREE_INPUTS) {
        return 4;
    }
    for (k = 0; k < n; k++) {
        fdir_tree_eval(&x[(size_t)k * FDIR_TREE_INPUTS], a);
        fwrite(a, 1, 3, fo);
    }
    free(x);
    fclose(fi);
    fclose(fo);
    return 0;
}
