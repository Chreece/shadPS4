# Explicit coarse texture gradients

This OpenOrbis hardware probe compares `IMAGE_SAMPLE_D`, `IMAGE_SAMPLE_CD` and
`IMAGE_SAMPLE_LZ` using the same 8×8 RGBA32F texture and nearest sampler. Each of
64 lanes samples the center of its own texel. The explicit gradients occupy four
VGPRs before the two coordinate VGPRs; their values deliberately differ from the
coordinates. Half the texture has alpha zero and half has alpha one.

Expected result: all three instructions return the known texel color in every
lane, and the output guard remains unchanged. Results are written to
`/data/coarse-texture-gradients-probe.txt`; success ends with
`RESULT=PASS failures=0`.

With OpenOrbis installed, set `OO_PS4_TOOLCHAIN` and run `make`. Load the resulting
`eboot.bin` using your usual homebrew loader. `make shader` regenerates `shader.h`
using LLVM 19's Bonaire assembler. The generated header is included so the
OpenOrbis build does not require the AMDGPU assembler.

The single-mip, nearest-sampled texture isolates gradient/coordinate operand
layout. It does not distinguish per-pixel versus per-quad mip selection or test
anisotropic filtering. The instruction's operand layout is documented in AMD's
[Sea Islands ISA](https://docs.amd.com/api/khub/documents/YO8RUh~LIQ5Cx3M_Kq9qkg/content),
IMAGE_SAMPLE_CD and image address-register ordering.

Validation on the development host: shader assembly succeeded. This probe has
not been linked with the OpenOrbis SDK or executed on PS4 hardware. Separately,
two affected captured fragment shaders were recompiled through the emulator;
the corrected gradient instructions produce Vulkan-valid SPIR-V with the
coordinate operands after the derivative operands. The emulator linked
successfully. Visual rendering still requires a GPU run.
