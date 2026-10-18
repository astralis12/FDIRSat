"""Timing and size sweep of the DUT firmware over CPU clock and flash wait states (item 5).

For each PlatformIO environment of firmware/pio/dut (16 MHz HSI, then 32-100 MHz from
the PLL with flash wait states per RM0383 Table 6, ART accelerator on, plus 16 and
100 MHz with the ART accelerator off): build, flash over ST-Link, then measure on the
STM32F411 with DWT->CYCCNT
  - one full 1 Hz step per spacecraft (EKF + history + tree + SGO v3), 7 x 300 steps
  - one CRC scrub of the RAM tree tables (x 20)
and record flash and RAM use from the ELF. Times = cycles / f_CPU.
Power is not estimated here: it is measured on the bench (INA226, RUNBOOK 5).
Ends by re-flashing the default 16 MHz build. Writes results/timing_sweep.json.
"""
from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "verify"))
from fault_injection import workload  # noqa: E402
from hil_ekf import Dut  # noqa: E402

PIO = Path.home() / ".platformio" / "penv" / "Scripts" / "pio.exe"
SIZE = Path("C:/arm-gnu-toolchain/bin/arm-none-eabi-size.exe")
PROJ = ROOT / "firmware" / "pio" / "dut"
ENVS = [("dut", 16, 0, True), ("f32", 32, 1, True), ("f48", 48, 1, True), ("f64", 64, 1, True),
        ("f84", 84, 2, True), ("f100", 100, 3, True), ("f100_noart", 100, 3, False), ("f16_noart", 16, 0, False)]


def flash(env: str) -> dict:
    r = subprocess.run([str(PIO), "run", "-e", env, "-t", "upload"], cwd=PROJ, capture_output=True, text=True)
    if "Verified OK" not in r.stdout + r.stderr:
        raise SystemExit(f"flash failed for {env}:\n{r.stdout[-1500:]}")
    elf = PROJ / ".pio" / "build" / env / "firmware.elf"
    s = subprocess.run([str(SIZE), str(elf)], capture_output=True, text=True).stdout.splitlines()[1].split()
    text, data, bss = map(int, s[:3])
    return {"flash_bytes": text + data, "ram_bytes": data + bss}


def measure(frames: int = 300) -> dict:
    time.sleep(0.5)
    dut = Dut("COM5")
    _, ver = dut.call(b"P")
    wl = workload()
    dut.call(b"J", bytes([7]) + struct.pack("<7f", *([0.6] * 7)))
    cyc = []
    for k in range(frames):
        _, d = dut.call(b"Y", bytes([7]) + wl[k % len(wl)].tobytes())
        cyc += [struct.unpack("<I", d[17 * j + 13:17 * j + 17])[0] for j in range(7)]
    scrub = [struct.unpack("<I", dut.call(b"C")[1][:4])[0] for _ in range(20)]
    dut.s.close()
    return {"firmware": ver.decode(), "step_cycles_max": int(max(cyc)), "step_cycles_median": float(np.median(cyc)),
            "scrub_cycles_max": int(max(scrub))}


def main() -> None:
    res = {"source_wait_states": "RM0383 Table 6, VDD 2.7-3.6 V", "power": "not estimated; bench INA226 (RUNBOOK 5)", "configs": {}}
    try:
        for env, mhz, ws, art in ENVS:
            sz = flash(env)
            m = measure()
            row = {"f_cpu_mhz": mhz, "flash_wait_states": ws, "art_accelerator": art, **sz, **m,
                   "step_us_max": m["step_cycles_max"] / mhz, "scrub_us_max": m["scrub_cycles_max"] / mhz,
                   "duty_at_1hz_pct_7_spacecraft": 100 * 7 * (m["step_cycles_max"] + m["scrub_cycles_max"] / 7) / (mhz * 1e6)}
            res["configs"][env] = row
            print(env, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in row.items() if k != "firmware"}, flush=True)
            (ROOT / "results" / "timing_sweep.json").write_text(json.dumps(res, indent=2))
    finally:
        flash("dut")                                    # leave the default build on the board
    (ROOT / "results" / "timing_sweep.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
