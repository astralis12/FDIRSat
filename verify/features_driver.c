/*
 * features_driver.c — host driver for verify/test_features_bitexact.py.
 * Input (binary): int32 n, then n x {v_cell, soc, i_sa_pu} float32.
 * Output: n x 3 float32 (dv60, i_sa_sun, dsoc600).
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include "fdir_features.h"

int main(int argc, char **argv)
{
    FILE *fi, *fo;
    int32_t n, k;
    float *x, o[3];
    FdirFeatures f;
    if (argc != 3 || (fi = fopen(argv[1], "rb")) == NULL || (fo = fopen(argv[2], "wb")) == NULL) {
        return 2;
    }
    if (fread(&n, sizeof n, 1, fi) != 1 || n < 1) {
        return 3;
    }
    x = malloc((size_t)n * 3 * sizeof(float));
    if (x == NULL || fread(x, sizeof(float), (size_t)n * 3, fi) != (size_t)n * 3) {
        return 4;
    }
    fdir_features_init(&f);
    for (k = 0; k < n; k++) {
        fdir_features_step(&f, x[3 * k], x[3 * k + 1], x[3 * k + 2], o);
        fwrite(o, sizeof(float), 3, fo);
    }
    free(x);
    fclose(fi);
    fclose(fo);
    return 0;
}
