# OpenOrbis overlapping buffer probe

This homebrew source exercises reads and writes through two V# descriptors whose bases differ by 64 bytes. It writes through A at index 16, reads through B at index 0, overwrites through B, then reads through A. All four dispatches use one command buffer, globally coherent buffer accesses and CS partial flushes. The host checks both values and surrounding sentinel bytes for 64 submissions.

Build with `OO_PS4_TOOLCHAIN` set to an installed [OpenOrbis toolchain](https://github.com/OpenOrbis/OpenOrbis-PS4-Toolchain), then run `make` and package the resulting `eboot.bin` with a standard homebrew package template. Read `/data/aliased-buffer-probe.txt`; expected final status is `RESULT=PASS`.

The GCN assembly was assembled for Bonaire with LLVM 19. The OpenOrbis application has not been linked or run on PS4 here. The small allocation checks guest overlap semantics; it does **not** force the emulator to create separate sparse Vulkan arenas. The separate host regression covers that implementation condition. Neither is a gameplay result.
