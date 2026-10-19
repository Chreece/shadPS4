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

The local C++ adaptation of this source-control-flow case compiled and reproduced **old: 1/97 mismatch, reuse 1; corrected: 97/97, reuse 97**. The fully pinned on-server replay is pending.

## Required further gates (no loop)

1. **Native behavioral replay:** both original and patched C++ compile; baseline demonstrably returns mismatched resource metadata; patched variation and cache reuse return correctly matching layout and slots.
2. **Full C++ ABI-only compile:** stage the three changed source files in an isolated GoW build, compile translation units with the established CMake flags, prove source/object restore without modifying the protected executable.
3. **GoW scene relevance:** show a shader with hash/variation whose cached SPIR-V descriptor count or layout differs from returned runtime `Info` in the *failing scene*. Preserve exact log filter and per-variation IDs.
4. **Only then, one game run:** compare the identical save/scene visually, including missing resources, frame pacing, and audio. Compiling is not a gameplay breakthrough.
5. A neutral result means the candidate remains isolated; do not return to rejected SRT/audio/CPU-scanning variants without new proof.

Source branch: https://github.com/Chreece/shadPS4/tree/fix/gow-permutation-resource-metadata-20261019

Existing failure ledger: https://github.com/Chreece/shadPS4/blob/diagnostic/gow-ragnarok-evidence-ledger-20261019/docs/diagnostics/gow-ragnarok-evidence-ledger-20261019.md
