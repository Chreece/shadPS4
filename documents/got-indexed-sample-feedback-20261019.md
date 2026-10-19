# GoT: executed indexed-image sample feedback, 2026-10-19

Gameplay graphics remain broken. This branch retains the verified LDS/Phi and Vulkan synchronization work and adds an opt-in diagnostic; it does not claim a white-menu or scene-geometry fix.

## Evidence that changes the next step

The inspected `got-shader-correctness-runtime-20261019-143642.tar.gz` and host archive `got-shader-correctness-host-20261019-143615.tar.gz` confirm the successful build, zero reported GPU faults, 19 zero GDS snapshots, and eight high-volume indirect draws without geometry. The late screenshot is still approximately 93% white.

However, the eight unresolved CPU-side descriptor words for shader `0x7eaf40ad` do **not** establish that its GPU resource path is missing:

- Final IR contains four `ImageSampleImplicitLodIndexed` operations.
- The runtime binds the complete 27,651-entry guest image table, with 27,612 valid entries and a separate null sentinel at index 27,651.
- Saved SPIR-V preserves the GPU index, checks it against the table length, selects the null sentinel for out-of-range values, and decorates descriptor access and sampled images as nonuniform.
- The first lookup uses `fs_samp2`; the other three use `fs_samp3`. Their sampler descriptors originate from uniform user-data registers (SGPR6–9 and SGPR2–5 respectively). These four operations do not establish a missing dynamic sampler index.
- The saved module passes `spirv-val --target-env vulkan1.3`.

`InvalidTSharpSource` was logged before the successful indexed fallback. This branch emits that failure report only when the indexed fallback did not resolve the resource. Low-level CPU sharp-fetch diagnostics can still describe the unsupported host expression; final IR and binding evidence determine whether GPU resolution succeeded.

## Diagnostic behavior

Set `SHADPS4_INDEXED_IMAGE_FEEDBACK_HASHES` to a valid shader hash list to instrument indexed implicit-LOD and gradient sampling in matching fragment/compute shaders. With the variable absent, no feedback resource or shader instructions are added.

For binding occurrences 1, 32, and 256, each matching shader gets a fresh, zeroed diagnostic storage buffer. The shader records executed sample count, out-of-range selections, nonzero RGBA sample count, nonfinite sample count, and a bitmap of observed IDs. IDs 0–32767 are represented individually; larger values share an overflow bit. Logs report the first 48 distinct IDs, the total distinct count, and overflow separately. Counters aggregate sampling operations that share an image resource; they do not distinguish the four individual sample sites.

Other draws bind a disabled four-byte buffer and skip all diagnostic atomics. Captures use the existing tracked shader-write-to-transfer-read synchronization, retain both buffers until the GPU timeline completes, and invalidate mapped host memory before decoding. Shader disk-cache reads and writes are bypassed during an enabled diagnostic run.

Feedback does not change the selected image, sampler, sample result, guest memory, or draw arguments. It does add shader work and three ordered readbacks, so timing comparisons must account for instrumentation. Nonzero samples include alpha and do not prove correct RGB output. Zero samples do not prove a descriptor error: empty textures, UVs, borders, or zero executed fragments remain possible explanations.

## Capture and acceptance

The paired host/runtime scripts build an isolated pinned candidate, restore the prior source bytes and executable, and run a bounded 120-second capture using a copied profile. Existing LDS/Phi regression checks remain. GDS/DMA probes and depth/raster overrides are disabled. Existing scene GPU queries, indirect-argument readbacks, targeted shader dumps, and screenshots remain for comparison.

Inspect `info/indexed-image-feedback-summary.txt`, the exact shader SPIR-V, `info/scene-gpu-invocation-summary.txt`, indirect-argument results, and late screenshots together. A completed capture, successful build, or nonzero texture sample is not a gameplay success criterion. Require an actually rendered world and scene geometry before calling GoT fixed.

Local validation: all four changed C++ translation units compile. A temporary harness calls the actual new SPIR-V emitter; final validation and full-build results are recorded with the published runner checkpoint. No GPU gameplay validation has been performed in this workspace.
