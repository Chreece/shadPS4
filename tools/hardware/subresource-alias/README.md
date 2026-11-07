# Subarray image alias probe

Original OpenOrbis homebrew using the original triangle shaders from the viewport probe. No game shaders or captured guest data are included.

Four cases allocate separate eight-layer, 128 × 128 linear RGBA8 surfaces. Each first draws to layer 7 to establish the full backing. The control cases draw to layers 3 and 4 through that backing. Their paired cases instead move the color-buffer address forward by three layer strides, describe a two-layer subarray, and select relative layer 0 or 1. The paired raw images should match and both controls must be nonempty. This checks that a subarray's byte offset is divided by one layer's stride, and that the view's relative layer is added to the backing offset.

Run `make OO_PS4_TOOLCHAIN=/path/to/OpenOrbis` with the SDK, Clang and LLD installed. Run the resulting `eboot.bin` on a homebrew-capable PS4 and under the emulator. Compare `/data/subresource-alias-probe.log` and `/data/subresource-alias-{0,1,2,3}.rgba`; each raw file contains eight consecutive 128 × 128 RGBA8 layers. The command buffers wait for a cache-flush fence; a two-second timeout stops further submissions.

Strict host C11 syntax with `-Wall -Wextra -Werror` passes. SDK linking and physical-console execution have not been performed, and no hardware result is claimed. This probe does not independently exercise mip-chain overlap ordering or the simultaneous texture/target merge; those corrections have host reproductions using extracted production functions. It does not establish that the games' missing graphics, FPS or cutscene failure are repaired.
