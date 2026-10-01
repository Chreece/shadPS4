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
