# General-purpose shadPS4 integration contract for ES-DE

Date: 2026-10-19

## Goal

A **single** Linux shadPS4 executable selected by the normal ES-DE launcher,
built from current upstream with generally correct engine behavior and all
*validated, compatible* project progress. There must be no GoW/Ghost-specific
binary selection, `CUSA`-based internal patches, shader-hash exceptions,
fake resource data, special guest-input sequences, or runtime experiments
silently enabled as permanent compatibility fixes.

## Current evidence — do not misreport

- Current isolated combined build **compiles**, binary SHA-256
  `48ae4ac2ec8f42e2155425521c8699359b466dd6459905948762de539865b5bc`.
- Its God of War Ragnarök startup **crashes** with a write fault at `0xac`.
  The older SHA-verified full-graphics GoW executable
  `8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef`
  was subsequently reported visually okay using the test profile.
- Ghost of Tsushima in the combined build displays logos, then a black screen
  with music; it remains alive and can exit cleanly with Home/PS. Shader
  `0x5c4c2b6f` reports unresolved SRT/dynamic resources. These IDs identify
  **test traces**, not permission to key production behavior on shaders.
- Uploaded `esde-fullstack-regression-triage-20261019-193924.tar.gz` includes
  only `triage/manifest.json`. The historical Oct 17 Ghost archive was absent
  at the hardcoded path, so `--reference-got` **did not launch a game**.
  Do not infer that an older Ghost binary passed or failed.
- Prior GoW runtime-backed five-file graphics/UFFD contract:
  `vk_rasterizer.cpp` SHA-256 `bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a`,
  `page_manager.cpp` `8ddd21535bd66135f98fe8a855528e2e409048873a0fdcc41bcc5312f929ce21`,
  `gnmdriver.cpp` `12ae19b6effe8e020e9c5a1f47b886dbc790818d1610566be6c28facd572f62e`,
  `page_manager.h` `f5a661ef28f7b00b5c513da33897a31487990a55f2646f2bb4cab7883c4c7511`,
  `vk_rasterizer.h` `889759b700915f2076ee95ce4af50c5fc8c0c90fee5901c03ec73bff1b165992`.
  These are evidence inputs, not instructions to replace current upstream
  files wholesale.

## Implementation requirements

1. **Upstream first.** Keep an exact commit and audit every upstream change
   against each fork fix. Do not duplicate fixes already merged upstream.
2. **CPU/kernel:** correct Linux guest affinity/CPU identity, CPUID/RDPID/
   RDTSCP and SSE4a/Jaguar behavior, guest module ABI, lifecycle and
   stack alignment, bounds and error handling. No host CPU topology leakage.
3. **Memory:** userfaultfd, guest page rights, deferred invalidation, GPU
   producer/consumer synchronization, sparse buffers, GPU write extents,
   coherent lifetime and ordering. No thread/memory unsafety or deadlock.
4. **Shader/IR:** correct real dynamic SRT pointer/offset semantics and
   control flow, Phi nodes, GCN arithmetic and DS operations, GPU-resolved
   descriptors, legitimate nonuniform image indexing. Never invent a
   descriptor count/texture/sampler on failed resolution.
5. **Vulkan:** general pipeline metadata, complete resource tables, image
   view and texture cache lifecycle, depth/stencil, compute/indirect barriers,
   queue ownership, readback, and swapchain presentation.
6. **Audio:** NGS2/ATRAC9, Audio3D and multiport locks preserving
   concurrency. Audio stutter remains unsolved until measured.
7. **Frontend:** PS/Home quit, correctly consumed confirmation keys, loading
   UI, reliable controller reconnect and default ES-DE integration.
8. **Feature flags:** permitted for bounded diagnostics and temporary
   scientific A/B only. A working production fix must have sound general
   semantics with diagnostics disabled, without title/shader feature gates.

## Promotion gates

- Source provenance and compatibility: all expected source/header changes
  accounted for, CI compiler+link, zero unresolved merges.
- Focused native test suites: CPU/Jaguar, memory/UFFD, PM4, GCN, SRT,
  GPU descriptor bindings, texture cache, audio and gamepad/quit flow.
- Same-binary gameplay: at minimum preserved GoW rendered scene and Ghost
  stage progression, not merely alive/playing music. Include earlier
  unaffected games for negative regressions.
- Save and profile preservation, ES-DE Home quit, startup loading, GPU fault
  safety and rollback checks. Only then select the unified binary.
- Record clean builds and all failures. Do not label a compile-only candidate
  working, nor assert that all previous experiments are proven fixes.

## Next engineering checkpoint

The current compiled candidate **fails the gameplay gate**. It must not
replace the installed ES-DE core. Reconstruct and compare the five proven
GoW UFFD/graphics files against merged upstream; separately port the general
Ghost SRT and indexed-resource compiler improvements while excluding GPU
feedback experiments. Test one shared binary on both titles before promotion.
The historical Ghost archive's missing path is evidence-unavailable, not a
reason to add a game-specific code path.
