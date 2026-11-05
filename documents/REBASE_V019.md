<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Stable v0.19.0 integration — 2026-10-03

Upstream base: tag `v.0.19.0`, commit
`c7e065d1b415be16c23e260a21f1dd8bbfc4cb57`, released 2026-10-02.
Previous local main: `873499647c31606a8ce5dd748867a845bc353381`.
Integration branch: `rebase/v0.19.0-confirmed-fixes`.

The branch carries the retained net changes from the previous main onto the
official stable release. Its source is the difference from the common ancestor
`d9cf41ba0ae2ebac746fdb843af1716002ab86a0`, rather than a replay of experimental
commits followed by their reversions. Existing topic branches retain the original
implementation history. Main integrates the new release-based tree by a forward
merge; no shared history is force-rewritten.

## Retained scope and evidence

| Change | Basis and remaining limit |
| --- | --- |
| NGS2 audio/ATRAC9 streaming, speaker matrix, queued-block interpolation and direct filters | Existing implementations and focused regression tests are preserved. Earlier user tests confirmed working cutscene audio/progression. Recent RDR and Bloodborne captures load native `libSceNgs2`; they do not prove the HLE path is active. |
| Sparse-capable queue selection and BDA allocation flags | Retained separately from audio, with no new GPU workaround added by this port. |
| Missing-user startup guard | Previously reproduced startup fault and successful user startup after correction. |
| Occlusion counters and count-control correction | User confirmed the outdoor-light-through-buildings symptom improved. This is not a fix for all disappearing geometry. |
| Depth/image transfer ordering and depth-growth initialization | Concrete validation/code defects and focused regression tests. Recent Bloodborne logs recorded the depth-growth path, without GPU readback or proof that visual glitches are resolved. |
| Presentation semaphore waits and fragment-read barriers | Identified synchronization defects; no new claim of a visual cure. |
| Home/PS quit confirmation and input routing | Latest RDR and Bloodborne captures recorded consumed confirmation input and normal emulator exit. |
| Absent sanitizer-provider behavior and thread-exit callbacks | Focused ABI, callback and module-reference tests; module unloading remains unsupported. |
| Kernel unlink error propagation | Filesystem regression checks plus expected missing-temp and subsequent options-save activity in game logs. |
| Registry diagnostics and evidence collection | Read-only investigation support, not a registry implementation. Known development-tool keys still need a verified response contract before implementing their values. |
| Local Docker installer and rollback | Existing installation, ordinary ES-DE launch entries, saves/settings and eight-channel audio are preserved by the tested installer. |

The old texture-containment, raw-buffer synchronization, tiled-mip experiment,
late audio-trace experiment and rejected open-PR batch are not reapplied as local
patches. No forced shader serialization or validation setting is introduced.
Sunshine and display/session repairs are outside this repository change.

The official release itself includes upstream mip-layout, shader and kernel
changes that overlap some earlier trial work. Those release changes are retained
as upstream baseline; their inclusion is not new user confirmation that the old
combined PR trial worked. The historical review and cleanup documents describe
the earlier builds and are not the release's acceptance record.

## Port details

The retained patch applied without conflicts. Only `CMakeLists.txt` and
`src/video_core/texture_cache/texture_cache.cpp` were modified by both upstream
and our retained patch. Both merges preserve the new upstream changes.

The image-transfer test fixture was adapted to upstream's renamed
`SlotVector::Insert` API: its fake method and two call sites were renamed.
All other retained files outside the two merge-overlap files match the previous
main byte-for-byte. No submodule revision is overridden from the stable release.

## Checks performed on the rebased source

Linux GCC 13.3, Release configuration; every suite configured and built separately.
CTest results are test entries, not a count of all inner assertions or scenarios.

| Suite | Passing CTest entries |
| --- | ---: |
| ngs2_hle | 11 |
| userservice | 1 |
| occlusion_query | 2 |
| image_transfer | 4 |
| graphics_diagnostics | 2 |
| quit_dialog | 6 |
| kernel_sanitizer | 1 |
| kernel_thread_atexit | 1 |
| kernel_unlink | 14 |
| kernel_regmgr | 12 |
| Total | 54 |

The thread-exit suite also compiled the real linker adapter. The unlink
permission test ran with a reduced-privilege child. All 41 Python tests from
`python3 -m unittest discover -s scripts -p 'test_*.py'` passed, including installer,
rollback and session-scoped evidence-collector tests. `git diff --check` passed.
Sanitizer and all-platform CI runs were not repeated for this integration.

To reproduce a focused suite, run `cmake -S tests/<suite> -B <build-dir>
-DCMAKE_BUILD_TYPE=Release`, then `cmake --build <build-dir>` and
`ctest --test-dir <build-dir> --output-on-failure --no-tests=error`, after checking
out the repository's pinned submodules.

## Local acceptance still required

The complete emulator has not yet been compiled or run on the user's system at
this new revision. Build main with the pinned `scripts/install_local_default.py`
Docker workflow. It switches only after the build and startup checks succeed,
and keeps one previous core with a printed RESTORE command. Saves, configuration,
7.1/eight-channel audio and Sunshine settings are not changed.

Arm `collect_graphics_evidence.py` for that exact revision before opening the game;
`--wait-minutes 0 --session-minutes 0` disables premature capture timeouts. First
check RDR gameplay, the same previously troublesome areas and a cutscene, then
Home/PS exit. Repeat for Bloodborne. Upload each completed report alongside what
was actually seen/heard. Logs with no renderer errors do not establish correct
rendering, especially when validation is disabled. New release behavior remains
unverified until these runs; game audio remains experimental.

AI assistance: Codex prepared the release integration and this audit. This change
does not submit an upstream PR or request an all-platform build.
