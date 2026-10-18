# PlatformIO pre-script: compile the flight sources in firmware/src inside THIS env's build
# directory. With build_src_filter = ../../../src/..., PlatformIO put their objects in a
# shared .pio/src/, so the ref and bench builds overwrote each other (ISS-058).
import os

Import("env")  # noqa: F821  (provided by PlatformIO)
SRC = os.path.normpath(os.path.join(env.subst("$PROJECT_DIR"), "..", "..", "src"))  # noqa: F821
FILES = ["soc_ekf.c", "fdir_tree.c", "fdir_onboard.c", "fdir_sgo_v3.c", "fdir_features.c", "fdir_state_guard.c"]
env.BuildSources(os.path.join("$BUILD_DIR", "flight_src"), SRC, src_filter=["-<*>"] + [f"+<{f}>" for f in FILES])  # noqa: F821
