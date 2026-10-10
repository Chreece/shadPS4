# Ghost of Tsushima (CUSA11456) — evidence ledger and no-repeat contract
Last updated: 2026-10-10 (18:04 follow-up, first-vs-second-half SRT correction). Scope: Ghost ONLY, not God of War Ragnarök/PES.

## Proven baseline
- Debian 13, i9-11900K, AMD RX 7900 XTX, Mesa RADV. shadPS4 Ghost executable `/home/chreece/Applications/shadps4-ghost/fence-readback-candidate/shadps4` SHA256 `e294cc6b5fabb7c41b7f7caca20ad14e47993bd0e9f1a774f341b35846fa2fae`, reported `v0.19.0-71-g89af13f6`.
- Provenance: original source commit `89af13f6d306ebc24396b4e8e207688537cdc28b` at `~/.cache/shadps4-ghost-fullstack-20261008-131621/source`; CMake build `~/.cache/shadps4-ghost-isolated/build`. Seven archived source hash pairs verified against `ghost-c6-fence-readback-20261009-234617.tar.gz` manifest SHA256 `80e8c8007e2048915a161e4fa7078c5511300548626ffc8530bcedd454dde6d0`. Reconstruct and RESTORE all staged sources, original build objects/executable and configs; never overwrite the installed executable/saves or touch ES-DE/GoW.
- Fully unattended ONE-PASTE SSH script: preflight -> build/test -> collect reports -> cleanup only own PID -> restore -> one archive. Never exit user's SSH, kill other emulator processes, use GitHub Actions/full tests, introduce unverified source/API calls, rely on unverified hashes or Base64 payloads, or produce report-only responses.

## Tests and causes already excluded — DO NOT repeat
- Oct 7–9: HLE stack-alignment crash and FP64 missing SPIR-V declaration were diagnosed/corrected; sampler point-filter, 16-bit feature and buffer arena VUID work performed. R32/D32 9-mip copy investigation and reverse direction tests performed. Do not revisit unless new evidence targets them.
- TSC native A/B and simple CPU affinity/polling interventions did NOT make the game playable.
- `GPU.direct_memory_access_enabled` on/off: off original PS checkpoint 79, on sometimes reached second pipeline (checkpoint ~730) but original first shader also hangs with DMA on. DMA switch not a fix; key is snake_case.
- Signal-based versus `userfaultfd` tracking: userfaultfd activated but original GPU pipeline still hung. ACO waitcnt workaround did not resolve.
- Pixel shader `0x8e743c8e`, pipeline `135001eaf353a56`, checkpoint 79 before `PS_PARTIAL_FLUSH`, no VM fault. Wave64 confirmed, not just Wave32 lane63 out-of-range.
- Shader lane substitutions, native subgroup UMin, forced loop exit and 64-iteration bound are DIAGNOSTIC ONLY. They shifted failures between first and second `0x361a48f5` pipeline and/or produced a ~620-flip CPU plateau, but never gameplay. Do not reapply/retest as permanent fixes.
- `Game:Main` near guest `0xB099xx` uses RDTSC polling and compares counters `0x2C5D8B0/0x2C5D8B4`; these counters stopped changing following the GPU hang. Do not chase CPU polling as a separate root cause absent new evidence.
- Oct 10 GoW-inspired Vulkan completion probe and actual compute queue 6 A/B: `Data64=2` and `IrqUndocumented=3` are the **observed** RELEASE_MEM selectors (earlier incorrect selectors produced zero gated events). Flush-and-wait: 234 gate executions, one 2.5s timeout, 704 guest flips, same original checkpoint 79. Flush only: 302 executions, 651 flips, same checkpoint 79. Waiting for GPU completion does NOT resolve hang. Do not repeat.
- Oct 10 pixel resource audit: `0x8e743c8e` has 3 buffers, 6 images, 5 samplers; image index **2** always `SharpFetch::Invalid` (value 2) and becomes a null-image descriptor. Shader `0x361a48f5` images resolved. Image2 is referenced by 28 `ImageSampleImplicitLod #65538` calls in the archived optimized shader IR (low 16-bit image index 2, high 16-bit sampler 1), so not merely unused declaration. Static reference does not prove executed path during hang.
- Oct 10 `ghost-srt-origin-20261010-172653.tar.gz`: 710 flips, same pipeline checkpoint 79; **8/8 reads of root SRT+0x80 succeeded** with nonzero data, BUT THIS WAS THE WRONG ADDRESS for invalid image2. `flatbuf[32]` matches resolved image 5, not invalid image 2. Never repeat a root SRT+0x80 read to diagnose image2. Proven same source/build/config restoration.

## Newly established actual image-2 provenance (offline reanalysis, no rerun)
Source: `ghost-readlane-ir-v2-20261010-012401.tar.gz`, `target-shaders/fs_0x000000008e743c8e.pre-res-patch.irprogram.txt`.
- `%79–%82 = ReadConst SGPR0:1, root SRT dwords 96–99` => descriptor stored at **root SRT + 0x180**.
- `%1609 = CompositeConstructU32x4 %79,%80,%81,%82` => guest indirect buffer descriptor.
- `%1322 = UMin32(ReadLane(...,31), ReadLane(...,63))` selects the record index.
- `%1582 = IMul32 %1322, #340`; `%1597 = IAdd32 %1582, #80`; `%1608 = ShiftRightLogical32 %1597, #2`; `%1610 = ReadConstBuffer(sharp_source=true, flatbuf_off_dw=0) %1609,%1608`.
- **Correct 32-byte image T# starts at indirect buffer + selected_index*340 + 64**, with words 0–3 read at +64 and words 4–7 read at +80. The IR constructs `ImageHandle` from both `CompositeConstructU32x4` halves (`%1687/%1688`). Image2 `SharpFetch::Invalid` is consistent with 112 `ReadLane`-dependent sharp loads that cannot be statically flattened.
- Runtime `ghost-srt-origin-20261010-172653.tar.gz` reports resource buffer index1: `base=0x1500047a00` (samples1–4), `base=0x150136bf00` (samples7–10), **stride340 size18020 = 53 records**, fetch valid. These are real buffers; neither address is the root SRT address.
- Oct 10 18:01 `ghost-indirect-table-20261010-180114.tar.gz`: proven root SRT+0x180 buffer descriptor **matches** the shader-bound buffer1. All 53 records at record+80 were mapped, nonzero and readable; scanning them as complete T# yielded 0/53 valid **because this was only the last 16 bytes of each descriptor combined with unrelated following bytes**. This is an invalid diagnostic interpretation, NOT evidence the guest has 53 invalid textures. Never repeat a census beginning at +80.
- Next truly NEW evidence: safely read the **complete 32-byte T# starting at bound buffer1 + row*340 + 64**, across all 53 rows; stop after one full table (the second observed table isn't required to correct this 16-byte offset error). Do not assume which lane-reduced row is selected or synthesize an image.
- If rows are valid but shadPS4 resolves image2 to null, upstream work must address runtime bindless/dynamic resource selection, not synthesize a static T#. If rows invalid, trace the selected index and producer. Do not claim the invalid image CAUSES GPU hang until experimentally shown.

## Oct 10 18:24 — FULL image2 descriptors validated (not a GPU-hang fix)
- Archive: `ghost-image2-full-20261010-182447.tar.gz`, status=result `first-complete-image2-table-captured`, build/reconstruction/restore all PASS, original installed binary and configs unchanged.
- **Two** independently captured image tables: bases `0x1501347a00` and `0x150005fd00`; each has 53 records of 340 bytes. For BOTH, root SRT+0x180 matches the shader-bound indirect buffer descriptor.
- Reading each complete eight-DWORD T# at `table_base + row*340 + 64` returned **106/106 readable and structurally valid** descriptors; none were zero or invalid. Both tables have **exactly the same 53 descriptor WORD ARRAYS**, with 40 unique texture addresses. Every T# had image type 9 (2D), raw data-format value 38, nonzero address. Structural validity is NOT validation of texture allocations/array binding.
- Yet shader `0x8e743c8e` still has image2 `SharpFetch::Invalid` in eight runtime binding samples and binds null; the other five images remain independently resolved. Shader `0x361a48f5` has no unresolved image.
- Archived FINAL IR still contains lane-reduced record index `%645`, with 10 multiply-by-340 uses and 28 `ImageSampleImplicitLod #65538` calls (image binding2 / sampler1). This is enough to justify preserving the runtime index for a dynamically indexed 53-element descriptor array, not enough to claim the GPU hang is fixed.
- Current shadPS4 code sets image/sampler handle to a fixed U32 binding in `resource_patching_pass.cpp`; `emit_spirv_image.cpp` loads `ctx.images[handle & 0xffff].id` without runtime descriptor indexing, and `SharpFetch::Invalid` returns null. `vk_instance.cpp` does not enable the Vulkan12 sampled-image nonuniform indexing features in its device creation chain. A correct fix must preserve GPU-computed index, enable/check device features, bind all 53 real guest textures in a descriptor array, and decorate nonuniform access as required. No shader loop-bypass or static texture substitution.
- **Do not repeat** root SRT+0x80 reads, table+80 32-byte reads, or table+64 census. The texture data/provenance question is closed. Focus on shader-resource dynamic indexing and runtime selection; only new preflight worth collecting is Vulkan12 descriptor-indexing support/device limits before modifying the recompiler.

## Open questions
1. Are the 53 correctly assembled 32-byte T# descriptors starting at row+64 valid? The last census mistakenly began at row+80, so its 0/53 result must not be reused.
2. Which lane-reduced index does the hung draw use, and can the recompiler preserve runtime descriptor semantics (including divergent/control-flow masking)?
3. Does accurately resolving that resource permit GPU completion AND transition beyond PlayStation Studios logo, without modifying original shader loop semantics?

## Gate for declaring progress
Real gameplay must proceed beyond logo, not merely more guest flips/checkpoints, no RADV hang, correctly restored user environment. Never merge an unproven diagnostic patch to upstream.
