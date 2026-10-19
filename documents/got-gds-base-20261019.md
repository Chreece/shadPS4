# GDS base correction from the 22:40 frame capture

The uploaded `got-frame-history-evidence-20261019-224004.tar.gz` contains complete pipeline statistics for all 3,371 submitted draws across six sampled frames. The first four samples are menu frames; the last two are gameplay frames.

The menu failure is already visible before final compositing. In frame 2, nine draws pairing FS 0x836253ca with VS 0x9d9a6bd6 submit 6,663,298,920 input vertices, with zero primitives surviving clipping and zero fragment invocations. The preceding odd frame's GPU argument copy records 362,324 instances for an index count of 2,040. Other mesh pipelines have zero indirect counts. Sampled main depth rows in both menu image frames remain zero. Gameplay contains some genuine geometry/depth writes but does not establish correct world rendering. This evidence does not support treating the final overlay as the sole cause.

## Confirmed translation defect

CS 0x11ac5627 adds to a GDS counter; CS 0xae3b2e07 reads it and writes an indirect argument. Both captured user-data snapshots set M0 to 0x07000008, selecting byte base 0x700. Before this change, ordinary GDS DS operations ignored M0 and used byte offset zero. The final native IR confirms `BufferAtomicIAdd32 #6, #0` and `LoadBufferU32 #2, #0`. Multiple GDS regions could therefore collide while the intended region stayed untouched. Earlier probes sampled only 0x980–0x9ff; their zero values did not rule this out.

AMD's GCN instruction reference, section 3.7, defines GDS M0 as `{base[15:0], size[15:0]}`, both in bytes:
https://gpuopen.com/download/AMD_GCN3_Instruction_Set_Architecture_rev1.1.pdf

The translator now adds M0's high-half base to ordinary GDS atomic, compare-exchange, read, and write addresses. Paired operations retain their individual instruction offsets. Fragment GDS operations bypass the LDS interpolation shortcut. LDS address behavior and the existing append/consume path are unchanged. Shader binary cache version advances from 53 to 54 so previously compiled shaders are not reused. Segment-size clamping is outside this focused base-address correction.

## Validation and limits

- Full Release shadps4 build and link: passed.
- Both captured compute shaders replayed through the production compiler: emitted address expressions evaluate to byte 0x700 using captured M0, versus byte zero in the original capture.
- Two captured modules, a GDS probe, and an LDS control pass Vulkan 1.3 SPIR-V validation.
- The OpenOrbis probe exercises two bases, writes, paired reads/writes, atomic add, compare-exchange, and independent absolute-address reads. An external interpreter of the emitted straight-line IR produces `17 29 17 20 23 41 23 41` after the correction. The old compiler produces `41 29 41 44 44 44 0 0` and fails this comparison. This is a compiler/IR check, not GPU execution.
- The equivalent LDS control emits byte-identical SPIR-V with the old and corrected translator.
- OpenOrbis source is in `documents/homebrew/gds-base`; assembly assembled for Bonaire. The application has not been linked or run on PS4.

This fixes an observed emulator translation defect. No corrected native screenshot exists yet, so neither the white menu nor the world is claimed fixed. The next native capture must check whether oversized/empty indirect counts normalize, geometry survives, and actual visible rendering improves.

## Native run

Use the paired pinned build-and-run script on the diagnostic branch. The overlay now includes `frontend/translate/data_share.cpp` (93 source files total), and the preflight expects cache version 54. Preserve the same capture sequence: stay at the menu for 150 seconds, then enter gameplay for the remainder of the five-minute capture. Upload the resulting `got-frame-history-evidence-*.tar.gz`. Production remains unchanged; the runner restores the original executable, source files, and profile.
