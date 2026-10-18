"""SEU fault-injection campaign on the DUT (item 5; plan M6 gate: zero silent data
corruption in 3000 injections after mitigation).

Setup: the DUT (firmware/pio/dut, tree tables in RAM with CRC scrub, IWDG, HardFault
reset) replays a fixed 7-spacecraft measurement stream through its full chain
(EKF -> history -> tree -> SGO v3). Between two frames, OpenOCD flips ONE random bit
in RAM over ST-Link while the core runs. The run is compared with a golden run of
the same stream.

Regions (bits drawn uniformly inside a region; injections stratified by region):
  tree_tables   feat/thr/left/right/leaf RAM copies        scrub on (mitigated)
  tree_noscrub  same tables, scrub switched off            ablation, not counted in the gate
  ekf_state     the 7 EKF instances (x, P)
  features      the 7 history buffers
  globals       the rest of .data/.bss
  stack         the top 1 KB below _estack
Outcome per injection (injected slot, or all slots for shared regions), over K frames:
  detected  reset (boot frame / no reply), scrub repair (bit 7), tree fault (bit 6),
            EKF guard (bits 0-3) or SGO flag that the golden run does not show
  sdc       an action differs from golden before any detection
  latent    actions identical, SoC estimate differs at the end (state corrupted, not yet visible)
  masked    identical outputs
Writes results/fault_injection.json.
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
for p in ("sim", "verify"):
    sys.path.insert(0, str(ROOT / p))
from hil_ekf import Dut  # noqa: E402

ELF = ROOT / "firmware" / "pio" / "dut" / ".pio" / "build" / "dut" / "firmware.elf"
NM = Path("C:/arm-gnu-toolchain/bin/arm-none-eabi-nm.exe")
OCD = Path.home() / ".platformio" / "packages" / "tool-openocd"
N_SLOTS, W, K = 7, 10, 40
FAULTS = [0, 5, 10, 1, 7, 12, 4]                    # workload: one stream per slot
EKF_SIZE, FEAT_SIZE = 48, 328
GS_SIZE, GS_FEAT, GS_GUARD = 380, 48, 376     # FdirGuardedState: ekf @0, feat @48, latch + crc @376 (ISS-057)


def symbols() -> dict:
    out = subprocess.run([str(NM), "-S", str(ELF)], capture_output=True, text=True).stdout
    sym = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) == 4:
            sym[p[3]] = (int(p[0], 16), int(p[1], 16))
        elif len(p) == 3:
            sym[p[2]] = (int(p[0], 16), 0)
    return sym


class Ocd:
    """Persistent OpenOCD with the TCL RPC port (commands end with 0x1a)."""

    def __init__(self):
        args = [str(OCD / "bin" / "openocd.exe"), "-s", str(OCD / "share" / "openocd" / "scripts"),
                "-f", "interface/stlink.cfg", "-f", "target/stm32f4x.cfg",
                "-c", "tcl_port 6666", "-c", "telnet_port disabled", "-c", "gdb_port disabled"]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                self.s = socket.create_connection(("127.0.0.1", 6666), timeout=5)
                break
            except OSError:
                time.sleep(0.2)
        self.cmd("init")
        self.retries = 0

    def cmd(self, c: str) -> str:
        self.s.sendall(c.encode() + b"\x1a")
        buf = b""
        while not buf.endswith(b"\x1a"):
            buf += self.s.recv(4096)
        return buf[:-1].decode(errors="replace")

    def flip(self, addr: int, bit: int) -> None:
        a = addr & ~3
        b = bit + 8 * (addr & 3)
        for attempt in range(5):                     # transient SWD errors (18 Oct: 'read_memory: failed')
            r = self.cmd(f"return [read_memory 0x{a:08x} 32 1]")
            try:
                v = int(r.strip().split()[0], 0) ^ (1 << b)
                break
            except (ValueError, IndexError):
                self.retries += 1
                time.sleep(0.3 * (attempt + 1))
        else:
            raise IOError(f"SWD read failed 5 times at 0x{a:08x}: {r!r}")
        self.cmd(f"write_memory 0x{a:08x} 32 {{0x{v:08x}}}")

    def close(self):
        try:
            self.cmd("shutdown")
        except Exception:
            pass
        self.proc.kill()


def workload() -> np.ndarray:
    """(W+K, 7, 8) 'X' payloads from the 4.4 plant (expert actions, faults at 15 s)."""
    from env_v2 import EPSEnv, EnvConfig
    env = EPSEnv(EnvConfig(n_env=N_SLOTS, seed=77, device="cpu", fault_prob=1.0))
    env.fault[:] = torch.tensor(FAULTS)
    env.t_onset[:] = 15.0
    q = env.tab.q_ah
    rows, i_prev = [], torch.zeros(N_SLOTS)
    for _ in range(W + K):
        obs, _, _ = env.step(env.expert_action())
        rows.append(torch.stack([i_prev * q, obs[:, 1] * q, obs[:, 0], torch.ones(N_SLOTS), obs[:, 1], obs[:, 2], obs[:, 4], obs[:, 5]], 1))
        i_prev = obs[:, 1]
    return torch.stack(rows).numpy().astype(np.float32)


def run(dut: Dut, wl: np.ndarray, inject=None) -> dict:
    """One pass of the workload; inject(frame) is called once before frame W."""
    dut.call(b"J", bytes([N_SLOTS]) + struct.pack("<7f", *([0.6] * N_SLOTS)))
    out = np.zeros((len(wl), N_SLOTS, 7), np.int64)        # soc bits, std bits, S, B, L, flags, status
    reset = False
    for k, frame in enumerate(wl):
        if k == W and inject is not None:
            inject()
        try:
            c, d = dut.call(b"Y", bytes([N_SLOTS]) + frame.tobytes())
        except (TimeoutError, IOError):
            reset = True
            break
        if c == b"b":                                       # the DUT rebooted
            reset = True
            break
        for j in range(N_SLOTS):
            r = d[17 * j:17 * j + 17]
            out[k, j] = [*struct.unpack("<2I", r[:8]), r[8], r[9], r[10], r[11], r[12]]
    return {"out": out, "reset": reset, "frames": k}


def classify(g: np.ndarray, f: dict, slots) -> str:
    if f["reset"]:
        return "detected"
    o = f["out"]
    det_step, act_step = None, None
    for k in range(W, len(o)):
        for j in slots:
            new_status = (o[k, j, 6] & ~g[k, j, 6]) != 0
            new_flag = (o[k, j, 5] & ~g[k, j, 5]) != 0
            if (new_status or new_flag) and det_step is None:
                det_step = k
            if (o[k, j, 2:5] != g[k, j, 2:5]).any() and act_step is None:
                act_step = k
    if act_step is not None and (det_step is None or act_step < det_step):
        return "sdc"
    if det_step is not None:
        return "detected"
    if (o[-1, list(slots), :2] != g[-1, list(slots), :2]).any():
        return "latent"
    return "masked"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--plan", nargs="+", default=["tree_tables=1000", "ekf_state=700", "features=700",
                                                  "globals=300", "stack=300", "tree_noscrub=400"])
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--out", default=str(ROOT / "results" / "fault_injection.json"))
    a = ap.parse_args()
    sym = symbols()
    tables = [sym[n] for n in ("feat_ram", "leaf_ram", "left_ram", "right_ram", "thr_ram")]
    big = {"gstate", "feat_ram", "leaf_ram", "left_ram", "right_ram", "thr_ram"}
    gs0, gsz = sym["gstate"]
    assert gsz == N_SLOTS * GS_SIZE, f"gstate size {gsz} != {N_SLOTS} x {GS_SIZE}"
    per = lambda lo, hi: np.concatenate([np.arange(gs0 + k * GS_SIZE + lo, gs0 + k * GS_SIZE + hi) for k in range(N_SLOTS)])
    sdata, ebss = sym["_sdata"][0], sym["_ebss"][0]
    # globals: .data/.bss bytes not covered by the big objects
    covered = np.zeros(ebss - sdata, bool)
    for n in big:
        s0, sz = sym[n]
        covered[s0 - sdata:s0 - sdata + sz] = True
    glob = np.concatenate([sdata + np.flatnonzero(~covered), per(GS_GUARD, GS_SIZE)])   # + latch / CRC / padding
    regions = {
        "tree_tables": np.concatenate([np.arange(s, s + z) for s, z in tables]),
        "ekf_state": per(0, GS_FEAT),
        "features": per(GS_FEAT, GS_GUARD),
        "globals": glob,
        "stack": np.arange(sym["_estack"][0] - 1024, sym["_estack"][0]),
    }
    regions["tree_noscrub"] = regions["tree_tables"]
    rng = np.random.default_rng(a.seed)
    wl = workload()
    dut = Dut(a.port)
    _, ver = dut.call(b"P")
    dut.call(b"Z", b"\x01")
    golden = run(dut, wl)
    check = run(dut, wl)
    assert not golden["reset"] and np.array_equal(golden["out"], check["out"]), "golden run is not deterministic"
    g = golden["out"]
    ocd = Ocd()
    res = {"firmware": ver.decode(), "frames_after_injection": K, "warmup_frames": W, "workload_faults": FAULTS,
           "region_bytes": {k: int(len(v)) for k, v in regions.items()}, "regions": {}, "log": []}
    t0 = time.time()
    try:
        for item in a.plan:
            name, n = item.split("=")
            dut.call(b"Z", b"\x00" if name == "tree_noscrub" else b"\x01")
            counts = {"masked": 0, "latent": 0, "detected": 0, "sdc": 0}
            for i in range(int(n)):
                addr = int(rng.choice(regions[name]))
                bit = int(rng.integers(0, 8))
                if name in ("ekf_state", "features"):
                    slots = [(addr - gs0) // GS_SIZE]
                else:
                    slots = list(range(N_SLOTS))
                f = run(dut, wl, inject=lambda: ocd.flip(addr, bit))
                o = classify(g, f, slots)
                counts[o] += 1
                res["log"].append([name, hex(addr), bit, o])
                if f["reset"]:                              # rebooted: scrub is back on by default
                    time.sleep(0.05)
                    dut.s.reset_input_buffer()
                    dut.call(b"Z", b"\x00" if name == "tree_noscrub" else b"\x01")
                if (i + 1) % 100 == 0:
                    print(name, i + 1, counts, f"{time.time() - t0:.0f} s", flush=True)
            res["regions"][name] = counts
            Path(a.out).write_text(json.dumps(res, indent=1))
    finally:
        ocd.close()
        dut.call(b"Z", b"\x01")
    gated = {k: v for k, v in res["regions"].items() if k != "tree_noscrub"}
    n_tot = sum(sum(c.values()) for c in gated.values())
    n_sdc = sum(c["sdc"] for c in gated.values())
    upper = 3.0 / n_tot if n_sdc == 0 else None                 # rule of three, 95 %
    size = {k: res["region_bytes"][k] for k in gated}
    w = {k: size[k] / sum(size.values()) for k in gated}
    weighted = sum(w[k] * gated[k]["sdc"] / max(sum(gated[k].values()), 1) for k in gated)
    res["swd_read_retries"] = ocd.retries
    res["summary"] = {"injections_gated": n_tot, "sdc_gated": n_sdc, "sdc_rate_upper95_if_zero": upper,
                      "sdc_rate_size_weighted": weighted, "wall_s": time.time() - t0}
    res["gate"] = {"metric": "zero SDC in >= 3000 injections with mitigation", "pass": n_tot >= 3000 and n_sdc == 0}
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(res["regions"], res["summary"], res["gate"])


if __name__ == "__main__":
    main()
