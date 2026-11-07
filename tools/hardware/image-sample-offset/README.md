# Runtime image-sampling offset probe

Original OpenOrbis homebrew; no game shaders or captures are included. The original GCN compute shader loads a packed offset from a buffer, samples a 128 × 128 RGBA8 coordinate pattern with `IMAGE_SAMPLE_L_O` at explicit LOD zero, and writes the four floating-point components to a separate part of the buffer. Seven cases cover zero, positive, negative and six-bit endpoint offsets. The expected sample is the texel at `(64 + offset_x, 64 + offset_y)` with nearest filtering and normalized coordinates.

Build with `make OO_PS4_TOOLCHAIN=/path/to/OpenOrbis`, run `eboot.bin` on a homebrew-capable PS4 and under the emulator, and compare `/data/image-sample-offset-probe.log`. Each case waits for a cache-flush fence; a two-second timeout stops submissions. This does not run either commercial game.

The shader begins with the binary-info pointer. Its instruction sequence is: buffer load of the packed offset into v0; VM wait; normalized coordinates 0.5 in v1/v2 and LOD zero in v3; image sample with offset to v4:v7; VM wait; buffer store of the four components at byte 16; VM wait; end. T# is s0:s7, S# s8:s11 and output V# s12:s15. These are assembled instruction words for this probe, not a dump from a title.

Host C11 syntax and translation through the actual emulator compiler are checked. SDK linking and physical PS4 execution remain unverified. The host shader check demonstrates that the old emitter omits the offset and the feature-enabled emitter emits it; SPIR-V validation also checks that this operand requires the enabled maintenance8 feature. This probe does not establish menu recovery or resolve the GoW cutscene stall.
