# Pending proven generic resource fixes for main

Tracking remains here because repository Issues are disabled. A proven emulator capability is retained regardless of GoT screenshot improvement. Do not confuse that with proof that a divergent `main` code path has received a native regression build.

## Proven in native GoT worktree, not yet independently forward-ported to main

- **PR #52, complete GPU-selected strided T# material table recognition:** 39 shader source recognitions including 536-byte per-record strided tables. Native evidence `got-strided-materials-120s-20261025-140547.tar.gz`. Original local source SHA `4272dd66d7f2ca5ffc6282c672d695dde7319d22`; PR52 local patched shader discovery blob `3d0df4d430336e6163923f62b80e28e22d34149c`. [Source PR](https://github.com/Chreece/shadPS4/pull/52). **Next:** isolate GCN strided matching and matching GPU ID/sampler binding in current `main`, compile and regress, then merge.
- **PR #64, genuine GPU-indexed T# with one real host-provably uniform S#:** recorded 13 new uniform-sampler binding log samples across seven shader hashes and 41 total strided recognitions, no GPU device loss in `got-guarded-uniform-sampler-120s-20261026-065033.tar.gz`. Main does not yet contain the equivalent image-array backend; don't merge the exact-host patcher onto unrelated public source directly. [Source PR](https://github.com/Chreece/shadPS4/pull/64). **Next:** adapt only generic rules to current main Vulkan/SPIR-V/renderer source, validate real sampled-image arrays and one sampler, compile + multi-game regress, merge.
- **PR #62 bounded source rejection diagnostics:** native result 58 already valid dynamic T# patterns rejected by later resource gate over 40 shaders; useful for diagnosis, but native main performance impact not established. [Source PR](https://github.com/Chreece/shadPS4/pull/62). **Next:** isolate safe bounded diagnostic counters for main, optional.

## Already promoted, don't duplicate

- PR #59 mandatory test cap `118s TERM + 2s KILL`: main commit `3b49eed54c9dee97b649b8d7ac8b9a1f5dbc33c4`.
- PR #65 diary and promotion policy: main commit `01beb8e0fbbea4462bbc661f0019624e959c53dd`.
- PR #66 SRT host-code emission rollback: main commit `8869651a2b3b1943fc7ab077074bdfbe5875a141`. **Native main test pending**, verified technique already in local GoT scheduler.
- PR #68 Precise linear-image GPU readback forward-port: main commit `b8db275e6605b4183aa27c445bb39661d991cdd1`. **Native main test pending**, prior GoT image readbacks and no-blink visually verified.

## Rejected - do not reintroduce

- PR #58 eager tiled readbacks: severe menu FPS collapse despite transfers completing.
- PR #61 SSA alias comparator: zero newly accepted resource sources.
- PR #56 repeated GPU dirty-page marking: redundant, sampled dirty state already set.

**No broad merge:** `main` and old GoT history diverged 267 commits ahead and 83 behind at 2026-10-26 audit; original tested user host source `c925523b49ef39434024b16b2b014d120471ee1a` is not on public GitHub. Isolate forward-ports. See [experiment diary](EXPERIMENT_DIARY.md) for exact effects on emulator/game and [promotion policy](PROMOTION_POLICY.md). All game runs must keep the working splash and GUIDE/A/B quit and stop at 120s with SSH connected.
