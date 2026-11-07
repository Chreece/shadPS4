# Indexed image explicit LOD probe

Original OpenOrbis GCN microprogram for a GPU-read material ID selecting real T#/S# pairs from 64-byte records. Two 128x128 RGBA8 textures have different blue channels and coordinate ramps. The shader uses `IMAGE_SAMPLE_LZ`, `IMAGE_SAMPLE_L` with LOD 0, and `IMAGE_SAMPLE_LZ_O` with offset (+1,-1). Each result is compared with the expected texel for both record IDs. No commercial shader or assets are included.

Set `OO_PS4_TOOLCHAIN` to an OpenOrbis SDK and run `make`. Run `eboot.bin` on hardware and the emulator and compare `/data/indexed-image-lod-probe.log`. The command buffer uses one invocation and a two-second fence deadline; a fence timeout stops further submissions.

The descriptor table bounds are 128 bytes, with texture descriptors at record offset 0 and sampler descriptors at offset 32. The ID is loaded through a vector buffer read then `v_readfirstlane_b32`, preventing host-side constant selection. All sampler fields are zero (point filtering, wrap, normalized coordinates).

Validation performed here: C11 syntax, production GCN decode, production compiler replay, and SPIR-V validation. SDK linking and physical PS4 execution have not been performed. Nonzero/runtime LOD and mixed dimension selection additionally require native coverage; the scratch compiler checks preserve the runtime LOD operand and validate the generated typed branches.
