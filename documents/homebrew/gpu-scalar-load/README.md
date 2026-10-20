# GPU-produced scalar load probe

OpenOrbis homebrew source for a scalar memory read whose value was produced by a prior GPU dispatch. A writer stores a seed through a buffer descriptor. A second dispatch uses S_LOAD_DWORD through a pointer to that same word and writes the result to an output buffer. The writer then changes the seed and another reader dispatch must observe the new value. There is no CPU readback between dispatches. CS partial flushes order the dispatches and each reader invalidates its scalar data cache; the host waits for the final completion event and checks results and adjacent sentinels for 64 submissions.

Build with `OO_PS4_TOOLCHAIN` set to OpenOrbis, run `make`, and package `eboot.bin` using the usual homebrew template. `/data/gpu-scalar-load-probe.txt` must end in `RESULT=PASS`; each case must return its seed and seed+1. The reader's scalar destination register is not a resource descriptor.

Assembly targets Bonaire. The application has not been linked or run on PS4 in this environment. This covers preceding-dispatch coherence, not self-modifying scalar data within a dispatch or GPU-written descriptor chains.
