# Image transfer regression checks

The 2026-10-01 17:21 RDR CUSA36843 report from main `10ff9e19` confirms
`GPU readbacksMode: 2` and an active Khronos core/synchronization validation layer.
It contains an invalid D32_SFLOAT → D32_SFLOAT_S8_UINT image copy (01548), depth
attachment load hazards after transitions whose destination access is shader-read
only, and depth store hazards when another transition uses that shader-read state.

`Runtime::Transit` previously accumulated consecutive transitions of one image in
one `vkCmdPipelineBarrier2`. The later transition depends on the earlier transition
but barriers inside one command are not ordered that way. Emit the pending batch
before recording another transition of that image. Batching independent images
is retained. This also handles partial subresources conservatively.

The single-sample D32/D32S8 copy path now transfers only their common 32-bit float
depth aspect through a device-local staging allocation with a transfer write/read
barrier. It preserves region offsets, mip levels and layers. Stencil is untouched.
Same-format copies and existing colour/depth maintenance8 handling are retained.
Multisample depth-format conversion is outside this change.

Sources: Vulkan specification [image copy compatibility](https://docs.vulkan.org/spec/latest/chapters/copies.html#VUID-vkCmdCopyImage-srcImage-01548)
and [synchronization](https://docs.vulkan.org/spec/latest/chapters/synchronization.html).
Upstream PRs 5002, 4773 and 4714 were reviewed; they address other extent, aliasing
and attachment-selection problems and are not imported as part of this fix.

Build this directory standalone with CMake and run CTest. It needs Vulkan headers,
not a Vulkan device. `VULKAN_HEADERS_PATH` can point at an existing header checkout.
The tests cover copy format selection, multi-layer/mip region preservation, 64-bit
allocation sizing, and the same-image versus independent-image batching decision.
They do not prove Vulkan command execution or correct game rendering.

Local acceptance: compare the same street and cutscene with validation disabled.
Check character/building surfaces, unusual colours, indoor light and cutscene audio.
A short optional validation run should then check whether 01548 and the reported
depth READ_AFTER_WRITE/WRITE_AFTER_WRITE errors are gone. The report also contains
swapchain present hazards, an arena four bytes above maxBufferSize, and a mapped
memory flush alignment error; those are separate issues and remain unresolved here.

## Depth overlap growth initialization

The detailed `ngs2-diagnostic-286d0cca(1)` capture records two R32_SFLOAT images
with five layers being recreated as D32_SFLOAT images with six layers. The newer
`f9110284` audio-review log also has a five-to-six layer warning, but lacks the
format metadata needed to identify its path on its own.

`ResolveDepthOverlap` created a fresh image, cleared its dirty flags, disabled
the first-use HTile clear, and copied the overlapping layers. Unlike `ExpandImage`,
it never initialized the additional layers or mip levels. Copying the minimum
layer count is correct; marking the uncopied part initialized is the defect.

For single-sample replacements that add layers or mip levels, refresh the new
image from guest/buffer-cache data before copying the old GPU image over the
overlapping part. Existing GPU-rendered data therefore wins over stale guest data,
while newly added subresources receive their guest contents. Same-size format
conversions retain their existing path and avoid an extra upload. Multisample
initialization and stencil contents are outside this fix.

`image_transfer_depth_growth` compiles the production `ResolveDepthOverlap` body
from `texture_cache.cpp` in a CPU fixture. Allocation, upload, and copy operations
are test doubles with distinct data for each layer/mip and for GPU versus guest
contents. CMake regenerates the fixture when the production file changes; the
decision and ordering code is not duplicated in the test. The original code fails
the logged five-to-six case with the sixth layer still holding the uninitialized
sentinel. The corrected code passes layer growth, mip growth, combined growth,
same-size conversion, and unchanged-cache cases. These checks also detect an
upload incorrectly placed after the preservation copy.

This establishes the missing initialization, not its visual impact in RDR. Test
the same street, character details, new locations, and cutscene locally with the
normal settings. The copy-layer warning can remain because the preservation copy
still correctly uses the smaller layer count.

## Runtime evidence

With `SHADPS4_GRAPHICS_DIAGNOSTICS=1`, the production overlap path emits a bounded
`depth-growth` record after refreshing the replacement and returning from the
preservation copy. `upload-recorded=1` means the dirty state was cleared by the
refresh before the copy. `depth-growth-uninitialized` identifies growth for which
this did not happen, including unsupported multisample initialization. These are
CPU-side command-recording observations, not a GPU readback or proof of visual
correctness. Existing layer-count warnings can still occur.

`scripts/collect_graphics_evidence.py --expected-revision <40-character SHA>` arms
one ordinary ES-DE launch. It verifies the selected installer record and binary
hash, preserves the single-instance guard, enables diagnostics only for the game
process, and captures stdout/stderr from launch until exit. It also verifies the
running executable through `/proc`, records the exit code and relevant settings,
includes fresh renderer logs, and restores the original launcher. It does not
enable Vulkan validation or change emulator settings or host services.

Run the collector before launching the game. Wait for `CAPTURE_ARMED`, launch
through the ordinary ES-DE game entry, reproduce the affected scenes, and exit
the game normally. Only then is `GRAPHICS_REPORT` created. Upload that archive
with observations about the same session. `CAPTURE_COMPLETE=True` describes
capture coverage, not a fixed bug. `NOT_OBSERVED` requires another relevant test;
it must never be reported as success. Missing binary identity, missing diagnostics,
interruption or a missing exit record keeps the capture incomplete. The controller
does not stop the game on timeout or interruption, and prints a guarded launcher
recovery command when arming.

Run `python3 -m unittest discover -s scripts -p test_collect_graphics_evidence.py`.
The collector tests launch a real child through exit and archive creation; the
installer, launcher, and `/proc` identity are fixtures. Both verified and denied
identity cases are tested. These checks do not replace evidence from the user's
emulator session.
