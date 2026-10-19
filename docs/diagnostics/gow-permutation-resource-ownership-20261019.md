# GoW shader permutation ownership — source correctness / validation record

**Date:** 2026-10-19. **Game:** God of War Ragnarök, CUSA34384. **Protected GoW base:** `test/gow-audio3d-queue-isolated-build-20261019`. **Candidate:** `fix/gow-permutation-resource-metadata-20261019`.

## Confirmed source defect in GoW

In the protected GoW baseline's `PipelineCache::GetProgram`, when a new shader permutation was required the code created local `Shader::Info new_info`, compiled a SPIR-V shader against it, but then discarded `new_info`. It registered shader metadata from `program->info` (the FIRST variation's layout), stored `StageSpecialization` referencing that first layout, and returned `&program->info` with the newly compiled module.

The cache-hit branch also used `info.AddBindings(binding)`, which counts images, not the actual image-array slots that the selected SPIR-V module may require.

The persistent `LoadPipelineStage` path assigned `infos[stage] = &it_pgm.value()->info` and could reuse or insert modules without associating each variation's resource metadata with that module.

**The code-level mismatch is undeniable. The frequency and impact of this path during the failing GoW scene remain unmeasured.**

## Source correction staged (not promoted)

- `Program::Module` owns `std::unique_ptr<Shader::Info>` when a variation differs from permutation zero. `GetInfo` returns the resource metadata for that specific module.
- `GetProgram` checks each existing variation using its own `Shader::Info`; misses compile/record a new variation and return its metadata, with binding slot counts adjusted for mip/image arrays.
- `LoadPipelineStage` keeps each serialized variation's `Info` alive and rejects incompatible occupied slot collisions instead of overwriting metadata.
- Original shader binary/meta/pipeline key versions **9 / 6 / 3** are unchanged; no automatic pipeline migration or deletion was made.
- Unrelated renderer, UFFD, audio source, game files and protected executable are unchanged.

## Native evidence; do not overclaim

Initial read-only test `gow_permutation_info_native_proof_20261019.py`:

- Baseline failed compilation because it lacks the **new API** (`GetInfo` and 3-argument `AddPermut`). This is *not* a before/after behavioral regression.
- Candidate compiled and ran its ownership test; ten source contracts passed; cache schema still 9/6/3.
- **The test did not compile the full emulator and did not run the game.**

Stronger read-only test: `gow_permutation_behavior_replay_20261019.py`, with launcher `gow_permutation_behavior_launcher_20261019.sh`. It extracts the original and corrected **actual** `Program` struct and `PipelineCache::GetProgram` control-flow sources, builds both under the same small C++ Shader::Info/Vulkan-module mock, then requests two resource variants with **1 and 97 image-binding slots**. Both must compile. The expected old behavior is: second SPIR-V module requires 97 image slots but GoW's returned `Info` describes 1; subsequent cached reuse advances 1 binding instead of 97. The corrected path must return the owned 97-slot layout with 97 binding slots. This specifically tests the actual source control flow while synthetic mock inputs replace real game resources.

The local C++ adaptation of this source-control-flow case compiled and reproduced **old: 1/97 mismatch, reuse 1; corrected: 97/97, reuse 97**. The on-server actual-source C++ replay passed on 2026-10-19 at 19:00 CEST using baseline `2608d5501307378a0816a05b9cca39b6bb2a874f` and candidate `c9af381aef9efd07afdc8f22738a81d87f5a3d35`. Both actual `GetProgram` source blocks compiled with identical mock dependencies. Original: `SECOND_METADATA=1`, `SECOND_ACTUAL=97`, `REUSE_BINDING_SLOTS=1`; corrected: `97/97` and `97` on reuse, with `SAME_CACHED_MODULE=YES` in both runs. Evidence archive: `gow-permutation-replay-20261019-190058.tar.gz` (user-provided server output). This is real source control flow under synthetic resources, **not yet a real emulator compilation or gameplay measurement**.

## Additional cached-specialization safety fix

Source review uncovered a separate out-of-range comparison risk in `StageSpecialization::operator==`: it indexed `other.buffers[i]`, `other.images[i]`, and `other.samplers[i]` without ensuring the two resource lists had equal lengths. This becomes particularly relevant when comparing serialized shader variations with distinct layouts. The candidate now rejects invalid specialization slots and mismatched resource-list lengths before element access. The first real translation-unit build must include this header.



## 19:33 CEST: read-only cache scan returned zero locations

User-provided `gow-permutation-cache-20261019-193322.tar.gz` has
`CACHE_LOCATIONS=0`, `GAMEPLAY_RUNS=ZERO` and a JSON report containing
`"caches": []`. No shader permutation data was inspected in that scan.
The original locator pruned directory names `source` and `build`, which
could exclude portable profiles. More significantly,
`src/core/emulator_settings.h` initializes
`pipeline_cache_enabled{false}` and `pipeline_cache_archived{false}` by default.
Thus no persistent cache is expected unless the profile explicitly enables it.
**A zero-cache scan is not proof there are no runtime shader variations.**

An expanded offline locator exists as
`scripts/gow_permutation_cache_discovery_20261019.py`, but do not repeat
blind cache searches when the feature is disabled. Instead run one isolated
instrumented scene with
`SHADPS4_PERMUTATION_TRACE=1`. The diagnostic-only branch
`diagnostic/gow-permutation-scene-proof-20261019` adds bounded warning-level
`PERM_TRACE_ACTIVE`, `PERM_TRACE_CREATED`, and
`PERM_TRACE_REUSED` events to the proven-compile candidate. These markers
must be observed *within the failing scene* and compared with the original
protected executable before claiming visual improvement.


## 20:34 CEST: full-link candidate staging update

Uploaded `gow-permutation-scene-resume-20261019-203432.tar.gz` confirms that
all 45 copied submodule Git directories were initialized successfully and
their pinned references were readable. The FFmpeg prebuilt
`ffmpeg-94dde08.zip` was reused with matching SHA-256. CMake then failed
because **tracked files were physically absent from the copied submodules**:
SDL3 `include/build_config/SDL_build_config.h.cmake` and
`include/build_config/SDL_revision.h.cmake`, and Zstd
`build/cmake/CMakeLists.txt`. The `libzstd_static` missing ALIAS is a
secondary consequence. No full build or game run completed; the protected
shadPS4 binary SHA remained unchanged.

The next script,
`scripts/gow_permutation_scene_tracked_restore_20261019.py`, checks every
staged Git submodule against its pinned HEAD tree and restores **only**
missing tracked paths with Git object data available through read-only
`objects/info/alternates`. It fails closed for absent blobs, changed pins,
insufficient disk, or unexpected CMake source, and then resumes the same
isolated CMake/Ninja build. This does **not** use dummy generated headers,
switch main branches, or alter the original dependencies.



## 20:44 CEST: full isolated diagnostic executable successfully linked

User-provided \`gow-permutation-scene-repair-20261019-204443.tar.gz\` confirms:
174 missing tracked files across eight pinned submodules were restored
using exact Git HEAD data; SDL3 and Zstd were recovered, all 45 submodules
remained at validated revisions, CMake configured, and the final full
shadPS4 link succeeded (\`[2626/2627] Linking CXX executable shadps4\`).
The isolated diagnostic binary is
\`~/projects/shadps4/GodOfWarRagnarok/tests/permutation-scene-20261019-201332/build/shadps4\`
with SHA-256
\`a6d600dac85bea7487025fa6d7d2838c69e713e37be4ea303ee8175b413cb00f\`.
Protected executable and original source-head hashes remained unchanged.
No game was launched. The full-build blocker is now cleared.

Next: a **single detached candidate scene run**, not another compiler
gate. \`diagnostic/gow-permutation-scene-proof-20261019\` contains
\`scripts/gow_permutation_scene_runtime_20261019.sh\`; it uses the earlier
hash-verified X11 navigation and cloned-save/conditional-Ollama helper,
adds \`SHADPS4_PERMUTATION_TRACE=1\` to the actual game environment,
and collects warning-level created/reused permutation markers.
An automated candidate scene detection is **not verified scene equivalence**.
Do not promote graphics fix on a trace counter alone.

## Required further gates (no loop)

1. **Native behavioral replay: PASS** — both original and patched C++ compile; baseline demonstrably returns mismatched resource metadata; patched variation and cache reuse return correctly matching layout and slots.
2. **Full C++ ABI-only compile: PASS** — 19:21:29 CEST, archive `gow-permutation-abi-20261019-192129.tar.gz`. Four real shadPS4 C++23 translation units compiled from pinned old (`2608d5501307378a0816a05b9cca39b6bb2a874f`) and corrected (`518e0d4a5985cd3ba66520a6dff925e7d1c49dcd`) sources, with the existing `GodOfWarRagnarok/build/build.ninja` flags. Both `vk_pipeline_cache.cpp` and `vk_pipeline_serialization.cpp` passed before/after. All 45 submodule revision links matched in each of the two source snapshots (90 verified links total). This is an isolated object-only compile, **not a full executable build**. Protected executable was unchanged. in an isolated GoW build, compile translation units with the established CMake flags, prove source/object restore without modifying the protected executable.
3. **GoW scene relevance: PENDING** — First inspect actual `CUSA34384` archived/unarchived shader cache for nonzero variation modules, matching serialized metadata (schema `6/9`) and differing reflected SPIR-V descriptor layouts using read-only script `scripts/gow_shader_cache_permutation_audit_20261019.py`. Cached variation existence establishes game usage, but not occurrence in the failing scene. Runtime instrumentation requires another gate. Original task: show a shader with hash/variation whose cached SPIR-V descriptor count or layout differs from returned runtime `Info` in the *failing scene*. Preserve exact log filter and per-variation IDs.
4. **Only then, one game run:** compare the identical save/scene visually, including missing resources, frame pacing, and audio. Compiling is not a gameplay breakthrough.
5. A neutral result means the candidate remains isolated; do not return to rejected SRT/audio/CPU-scanning variants without new proof.

Source branch: https://github.com/Chreece/shadPS4/tree/fix/gow-permutation-resource-metadata-20261019

Existing failure ledger: https://github.com/Chreece/shadPS4/blob/diagnostic/gow-ragnarok-evidence-ledger-20261019/docs/diagnostics/gow-ragnarok-evidence-ledger-20261019.md
