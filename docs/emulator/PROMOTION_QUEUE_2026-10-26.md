# Universal shadPS4 — accepted fix / PR promotion queue

**2026-10-26 snapshot.** We keep PRs and isolated game trees until each generic fix has native source proof plus cross-title safety evidence. **Nothing below is asserted merged to fork main.**

| PR | Generic fix | Evidence and current status | Required before main |
|---|---|---|---|
| [#63](https://github.com/Chreece/shadPS4/pull/63) | native emulator-owned CPUID without DynamoRIO | 2026-10-26 linked native executable 3e5c8c3f; no-DynamoRIO CMake/Ninja/CLI verified. **KEEP_GENERIC_BUILD_PASS**, PENDING_GOW_GOT_120S | cross-title startup/gamepad/renderer test and latest-upstream forward port |
| [#60](https://github.com/Chreece/shadPS4/pull/60) | kernel sanitizer and thread atexit HLE | two HLE CTest suites passed; duplicate linker corrected, combined link succeeded. **KEEP_GENERIC_REGRESSIONS_PASS**, PREVIOUS_TRANSLATED_RUNTIME_SIGABRT_NULL_READ; native runtime not yet verified | run same native source through both games; forward-port minimal kernel changes |
| [#52](https://github.com/Chreece/shadPS4/pull/52) | GPU-selected strided T# material index layouts | 39 native recognition events including 536-byte records. **KEEP_GOT_GENERIC_RESOURCE_CAPABILITY**, GOT_WORLD_STILL_MISSING | rebase exact tested source to latest upstream main+shared core, test GoW and GoT |
| [#57](https://github.com/Chreece/shadPS4/pull/57) | Precise mode linear GPU image readbacks | 32 sampled queues/32 copies, silhouettes stopped blinking. **KEEP_GOT_VISUAL_AND_GENERIC_COHERENCE**, GOT_WORLD_STILL_MISSING | cross-title readback correctness/performance and main-targeted isolated source port |
| [#62](https://github.com/Chreece/shadPS4/pull/62) | GPU-indexed shader source rejection diagnostics | 58 rejected-source events over 40 shaders. **KEEP_DIAGNOSTICS_ONLY**, GOT_WORLD_DARK | use evidence to find real descriptor readiness gates; no rendering claim |
| [#64](https://github.com/Chreece/shadPS4/pull/64) | guarded real uniform S# for GPU-indexed T# tables | 13 native real-binding events across 7 shaders; no device loss. **KEEP_GOT_GENERIC_BINDING_CAPABILITY**, GOT_TERRAIN_INCOMPLETE_AND_SLOW | prove no cross-title or FPS regression before selective promotion |
| [#66](https://github.com/Chreece/shadPS4/pull/66) | discard incomplete SRT walker machine code | open PR audit only, result not provided in this task. **REVIEW_PENDING**, UNKNOWN | read native CTest and GoT+GoW evidence and decide KEEP or REJECT |
| [#35](https://github.com/Chreece/shadPS4/pull/35) | prior main synchronization direction is reversed | fork main diverges 84 fork-only/159 upstream-only at 2026-10-26 audit. **TRACKING_ONLY**, NOT_APPLICABLE | replace by verified fresh upstream rebase preview and new main-targeted PR |

Already merged to current fork main: PR #59 hard 118s SIGTERM + 2s SIGKILL test limit, plus existing splash and GUIDE quit source. Their actual runtime/UI confirmation must still be tested on the current native executable. A generic fix without visible GoT terrain recovery is still worth keeping if real emulator correctness is proven and cross-game regressions are ruled out.

**Promotion order:** newest upstream tip -> replay current tested fork main -> isolate source delta from game tree -> native CTest and full build -> strict 120s both games -> splash/quit and normal fullscreen/FPS -> reviewable main-targeted PR -> merge -> record exact main merge SHA. Never merge old full ancestor chains or add title/shader-specific switches. No automatic DynamoRIO path.

Structured queue: [PROVEN_FIX_QUEUE.json](PROVEN_FIX_QUEUE.json). Human test diary: [EXPERIMENT_DIARY.md](EXPERIMENT_DIARY.md).