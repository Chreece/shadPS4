# GoT: executed indexed-image sample feedback, 2026-10-19

Current candidate: [large GPU-selected buffer-table repair](got-buffer-spans-20261019.md). The latest native run completes without the previous crash, but the menu remains white and the world missing. The new candidate preserves a confirmed missing compute write at large table counts; local checks pass, native visual recovery remains unverified. Earlier evidence and pins below are historical.

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

The paired host/runtime scripts build an isolated pinned candidate, restore the prior source bytes and executable, and run a bounded 300-second capture using a copied profile. Existing LDS/Phi regression checks remain. GDS/DMA probes and depth/raster overrides are disabled. Existing scene GPU queries, indirect-argument readbacks, targeted shader dumps, and screenshots remain for comparison.

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


## Gameplay capture: view exhaustion and remaining loop-selected image

Inspected bundle: `got-indexed-table-resize-evidence-20261019-164522.tar.gz`. Its host report confirms the complete build passed and the baseline executable was restored. Its runtime report records `GAME_ABNORMAL_EXIT`, `GAME_EXIT=133`, zero reported GPUVM faults, and successful Ollama restoration. The terminal assertion identifies **TextureCache.ImageViews**, with 65,536 live entries, 65,536 capacity, two committed chunks out of two, and 64-byte elements. This is a host resource-pool exhaustion, not evidence of a Vulkan shader compilation failure.

The user reports moving terrain and intermittent horse graphics after entering gameplay; the supplied screenshots cover only seconds 20 and 40 and still show the blank menu. The archive therefore does not independently establish correct world rendering. It does establish that material compute shaders `0x04950cf6`, `0x55d17c16`, `0x52b6e003`, and `0x5c4c2b6f` reach complete strided image/sampler table binding, including populated shader permutations. All 46 captured SPIR-V modules pass local `spirv-val --target-env vulkan1.3`.

All three completed feedback rows for fragment shader `0x7eaf40ad` report **zero executed samples**. This cannot identify selected texture IDs or attribute the white menu to invalid sampled resources. Three captured direct draws produce fragment invocations; the two captured high-volume indirect draws still execute no vertices. These observations must not be generalized to every gameplay draw.

The remaining `ReadConst` image failure in `0x5c4c2b6f` is distinct from the packed fragment table: one raw sample uses explicit level zero and eight pointer-relative scalar loads selected by a loop induction Phi. Captured IR derives the descriptor byte addresses from `376 * loop_index + 440` through `+468`, relative to SGPR0/1; the loop count is another `ReadConst` at dword 486. The current indexed recognizer handles buffer-descriptor reads, so this pointer-table/loop case remains unresolved. No table length, descriptor value, or shader result has been guessed to hide it.

Candidate source: `b18cfc312904d8eaf01ed7788b85a97bcd114121`. Paired host/runtime scripts: `cd6f400af39c5649ecd03109174a2cfbca96efc1`.

The cache now reserves 1,048,576 image-view slots independently of the experimental slot-pressure eviction switch. Views and images need different capacities because an image can have multiple formats, mip/layer ranges and backing views. At the captured element size this reserves 64 MiB of virtual address space, with host pages still committed in 2 MiB chunks as needed; reservation itself allocates no Vulkan images or VRAM. The existing finite bound, stable 32-bit handles, view deduplication, image budget collection, and scheduler-deferred destruction remain. The runner keeps `SHADPS4_TEXTURE_SLOT_PRESSURE=0`. Capacity expansion removes this exact hard stop; it is not proof that cache growth has stabilized or that graphics are corrected. View high-water reports expose subsequent growth.

Targeted dumps now retain final IR and flattened user data per shader permutation, alongside the existing matching SPIR-V. Resource audits identify the permutation and no longer count successfully resolved indexed image/sampler arrays as failed static sharp fetches. Runtime feedback includes all four material compute shaders and the fragment shader, with per-shader totals. Screenshots are scheduled every ten seconds using a deadline rather than a narrow timestamp window. The evidence bundle also retains texture-pool counts and terminal assertions.

Validation: a temporary host executable reproduces the exact old failure on insertion 65,537 using the production SlotVector and entries the same size as ImageView. The enlarged reservation passes through its full 1,048,576 slots with address stability, live-handle lookup, erase/reuse, and matching construction/destruction counts. The complete Release emulator link and all existing host preflight/regression checks pass. All 80 source-overlay blobs match the built source pin. Script syntax and embedded Python checks pass; a cleanup fixture preserves abnormal exit 133, permutation IR/userdata files, and the pool assertion in the archive. Mixed-shader feedback is summarized separately. No tests were added to the repository. GPU gameplay validation of this candidate remains pending.

The launcher produces one `got-material-view-capacity-evidence-*.tar.gz` bundle after the five-minute manual gameplay capture. The original source, executable and profile are preserved. Accept only visibly restored menu/world graphics and meaningful scene geometry as rendering success.


## Five-minute gameplay capture and bounded pointer resource correction

Inspected bundle: `got-material-view-capacity-evidence-20261019-171059.tar.gz`. The complete server build passes. Runtime reaches the normal 300-second timeout (`CAPTURE_COMPLETE`, `GAME_EXIT=124`), with no reported GPUVM faults or image-view exhaustion; the baseline executable remains unchanged and Ollama restoration passes. The sampled high-water report reaches 27,706 views, below the former 65,536 limit, so this run does not exercise the expanded capacity above the old limit. Late screenshots still show missing world graphics. There is no visual breakthrough in this capture.

The 23 completed feedback rows contain 362 executed samples, all nonzero including alpha, with no recorded out-of-range indices or nonfinite results. These come from compute shaders `0x04950cf6` (301) and `0x5c4c2b6f` (61). Nonzero alpha does not establish correct texture color. Captured `0x7eaf40ad` samples remain unexecuted. All 22 captured SPIR-V modules pass Vulkan 1.3 validation. These results narrow the investigation; they do not prove a working menu or world.

The new permutation constants establish the previously unresolved pointer-loop count: flattened word `0x5e` is zero in preliminary variants and four in populated variants of `0x5c4c2b6f`. Its explicit-level-zero sample takes T# words from pointer-relative bytes `376 * index + 440..468` and the matching S# from `376 * index + 696..708`. The same GPU SSA index is bounded by an unsigned comparison against the host-uniform count. These numbers describe evidence only; the implementation contains no game, shader, stride, offset, or count special case.

Candidate source: `aa775cd9965118910d44e675708e7fc0cc2a62a1`. Paired host/runtime scripts: `78c7a116c1011b7f19728ef6982cdf331cb53a49`.

The compiler now recognizes a conservative bounded pointer-table form. It proves that the unsigned guard controls every path to the sample, verifies all twelve descriptor words share the original selector, pointer and stride, and rejects arithmetic wrap or descriptor-budget overflow. Supported explicit-level-zero samples become independently resolved candidate texture/sampler pairs; GPU equality/select instructions retain the original selection. Other sampling modes remain on the existing path. Enumerating four candidates performs up to four samples for this operation, so runtime cost still needs measurement.

After expansion, resource discovery and SRT flattening rebuild the descriptor locations. Bound-source witnesses participate in shader specialization, including zero and over-budget counts, so later count changes cannot silently reuse the wrong candidate layout. Shader cache version 48 invalidates old metadata. This is a bounded compiler lowering, not unrestricted bindless pointer support or a solution for every remaining unresolved shader.

Validation completed before publication:

- Full Release emulator build and final link pass. All existing host preflight and regression checks pass with the complete 82-file overlay.
- A temporary executable exercising the actual new pass passes seven cases: captured four-entry shape, zero count, over-budget count, guard bypass, nonmatching execution mask, address wrap and unsupported sampling mode. It checks the 48 generated descriptor word addresses and count-specialization changes.
- A second temporary executable recompiles the actual captured guest binary through `TranslateProgram`, both SRT passes, resource patching and SPIR-V emission. Captured flattened constants and the captured SRT program reconstruct known scalar memory. The previously unresolved descriptor records were absent from the archive, so explicit valid test descriptors fill those records; their contents are not represented as captured game data. Workgroup size 8×8×1 and 4,096-byte shared memory match the captured SPIR-V.
- With count four, the real-code replay reports 24 images, 7 samplers, one bound witness and **zero invalid image or sampler sources**. Counts zero and five retain the unsupported original sample (21 images, 4 samplers, one invalid pair), and live count changes reject specialization reuse. All three generated SPIR-V modules pass `spirv-val --target-env vulkan1.3`. This validates compilation, not GPU execution or the actual missing record contents.
- All three shell scripts pass syntax checking; all embedded Python compiles. A runtime-cleanup fixture preserves exit 133, the new lowering summary, and all added shader dumps. No tests or replay fixtures were added to the repository.

The five-minute runner retains the verified synchronization, LDS/Phi, table-size specialization and view-capacity changes. It also collects fragment shaders `0x836253ca`, `0x22d378cd` and `0x5116e459`, which appear in active scene records and still report unresolved resources. The next bundle is `got-bounded-pointer-images-evidence-*.tar.gz`. Check the `BoundedPointerImage` log, populated per-permutation descriptor contents and resource audit, actual geometry/query results, and screenshots together. GPU validation of this candidate is pending; restored world graphics remain the acceptance criterion.


## Confirmed pointer lowering and GPU-indexed fragment tables

Inspected bundle: `got-bounded-pointer-images-evidence-20261019-175254.tar.gz`. Its host report confirms the complete build passed and the baseline executable was restored. The runtime completed the 300-second window (`CAPTURE_COMPLETE`, timeout 124), recorded no GPUVM faults or texture-pool exhaustion, preserved the active executable, and restored Ollama. The late screenshot still shows a largely absent world. All six sampled high-volume indirect draws produce no geometry: two have index count zero, and four have instance count zero. This is not proof that every scene draw is empty.

The bounded pointer path is now confirmed on the user's GPU: 29 shader compilations use the lowering, and populated variants 4, 5 and 6 of `0x5c4c2b6f` report 24 images, 7 samplers and zero unresolved image or sampler sources. All 31 captured SPIR-V modules validate for Vulkan 1.3. Completed indexed feedback records 305 samples (226 from `0x04950cf6`, 79 from `0x5c4c2b6f`), all nonzero including alpha, with no recorded out-of-range or nonfinite results. The captured `0x7eaf40ad` samples remain unexecuted. These are compiler/execution observations, not visual acceptance.

Three newly captured fragment shaders still have one unresolved image resource each and no unresolved sampler resources. Their texture descriptors come from GPU-indexed records, while their actual sampler is host-uniform. The previous strided resource path incorrectly requires an indexed sampler from the same record. It also rejects descriptors at record offset zero, returns from contiguous-word analysis before trying strided analysis, and excludes explicit LOD and level-zero instructions.

Candidate source: `1b1ca11bfea7025c97bcde4810f892eee81c7227`. Paired host/runtime scripts: `dc625bea187590cdf2335e1ca8a6a26dc405be2b`.

The generic fix recognizes the zero-offset affine expression and continues record analysis when a contiguous-word selector is not a normalized packed-table ID. It accepts a resolvable uniform sampler, retaining its descriptor rather than creating an indexed/default sampler. Matching dynamic samplers continue to use their original GPU selector. Explicit LOD and level-zero instructions now use the indexed SPIR-V emission path, including the existing unsigned bounds check and explicit null slot. Unsupported sampler coordinate/degamma modes remain excluded from the new strided uniform-sampler path. Shader cache version 49 invalidates old compiled layouts. No game hash or fixed record layout affects emulation behavior.

Local replay uses the three actual guest shader binaries, captured flattened constants, and captured SRT programs to reconstruct the known scalar memory. No missing texture-record contents are fabricated for these fragment replays. Fragment input/export configuration is a validation fixture; the archive does not contain the complete original register state. The check establishes resource lowering and valid shader generation, not pixel equivalence to PS4 hardware.

| Captured fragment | GPU-indexed bindings | Recorded layouts | Invalid image/sampler sources after replay |
| --- | ---: | --- | --- |
| `0x836253ca` | 3 | 236-byte records, 49 entries, offsets 0/32/64 | 0 / 0 |
| `0x22d378cd` | 5 | 340-byte records × 53 and 60-byte records × 49 | 0 / 0 |
| `0x5116e459` | 8 | 340-byte records × 24 and 60-byte records × 49 | 0 / 0 |

All three generated modules pass `spirv-val --target-env vulkan1.3`, including implicit LOD, explicit LOD/level zero, and gradient samples. The earlier bounded-pointer compute replay still resolves 24 images and 7 samplers with zero invalid sources and valid SPIR-V (its previously uncaptured pointer records remain explicit fixtures). The complete Release emulator build and all existing host preflight checks pass. No tests were added to the repository. OpenOrbis hardware comparison and GPU rendering of this candidate have not been performed.

The runner previously supplied 9 and 16 hashes to filters with an eight-entry limit. Those filters failed closed, disabling targeted/invalid-source traces; global shader dumping still supplied the inspected binaries. The new target, SRT, invalid-source and feedback lists contain eight hashes, including all three fragment shaders. Resource audits also include them.

An opt-in `SHADPS4_INDEXED_TABLE_DUMP=1` capture saves actual mapped record-table bytes at binding observations 1, 32 and 256, only for targeted shaders. Each table is limited to 64 KiB; at most 64 shader/resource keys and 128 capture attempts are retained per binding thread. Filenames record shader, resource, stride, offset and count. This makes actual descriptor types and contents reviewable alongside resource-binding validity, executed GPU indices and screenshots. It does not invent descriptors or modify their selection.

All 82 source-overlay paths remain present. Shell syntax, embedded Python and filter lengths pass validation. A cleanup fixture confirms abnormal exit 133, nonzero launcher status and the table binary survive archiving. The next five-minute run produces `got-uniform-sampler-tables-evidence-*.tar.gz`. Existing production/source/profile restoration and VRAM-conditioned Ollama handling remain in place. Synchronization, LDS/Phi, table-size specialization and view-capacity improvements are retained. Visible world geometry remains the acceptance criterion; this candidate is not yet a demonstrated white-menu or gameplay fix.


## GPU confirmation of uniform-sampler fix; missing broader shader evidence

Inspected bundle: `got-uniform-sampler-tables-evidence-20261019-182330.tar.gz`. The complete server build succeeds. The runtime reaches its normal 300-second timeout with no reported GPUVM faults or pool exhaustion, restores Ollama, and preserves the active executable. The user reports no visual improvement; the 294-second screenshot still shows a largely absent world. This candidate has not restored gameplay rendering.

The GPU-side compilation audits confirm zero invalid buffer, image or sampler sources in all three targeted fragments: `0x836253ca` has 3 images/1 sampler, `0x22d378cd` has 15 images/5 samplers, and `0x5116e459` has 18 images/5 samplers. Captured bindings show populated, valid texture tables. The 108 collected record-table files contain Color2D descriptors with UNORM or sRGB formats, rather than an unsupported dimensionality explaining the missing world. All 25 distinct captured SPIR-V modules validate locally for Vulkan 1.3; the server itself reports that `spirv-val` was unavailable.

There are 111 completed feedback rows and 365 executed samples: 302 from `0x04950cf6`, 63 from `0x5c4c2b6f`, with no recorded out-of-range or nonfinite results. Nonzero counts include alpha. The three corrected fragments and `0x7eaf40ad` still record zero samples at the selected binding observations 1, 32 and 256. These early bounded observations do not establish that every later gameplay draw is empty. All six sampled high-volume indirect draws still have zero index or instance count. No new GDS, depth, clipping or raster experiments are warranted by this capture.

The broader log contains 1,221 resource compilation audits. The latest compiled variant of **82 other shader hashes** still reports an unresolved image or sampler source: 59 compute, 22 fragment and 1 vertex shader. Compilation does not establish execution or responsibility for the missing graphics. Some invalid static buffer fetches already use the existing `SBufferDynamic` GPU-memory fallback and must not be treated automatically as dropped buffer operations.

The archive's fixed watchlist includes **none of those 82 shader binaries**. Global shader dumping was enabled, and the runner retains the original `profile/user/shader/dumps` directory under the run directory; cleanup only excludes that full directory from the archive. The next useful step is to retrieve those existing files before proposing another emulation change.

Collector source: `6223d8df521183f80f08762674a9faf8090f5088`. Updated runtime pin: `3361f42a58dc3a7bb6f0a5adc317671e5fbdf17f`. Emulator source remains `1b1ca11bfea7025c97bcde4810f892eee81c7227`; no emulator rebuild or gameplay relaunch is needed for recovery.

`scripts/got-collect-unresolved-shaders-20261019.py` reads a saved run log and selects its latest resource failures, queried scene shaders, the twelve highest-volume fragment hashes and their observed paired vertices. For this log it selects 151 stage/hash pairs, including all 82 unresolved image/sampler shaders. It archives every matching GCN binary, SPIR-V module, flattened userdata file, SRT program and IR stage, with per-file SHA-256 and per-shader completeness. It matches complete shader identifiers, excludes symlinks, refuses archive overwrites, limits individual files to 64 MiB and selected input to 1 GiB, and explicitly reports partial recovery when replay inputs are missing. It reads the original run without altering it and never starts the emulator or changes Ollama.

The runtime now invokes the pinned collector automatically during cleanup and includes `remaining-shaders/unresolved-shaders.tar.gz` plus its status in future captures. Existing fixed watchlist files remain available. A collector failure does not hide the original runtime result.

Validation uses the actual supplied log for selection and explicit temporary file fixtures for archive behavior. It passes complete recovery, missing GCN/partial reporting, missing dump directory, exact-hash matching, symlink exclusion and refusal to overwrite. The runtime cleanup fixture archives all 82 unresolved shader fixtures while preserving game exit 133 and process status 2. Shell syntax and all 15 embedded Python blocks pass. These are evidence-packaging checks, not emulation or rendering tests; no repository tests were added.

Recovery command inputs: the retained run is `~/projects/shadps4/GhostOfTsushima/tests/runs/got-indexed-sample-feedback-runtime-20261019-181759`; output belongs under `~/projects/shadps4/logs/got-unresolved-shaders-recovery-*.tar.gz`. If that retained full dump directory has been removed, the collector reports it explicitly instead of claiming a complete shader capture. World rendering remains unresolved.
