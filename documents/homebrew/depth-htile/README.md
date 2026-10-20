# Depth HTile readback probe

OpenOrbis homebrew for the original PS4 eight-pipe layout. It draws a full-screen
triangle into a 512x512 D32 depth target with depth-only HTile enabled, then copies
all 4096 metadata words with an ordinary `buffer_load_dword` compute shader.
Four draws change the depth to 0.25, 0.75, 1 and 0. Output bounds must follow each
draw; stale initial metadata and zero-filled metadata fail. Output guards are
checked. Compression bits may differ between hardware and the emulator.

Set `OO_PS4_TOOLCHAIN` to an OpenOrbis SDK and run `make`. `make shader` regenerates
the checked-in shaders using LLVM's AMDGPU assembler. The result is written to
`/data/depth-htile-probe.txt`. A submission failure deliberately retains the GPU
allocations until the process is stopped.

The shaders have been assembled and the C source checked locally. This probe has
not yet been run on PS4 hardware. Do not treat its expected output as a recorded
hardware result. It does not test stencil HTile encoding or PS4 Pro pipe layouts.

The emulator's GPU summary shader is separately checked against AMD's address
library, including linear/tiled addresses, layers and partial buffers. The
format follows `ac_htile_dword.z` in Mesa's AMD common code: four compression
bits, fourteen minimum-depth bits, fourteen maximum-depth bits.
