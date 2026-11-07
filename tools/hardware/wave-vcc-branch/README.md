# Whole-wave VCC branch probe

Original OpenOrbis homebrew, with a small hand-encoded GCN compute shader. No game assets are required.

`S_CBRANCH_VCCZ` and `S_CBRANCH_VCCNZ` test the **whole 64-bit VCC register**, including when only some EXEC bits are set. See AMD's [Southern Islands ISA, SOPP instructions](https://www.amd.com/content/dam/amd/en/documents/radeon-tech-docs/instruction-set-architectures/southern-islands-instruction-set-architecture.pdf).

The shader first records both branch outcomes for supplied VCC values, then walks two 32-bit masks using scalar instructions under partial EXEC. After restoring EXEC, all 64 lanes must report the same outcomes, the population count of the input mask, and a cleared low half. A probe-only 65-iteration guard reports incorrect termination without intentionally hanging the GPU. This guard is not part of the emulator or any game shader.

`shader.s` documents the original instructions; `shader.h` contains their GCN encoding and OrbShdr header. Build with `OO_PS4_TOOLCHAIN=/path/to/OpenOrbis make`. Run `eboot.bin` through a homebrew loader on a PS4, then through the emulator. Collect `/data/wave-vcc-branch-probe.log` from both. The program submits one 64-lane workgroup per case, with a two-second fence timeout and no further submissions after a timeout.

Expected: every case has `mismatches=0`. Hardware execution and SDK linking must be recorded separately from host syntax checks or compiler replay; neither establishes hardware results.
