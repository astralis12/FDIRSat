/*
 * ekf_driver.c — host driver for verify/test_ekf_bitexact.py.
 *
 * Input  (binary, little-endian): int32 n, double soc_init, then n x {t, i, v} doubles.
 * Output (binary): n x {soc, std, innov, innov_var, status} doubles; record 0
 * is the initial state (innov = 0, status = 0), as in ecm_ekf.run_ekf.
 * Usage: ekf_driver in.bin out.bin
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include "soc_ekf.h"

int main(int argc, char **argv)
{
    FILE *fi, *fo;
    int32_t n, k;
    double soc_init, *buf, rec[5];
    SocEkf e;
    SocEkfStep s;

    if (argc != 3 || (fi = fopen(argv[1], "rb")) == NULL || (fo = fopen(argv[2], "wb")) == NULL) {
        fprintf(stderr, "usage: ekf_driver in.bin out.bin\n");
        return 2;
    }
    if (fread(&n, sizeof n, 1, fi) != 1 || fread(&soc_init, sizeof soc_init, 1, fi) != 1 || n < 1) {
        return 3;
    }
    buf = malloc((size_t)n * 3 * sizeof(double));
    if (buf == NULL || fread(buf, sizeof(double), (size_t)n * 3, fi) != (size_t)n * 3) {
        return 4;
    }
    soc_ekf_init(&e, (ekf_real_t)soc_init);
    rec[0] = (double)soc_ekf_soc(&e);
    rec[1] = (double)soc_ekf_std(&e);
    rec[2] = rec[3] = rec[4] = 0.0;
    fwrite(rec, sizeof(double), 5, fo);
    for (k = 1; k < n; k++) {
        const double *p = buf + 3 * (k - 1), *c = buf + 3 * k;
        uint8_t st = soc_ekf_step(&e, (ekf_real_t)p[1], (ekf_real_t)c[1], (ekf_real_t)c[2],
                                  (ekf_real_t)(c[0] - p[0]), &s);
        rec[0] = (double)soc_ekf_soc(&e);
        rec[1] = (double)soc_ekf_std(&e);
        rec[2] = (double)s.innov;
        rec[3] = (double)s.innov_var;
        rec[4] = (double)st;
        fwrite(rec, sizeof(double), 5, fo);
    }
    free(buf);
    fclose(fi);
    fclose(fo);
    return 0;
}
