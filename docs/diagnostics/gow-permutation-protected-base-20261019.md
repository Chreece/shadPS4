# GoW Ragnarök: narrow permutation fix on protected baseline (2026-10-19)

## Why the 21:31 attempt crashed before the shader trace

The read-only startup audit `gow-permutation-startup-audit-20261019-214757.tar.gz` scanned 7 retained archives containing 9 consoles that rendered gameplay. **All nine** report the same optional/probed `/app0/exec/wad/orbis_le/sys_files.texpack.toc` and `sys_files.lodpack.toc` ENOENT as the 21:31 crashing diagnostic. Missing these TOCs alone is therefore not the distinguishing regression.

The failed candidate aborted around 7.5 seconds: guest write fault at RIP `0x154d284` to `0xac`, after `sceAudio3dTerminate` and AJM finalization. In the successful runs, Audio3D initialized and ports opened instead. Zero `PERM_TRACE_*` events were recorded. EBOOT begins with PS4 SELF magic `4f153d1d` (not a plain ELF), so a naive ELF program-header lookup does not map the guest RIP.

**Additional source baseline confounder:** the failed build was based on `test/gow-audio3d-queue-isolated-build-20261019` (`2608d550...`). Its source diverges heavily from the protected source checkout `3965a497...` (338 commits ahead / 17 behind; approximately 254 files differ). The previous emulator was not a clean permutation-only A/B test.

## Clean new candidate

New branch: `diagnostic/gow-permutation-protected-base-20261019`.
Pinned four-file source commit: `e6ca28e7386faa0624c1421a5dc0585eeb028532`.
Base: `3965a497532edb7b18027692ab04eb97fa4c145b`.

Verified GitHub compare: exactly four files change from the base:
- `src/video_core/renderer_vulkan/vk_pipeline_cache.cpp` — match the selected SPIR-V shader module with its `Shader::Info`, correct slot accounting, and bounded environment-gated warning-level trace
- `src/video_core/renderer_vulkan/vk_pipeline_cache.h` — own metadata for nonzero shader permutation modules
- `src/video_core/renderer_vulkan/vk_pipeline_serialization.cpp` — preserve owned cached permutation metadata without overwriting incompatible slots
- `src/shader_recompiler/specialization.h` — reject invalid or mismatched-size resource lists before indexed comparison

No CPU, kernel, audio, UFFD, rasterizer, or unrelated shader recompiler file changes are included in the pinned source commit. Shader binary/meta/pipeline schema remains 9/6/3.

## Next gate: isolated build of the four-file candidate

Script `scripts/gow_permutation_protected_base_build_20261019.py` performs:
1. Verify the original checkout HEAD and protected executable SHA-256.
2. Verify the exact pinned four-file diff and stage it in a separate Git worktree.
3. Extract 45 pinned submodules with `git archive` (all tracked CMake templates), provide standalone Git metadata referencing existing original Git object databases, and validate revision/status. No placeholder files.
4. Reuse FFmpeg prebuilt only if exact pin and hash match, then configure/link shadPS4 independently with established CMake flags.
5. Save logs, report build result and hashes. No emulator or game launch.

Protected executable SHA-256: `8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e`.
No claim is made that the local source HEAD necessarily corresponds byte-for-byte to every patch in the previously built protected executable. A scene-matched runtime gate is still required before promotion.

**Evidence status:** source bug reproducible in mocks; prior four C++ translation units and full link passed on the different candidate base; this new four-file base compilation has NOT yet been performed.


## 22:04 CEST: protected-base isolated build evidence

User-uploaded \`gow-permutation-protected-build-20261019-220440.tar.gz\` confirms
\`3965a497...\` original HEAD and SHA-256
\`8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e\`
of the protected executable were unchanged. The candidate pin remained
\`e6ca28e7386faa0624c1421a5dc0585eeb028532\`, exactly four source files
differ, and **45 primary Git submodules were staged at exact commits**.
Git archive restored excluded tracked files (libusb 4, protobuf 33, zlib-ng 18).
SDL3, Zstd and FFmpeg were present. No game was started.

**The full build is not yet complete.** CMake failed at
\`externals/sirit/CMakeLists.txt:90 (add_subdirectory)\`, because nested path
\`externals/sirit/externals/SPIRV-Headers\` lacked \`CMakeLists.txt\`.
This is the gitlink omitted by archived *nested* submodules, not a shader source
compile failure. External Sirit HEAD \`fc2b42d5e57ee2f638676c0c02f7fd71934b596a\`
declares \`externals/SPIRV-Headers\` at exact commit
\`2acb319af38d43be3ea76bfabf3998e5281d8d12\`.
The latter has a tracked CMakeLists.txt and no further gitlinks.

Resumption script:
\`scripts/gow_permutation_protected_nested_resume_20261019.py\`.
It requires both the original and staged pinned commits, verifies the
protected executable, reconstructs only that exact nested gitlink inside
the isolated worktree (prefer existing pinned object database, otherwise
fetch exactly the SHA), verifies the restored Git index/content and
resumes CMake and Ninja without touching the working emulator.

Next gate: **isolated full CMake configure/link**, followed by one
scene-matched trial only when it builds and the trace behaves correctly.


## 22:14 CEST: Sirit SPIRV-Headers fixed; next nested RapidJSON gap

Uploaded \`gow-permutation-protected-resume-20261019-221438.tar.gz\` confirms the
Sirit HEAD \`fc2b42d5e57ee2f638676c0c02f7fd71934b596a\` gitlink to
\`SPIRV-Headers\` SHA \`2acb319af38d43be3ea76bfabf3998e5281d8d12\` was repaired
using the original Git objects read-only. Both commit and tracked CMake content
checks passed. **CMake configure then passed.** Ninja reached 1825/2634
scheduled steps, then stopped because \`externals/discord-rpc\` failed to
include \`rapidjson/document.h\` in both \`rpc_connection.cpp\` and
\`discord_rpc.cpp\`. No separate candidate executable was linked.

Source of the next missing pinned dependency:
\`externals/discord-rpc\` HEAD
\`19f66e6dcabb2268965f453db9e5774ede43238f\`
declares \`thirdparty/rapidjson\` commit
\`d621dc9e9c77f81e5c8a35b8dcc16dcd63351321\`.
Exact \`include/rapidjson/document.h\` Git blob:
\`4b2d723224833d70208512881091a068f2d075c6\`.
The repair script \`scripts/gow_permutation_protected_rapidjson_resume_20261019.py\`
restores this source **only within the isolated test checkout** and resumes
existing Ninja without reconfiguring or touching production. It reports
remaining missing nested gitlinks but does not silently download optional
third-party test dependencies. The RapidJSON checkout has a nested
\`thirdparty/gtest\` gitlink that is *not* required by Discord RPC's normal
header-only RapidJSON use; no test dependencies should be fetched blindly.

Protected source and executable were unchanged in the 22:14 report.
**Shader behavior in the actual game remains unmeasured.**
