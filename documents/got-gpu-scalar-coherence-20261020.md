# GPU-written scalar constants

Input: `got-frame-history-evidence-20261020-054401.tar.gz`, source `b936b4fccd2265ce368371a6855418105a00ad04`.

## What the native run establishes

The GDS-base correction changed execution. In sampled menu frame 2, fragment `5116e459` / vertex `ae1cc7b7` now has eight draws with submitted vertices, 12,558 clipping output primitives and 234,359 fragment invocations. Its previous sampled draws were empty. The sampled main depth image now has 28,437 nonzero samples out of 259,200. These are improvements in intermediate execution, not a restored world or menu.

Two other mesh paths still have submitted vertices but zero clipping output and zero fragment invocations. Vertex `9d9a6bd6` reads its visibility limit from flat slot 62, and vertex `7472760d` reads it from slot 65. Their captured CPU values are zero. The originating guest words are respectively `0x150726fe00` and `0x150726fd00`. Both limits remain ReadConst operations in the final shader IR. The captured compute producer `ae3b2e07` has a store descriptor targeting `0x150726fd00`. A GPU-produced value at either address must not be replaced by the stale CPU snapshot. These identifiers describe evidence only; the implementation has no shader or game whitelist.

The late menu screenshot remains white. An earlier intermediate contains some geometry, while the later HDR sky pass contains very large finite values (up to 65,504). The images have different resolutions and guest buffers; they do not establish that a correct full scene exists underneath an overlay. Large HDR values alone do not prove an erroneous shader or justify clamping exposure.

## Change

The generated scalar-memory walker records the guest byte address alongside each flattened value, including immediate offsets, dynamic offsets and inline scalar-buffer reads. User-data register slots retain zero origins. The walker ABI and shader cache versions change together (binary 55, metadata 28).

When binding the flat buffer, GPU-modified, aligned, in-range source words are copied from the GPU buffer cache into their corresponding flat slots. Adjacent copies are coalesced. CPU-only values remain the normal CPU upload. The existing runtime orders producer writes before copies; the flat-buffer binding records the copy-to-shader dependency. The refresh does not wait for a CPU readback and uses no forced draw count, clipping bypass or texture fallback.

During selected frame-probe frames, bounded readbacks compare the actual flat buffer with the CPU snapshot. `FrameProbeScalarRefresh` reports submitted refreshes. `FrameProbeScalarValue` reports changed words with CPU and GPU values. `FrameProbeScalarReadback` reports completion, even when the values match. Captures are limited to the first refreshed binding of each shader hash per frame, at most 128 shaders and 256 KiB per shader. This diagnostic ends render passes like the existing copy path; it is not enabled in ordinary runs.

This covers scalar data written by an earlier dispatch/draw. It does not make CPU-resolved descriptor pointers, scalar-derived table bounds or resource chains GPU-resolvable, and does not implement scalar reads of writes made within the same dispatch. Native evidence must still establish whether the visibility limits differ and whether clipping output and visible world geometry return.

## Validation

The complete emulator compiles and links. Replaying the two captured vertex shaders through the production recompiler checks all 47 / 50 memory-derived flat slots against reconstructed captured memory. Their counter origins remain exactly `0x150726fe00` / `0x150726fd00`, with CPU values zero. Cloned cached walkers reproduce the same values and origins. Both emitted modules pass SPIR-V validation for Vulkan 1.3.

An external production-walker check covers immediate and dynamic offsets (including EDX scratch use), an inline scalar-buffer read with a uniform ReadFirstLane base, deduplicated reads, changing user data, and array-bound canaries. An external check executes the production refresh block with a simulated GPU buffer: values 17 then 41 arrive at the flat slot; CPU-only values stay unchanged; contiguous copies coalesce; invalid or unaligned addresses are excluded; and the shader-read hazard is recorded. These checks do not execute Vulkan on the user's GPU. The inline-buffer test does not establish direct-root scalar-buffer bounds correctness; that pre-existing path remains outside this change.

The runner's source preflight, shell syntax and 94-file overlay coverage pass. No new repository tests were added. OpenOrbis hardware source is in `homebrew/gpu-scalar-load`; it explicitly invalidates the scalar data cache before each reader. The application has not been linked or run on PS4 here.

The existing isolated runner retains the synchronization and shader corrections and restores the original source and executable. Stay in the menu for 150 seconds, then enter gameplay for the remainder of the 300-second capture. The white menu and absent world remain unresolved until a new native capture demonstrates corrected rendering.
