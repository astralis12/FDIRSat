"""On-target EKF test: the DUT (firmware/pio/dut) runs soc_ekf_step on the
STM32F411 FPU; this host streams DFN profiles over the UART and compares every
step with the Python reference (sim/ecm_ekf.py), as verify/test_ekf_bitexact.py
does for the host builds.

Tolerance (same as the host float build): |dSoC|, |dSigma| <= 1e-4; 0 mismatches
and no guard activation is the gate. Also reports the cycle count per step
(DWT->CYCCNT, HSI 16 MHz, 0 wait states): max over all steps = measured WCET.
Writes results/hil_ekf.json.

Usage: python verify/hil_ekf.py --port COM5 [--seeds 14 6]
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np
import serial

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sim"))
sys.path.insert(0, str(ROOT / "codegen"))
from ecm_ekf import run_ekf  # noqa: E402
from emit_ekf_tables import reference_model  # noqa: E402

TOL = 1e-4
F_CPU_HZ = 16e6


def crc16(b: bytes) -> int:
    c = 0xFFFF
    for x in b:
        c ^= x << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


class Dut:
    def __init__(self, port: str, baud: int = 460800):
        self.s = serial.Serial(port, baud, timeout=2.0)
        time.sleep(0.1)
        self.s.reset_input_buffer()

    def call(self, cmd: bytes, payload: bytes = b"") -> tuple[bytes, bytes]:
        body = cmd + bytes([len(payload)]) + payload
        self.s.write(b"\xA5\x5A" + body + struct.pack("<H", crc16(body)))
        while True:
            b = self.s.read(1)
            if not b:
                raise TimeoutError("no reply from DUT")
            if b == b"\xA5" and self.s.read(1) == b"\x5A":
                break
        head = self.s.read(2)
        data = self.s.read(head[1])
        crc = struct.unpack("<H", self.s.read(2))[0]
        if crc != crc16(head + data):
            raise IOError("bad CRC from DUT")
        return head[:1], data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--dfn", default=str(ROOT / "data" / "dfn"))
    ap.add_argument("--seeds", type=int, nargs="+", default=[14, 6])
    ap.add_argument("--soc-init-error", type=float, default=0.20)
    ap.add_argument("--out", default=str(ROOT / "results" / "hil_ekf.json"))
    a = ap.parse_args()
    ecm = json.load(open(ROOT / "results" / "ecm_scheduled.json"))
    cv = json.load(open(ROOT / "results" / "ekf_sigma_cv.json"))
    tab, ocv, n = reference_model(ecm, cv, Path(a.dfn))

    dut = Dut(a.port)
    cmd, ver = dut.call(b"P")
    res = {"port": a.port, "firmware": ver.decode(), "f_cpu_hz": F_CPU_HZ, "tolerance": TOL, "seeds": {}}
    print("DUT:", res["firmware"])
    total_bad, total_guard, cyc_all = 0, 0, []
    for s in a.seeds:
        va = np.load(Path(a.dfn) / f"valid_25C_s{s}.npz")
        t, i, v = va["t"], va["i"], va["v"]
        soc0 = float(va["soc"][0]) - a.soc_init_error
        ref = run_ekf(tab, ocv, t, i, v, soc0, p0_soc=n["p0_soc"], q_soc=n["q_soc"], q_rc=n["q_rc"], r_v=n["r_v"])
        dut.call(b"I", struct.pack("<f", soc0))
        out = np.empty((t.size, 2))
        out[0] = [np.float32(soc0), np.sqrt(np.float32(n["p0_soc"]))]
        status, cycles = np.zeros(t.size, int), np.zeros(t.size, int)
        t0 = time.time()
        for k in range(1, t.size):
            c, d = dut.call(b"S", struct.pack("<4f", i[k - 1], i[k], v[k], t[k] - t[k - 1]))
            if c != b"s":
                raise IOError(f"DUT error frame {d!r} at step {k}")
            soc, sd, _, _ = struct.unpack("<4f", d[:16])
            out[k] = [soc, sd]
            status[k] = d[16]
            cycles[k] = struct.unpack("<I", d[17:21])[0]
        ds, dsd = np.abs(out[:, 0] - ref["soc_est"]), np.abs(out[:, 1] - ref["soc_std"])
        bad = int(np.sum((ds > TOL) | (dsd > TOL)))
        guard = int(np.count_nonzero(status))
        total_bad += bad
        total_guard += guard
        cyc_all.append(cycles[1:])
        res["seeds"][str(s)] = {"samples": int(t.size), "max_abs_dsoc": float(ds.max()), "max_abs_dsigma": float(dsd.max()),
                                "mismatches": bad, "guard_activations": guard,
                                "cycles_max": int(cycles[1:].max()), "cycles_median": float(np.median(cycles[1:])),
                                "wall_s": time.time() - t0}
        print(s, res["seeds"][str(s)])
    cyc = np.concatenate(cyc_all)
    res["cycles"] = {"max": int(cyc.max()), "p99": float(np.percentile(cyc, 99)), "median": float(np.median(cyc)),
                     "max_us_at_16MHz": float(1e6 * cyc.max() / F_CPU_HZ)}
    res["gate"] = {"metric": "mismatches + guard activations (on-target float)", "value": total_bad + total_guard,
                   "pass": total_bad + total_guard == 0}
    Path(a.out).write_text(json.dumps(res, indent=2))
    print("cycles", res["cycles"], "GATE", res["gate"])
    sys.exit(0 if res["gate"]["pass"] else 1)


if __name__ == "__main__":
    main()
