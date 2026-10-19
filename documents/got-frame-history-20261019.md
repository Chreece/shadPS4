# Missing world: inspect execution and pass history

Input: `got-alias-coherence-evidence-20261019-220051.tar.gz`, emulator source `ad7320c7111d466c7a44a68d544bcddc3d3e855a`.

The 300-second run completes without a reported GPU fault, but the world remains absent. The synchronized HDR scene image already contains the empty ground seen in the composed image. This rules out the final compositor covering an otherwise correct world **at that captured frame**. It does not exclude an earlier overwrite, clear, failed mesh draw, or the separate white-menu problem.

The existing GPU queries are not representative of the scene. Four indirect observations of one selected shader pair have zero instance count; three direct controls execute. Meanwhile, the draw trace records over 162,000 submissions for fragment `0xd0355f00`, which those queries never inspect. That fragment has a paired vertex shader doing skinning. The same query filter misses fragment `0x836253ca` and the dominant depth-only passes. These are investigation identifiers, not rendering special cases.

The new native 1,775-record table validates through the actual `BuildBufferTableSpan` implementation. Its four descriptor offsets yield 1,775 / 0 / 1,775 / 663 live descriptors; all live ranges fit their computed spans. All 161 inspected captured SPIR-V modules (material-table variants plus the selected mesh vertex/fragment modules) pass Vulkan 1.3 validation. Neither check proves correct GPU inputs or rendered pixels. No new shader behavior fix is justified by these checks alone.

Earlier available 17:10 screenshots already show the same nearly empty ground. They do not provide a reproducible horses-visible baseline, so a regression relative to that older user-reported build has not been isolated.

## Diagnostic change

`SHADPS4_FRAME_PROBE=1` captures two consecutive guest frames at approximately 60, 120 and 210 seconds after the first presentation:

- Every submitted draw is eligible, including depth-only, direct, indirect and fullscreen draws. No game hash, attachment address or guest index-count whitelist selects queries. The per-frame limit is 4,096 draws; exceeding it is explicitly reported, and query completion is counted independently.
- The first frame of each pair reads actual indexed-indirect arguments for the first two observations of each vertex/fragment pair, capped at 128 copies per frame. Existing argument interpretation and buffer/count bounds checks remain in effect. Non-indexed indirect arguments are not copied.
- The second frame saves attachment contents at actual render-pass ends. It records existing Vulkan image handles and subresources, copies every eighth horizontal row, and restores the original layout before the next operation. No shader or guest draw argument is replaced. Image copies use a separate host buffer and wait for the existing GPU timeline before saving.
- Image capture covers supported single-sample formats in color passes at least 640 by 360, including their attached depth image. It is limited to 96 images and 64 MiB per image-capture frame. Unsupported formats, budget exhaustion, and absent native captures must not be interpreted as empty images. Standalone depth-only passes still receive draw queries, but no pixel snapshots.
- Compute dispatches and image clears, uploads, copies and resolves are logged inside the selected frames to connect render-pass results with intervening writes.

The two frames have distinct purposes: argument copies can end a render pass; the following image-capture frame does not introduce those argument-copy interruptions. Queries still introduce measurement overhead, and GPU-native validation is pending.

The collector includes shaders observed by the frame census and compute trace. The analyzer creates a pipeline CSV, query-completeness summary, pass-image previews and normalized depth previews. Raw samples and metadata remain in the archive. A missing query is reported as missing, not counted as zero geometry.

Vulkan query semantics follow the [Khronos query specification](https://docs.vulkan.org/spec/latest/chapters/queries.html): pipeline counters and occlusion results describe different parts of execution. Absolute fragment and clipping invocation counts are implementation-dependent; zero/nonzero observations and relative results are the intended use here.

## Validation and native use

The complete Release build and link pass. Temporary external checks execute the production checkpoint selector and image-capture body against a command recorder, checking bounded scheduling, argument fairness, pass-end callback reentrancy, copy offsets for non-multiple-of-eight heights, depth-copy sizes, layout restoration, preservation before a later overwrite, and image budgets. The analyzer check verifies that missing query results are not classified as empty draws. These checks do not execute Vulkan on the user's GPU. No repository tests were added.

The runner retains the verified shader and synchronization changes, restores the baseline source and executable, and uses the isolated profile. Its source overlay now includes the scheduler pair and both frame-probe headers. Keep the menu visible until approximately 150 seconds, then enter gameplay. This allows the first two checkpoint pairs to cover startup and the settled menu; the final pair covers gameplay. Upload the printed `got-frame-history-evidence-*.tar.gz` after the five-minute run.

This candidate closes specific diagnostic gaps. It does **not** claim to repair the white menu, restore the world, or identify a proven overlay culprit. Native output is required to locate the first failing draw or overwrite and justify the next emulation change.
