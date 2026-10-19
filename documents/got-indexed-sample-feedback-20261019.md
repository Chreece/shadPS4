# GoT: executed indexed-image sample feedback, 2026-10-19

Gameplay graphics remain unverified. This branch retains the verified LDS/Phi and Vulkan synchronization work, adds opt-in sample feedback, and repairs indexed texture-table specialization. It does not yet claim a white-menu or scene-geometry fix.

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

Local validation: all four changed C++ translation units compile. A temporary harness calls the actual new SPIR-V feedback emitter twice in one fragment function, including the null-sentinel and bitmap-overflow boundary values; its generated module passes `spirv-val --target-env vulkan1.3`. This validates the instrumentation structure, not execution of the real game shader. The full Release build and final shadPS4 link also pass locally with Clang 19 and libstdc++ 14 headers. Local dependency/header-path issues were resolved without repository source changes. No GPU gameplay validation has been performed in this workspace.


## Initial feedback runner checkpoint

- Diagnostic source: `c917fafd138eac21df2fb2a99c5afd21f432986a`.
- Paired host/runtime scripts: `c29a3f0e29af8949603d563f1beb2a42d464f52e`.
- Entry point: `scripts/got-indexed-sample-feedback-build-and-run-20261019.sh`.
- All 80 unique source-overlay paths exist at the source pin. Both runner scripts are required at the script pin before the launcher starts building.
- All three scripts pass `bash -n`; their embedded Python compiles. The feedback-summary parser was exercised with representative completed-capture data.
- Outputs: `~/projects/shadps4/logs/got-indexed-sample-feedback-host-*.tar.gz` and `got-indexed-sample-feedback-runtime-*.tar.gz`.
- The corrected server build and GPU capture remain pending. Production is unchanged; white-menu and world-rendering success are not claimed.

## Host preflight repair

The server archive `got-indexed-sample-feedback-host-20261019-160205.tar.gz` stopped with `STATUS=HOST_TESTS`, `EXIT=1`, and an empty `build.log`. Compilation had not started. Its final report confirms `CANDIDATE_BUILT=0`, `BASELINE_EXECUTABLE_RESTORED=1`, `GAME_LAUNCHED=NO`, and `OLLAMA_CHANGED=NO`.

The runner's stale source assertion required the GDS capture call immediately after `ResetBindings(true)`. The feedback capture now sits between those calls, so the assertion rejected valid ordering. The repaired guard checks both `DispatchDirect` and `DispatchIndirect` independently, requiring exactly one reset and one GDS capture in each function, with reset first. It permits intervening statements. Failures now report `PRODUCTION_SOURCE_PREFLIGHT_FAILED` and preserve the traceback in both `info/source-preflight.log` and `host-tests.log`.

Local validation executed the runner's complete source-preflight and host-test block against all 80 pinned source blobs, ending with `HOST_ONLY_TESTS=PASS`. This includes the producer-address and LDS singleton checks that the server never reached. Temporary negative cases confirmed that removing the reset or placing it after capture fails in either dispatch function; the actual intervening feedback call passes. No emulator source changed in this repair. The source pin and previously successful full Release build remain the same; a successful server run and rendered world are still unverified.

## Packed texture-table layout repair

Candidate source: `c8121ad971408e5fb28b2da28de09625fa53efe0`. Paired host/runtime scripts: `5228e7c2eafb057216b69498c0f065e9327e2353`.

The subsequent server archive `got-indexed-sample-feedback-host-20261019-161026.tar.gz` confirms `HOST_ONLY_BUILD_PASS`, all host checks, the complete emulator link, and restoration of the original executable. Both attachments in this follow-up are host archives; neither contains a runtime capture. The user's in-game regression and earlier horse-visible build are therefore not yet tied to exact executable hashes. The retrieved earlier screenshot has no associated commit, so it is not a safe rollback target.

The resource audit identified a reproducible cache defect. Table-size witnesses were recorded only for strided compute material tables. Packed tables, including fragment tables used by `0x7eaf40ad`, had none. Indexed images return a null compile-time T# to establish their SPIR-V type, so ordinary image specialization also skips them. When a packed guest table changed length, shader selection could reuse the old fixed array layout. Binding then rejected the size mismatch and supplied null images for the whole table.

This change records distinct table sources for packed and strided tables in all shader stages, including empty tables. Specialization compares their current complete entry counts and selects a new variant when the represented size changes. Address-only changes retain reuse. The existing GPU selector, bounds/null-sentinel behavior, sampler pairing, compute all-or-none rule, LDS/Phi fixes, and synchronization remain. Shader cache version 47 prevents reuse of metadata that lacks these witnesses. No game or shader identity affects the fix.

A temporary executable calls the actual resource-patching witness collector and `StageSpecialization`. Before the change, both fragment and compute shaders incorrectly reused a 16-entry layout for tables of 0, 17, 27,651, and 32,769 entries. Afterward, all four transitions reject reuse. Further checks passed 80 transitions covering packed/strided layouts, raw-byte/record-count V#, empty/populated initial tables, and both stages; 16 address-only cases retain reuse. The complete Release emulator build and all existing host preflight checks also pass. No tests were added to the repository.

The corrected runtime allows five minutes for manual entry into gameplay and captures later screenshots, the affected material compute shaders, and table-selection events. Missing menu-specific queries are recorded as missing evidence because gameplay can use different pipelines. The launcher combines the host and runtime archives into one `got-indexed-table-resize-evidence-*.tar.gz` upload, including abnormal-runtime results. Fixture validation exercised dump collection, archive creation, and preservation of the runtime failure exit code.

This fixes a verified missing-texture failure mechanism. The current captures do not prove that a table-size transition caused the white menu or the horse regression. Restored world graphics remain a GPU acceptance requirement.
