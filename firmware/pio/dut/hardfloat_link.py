# PlatformIO pre-script: link with the same float ABI as the compile step, so the
# hard-float multilib (libm, libgcc) is selected for the Cortex-M4F FPU.
Import("env")  # noqa: F821  (provided by PlatformIO)
env.Append(LINKFLAGS=["-mfloat-abi=hard", "-mfpu=fpv4-sp-d16"])  # noqa: F821
env.Append(ASFLAGS=["-mfloat-abi=hard", "-mfpu=fpv4-sp-d16"])  # noqa: F821
