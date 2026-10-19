# Unified shadPS4: GoW Ragnarök / Ghost of Tsushima full-stack source audit

Research date: 2026-10-19. This is an **engineering input contract**, not a
claim that a new binary passes gameplay. Never promote to ES-DE without
source preflight, full native compile, focused regressions, and actual game tests.

## Why the previous ES-DE image crashes

The Oct 18 image at SHA-256
`d8ef5b2ae1b12464a5d4ca83d8e5e9c3b0f2f0053148c531eda7457c9c21117d`
was built from upstream `cd3cc802` plus the **October 5 fork main**
`aa5b281c`, quit dialog, startup GUI, and Vulkan11 16-bit feature.
It omitted complete Linux CPU identity/affinity, the GoW UFFD/deferred
invalidation source, GoW native SRT/resource and GCN corrections,
as well as the GoT v51 stencil and the October 17 visual baseline.

These omissions are **verified in source ancestry**, but the individual
immediate-crash causes remain unproved without the new CUSA-specific logs.
Never claim an absent fix was the sole cause of a crash.

Official upstream latest main was `d615b3887ba29025a59242e1a26f5b68bb176158`
at the October 19 GitHub query. Advance the pin only after re-auditing.

## Runtime checkpoints — protect these

**God of War Ragnarök (CUSA34384)**

- Last full-graphics + UFFD reference binary:
  `~/projects/shadps4/GodOfWarRagnarok/tests/gow-renderer-log-hotpath-20261017/build/shadps4.fullgfx-uffd-batched-read-gow-uffd-batched-read-20261017-231412`
- SHA-256:
  `8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef`
- 135-second runtime: full terrain/geometry/cutscene, no historical UFFD
  deadlock; audio stutters remain. Source checkpoint:
  `tools/development/gow_proven_checkpoint_20261018.json` on
  `fix/gow-proven-graphics-perf-20261018`.
- Renderer source SHA-256
  `bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a`
  (verify corresponding *local source*, not just the Git branch).
- Earlier GoW rendered general-build evidence, including 76/76 GCN
  regressions, 126/126 SRT arithmetic and 5/5 audio tests, is documented
  in `docs/PROVEN_GENERIC_FIXES_20261015.md` on
  `restore/gow-rendered-baseline-20261016`.
- The protected source tree for later work is
  `~/projects/shadps4/GodOfWarRagnarok/tests/native-rendered-gfx-20261016-152535-3014679/source`;
  it is a *source provenance*, not proof each object in the 135-second
  fullgfx binary came from its current working tree.
- Oct 19 source reconciliation proved the protected SRT code **already
  has the correct constant-left/dynamic-right shift helper**.
  Do not rewrite it or redundantly apply the same patch.
  See `test/gow-preserved-graphics-audio3d-srt-20261019`,
  `tools/development/gow_verified_srt_rootcause_audit_20261019.py`.
- The Oct 19 Audio3D mutex source change had a successful build, but
  audible stutter resolution and broader game A/B have *not* been established.
  Do not overstate its gameplay benefit.

**Ghost of Tsushima (CUSA11456)**

- The last October 17 branch checkpoint with visible horse/rider and partial
  world, no reported GPU hang during three recorded runs: commit
  `e9f470b359845a456139a865aa1d7b3001f0dbfd`
  on `proof/got-world-visible-pm4-tsharp-20261017`.
- Source/evidence: `docs/diagnostics/got-visual-baseline-20261017.md`.
  The FPS/GPU-feedback variant `bee2a087` rendered a near-black world
  when fragment feedback was enabled. **Exclude** that feedback path.
- GoT v51 source-only stencil correction:
  `~/projects/shadps4/GhostOfTsushima/tests/got-v51-stencil-reference-20261014/src/video_core/renderer_vulkan/vk_rasterizer.cpp`
  (exact path is checked on host; some test trees may store it differently).
  Verified post-source SHA-256
  `814677f63524610ba23932fd4ebbe062be299d641ac4699c390d62fe1cc1b03b`,
  pre-source SHA-256
  `02be53c70ac824ca42b1df41af93163f0e4bdfe5b1666f270193f51fd58f155d`.
  Its prior proof is included in `documents/ESDE_PROVEN_RELEASE_20261014.md`
  on `release/esde-proven-20261014`.
- GPU dynamic T# resource table: actual guest table has 27,651 records;
  incomplete Vulkan mapping of 15 entries cannot solve it.
  FS `0x7eaf40ad` had 24 unresolved dependencies, and two compute shaders
  had 44 and 76 unresolved dependencies. **Do not** promote fake/zero
  resource bindings or sampled-image feedback experiments.
  October 18-19 shader diagnostics are still actively unresolved.

## Required missing dependency classes

| Area | Include only with matching source and tests | Specific proof |
| --- | --- | --- |
| Linux guest CPU identity | Virtual CPUID, RDPID, RDTSCP, affinity, feature filter, native module/guest bounds, guest masks/signal inheritance; no forced host IDs | Upstream PRs #5287, #5304, #5314, #5315; 33/33 and 57/57 homebrew suites plus integrated game runs |
| Kernel startup | Native stack alignment for `sceKernelLoadStartModule`, module paths and lifecycle | `docs/PROVEN_GENERIC_FIXES_20261015.md`, original `c76e11c0` restoration |
| UFFD and GPU coherency | Deferred invalidation, GPU-origin direct resolution, correct full range/page rights, teardown/lifetime; source/object hash provenance | Fork PRs #10, #14, #15. Last native rebase (#16) previously had early SIGSEGV; don't copy the broken rebase wholesale |
| Shader CPU arithmetic/IR | GCN `S_BITCMP[01]_B64`, DS ordered-count, ReadFirstLane, SRT root/planner, exact dynamic-left shift, bit-extract width 32, Float64 Phi, real dynamic ReadConst memory semantics | `docs/PROVEN_GENERIC_FIXES_20261015.md`; 76 GCN and 126 SRT tests |
| GPU/resources | PM4 SET_SH_REG graphics/compute window bounds and split, real descriptor and buffer extents, GPU dirty ownership, DMA/page tracking, texture coherence, Vulkan sparse queue/BDA, correct pipeline cache metadata | Fork PR #13; 400,000 randomized PM4 checks; GoW renderer checkpoint |
| Ghost rendering | v51 masked stencil-reference semantics, GoT world-proven PM4/T# dependencies, complete dynamic descriptor tables **only where actually validated** | `docs/diagnostics/got-visual-baseline-20261017.md` |
| Audio | NGS2 + ATRAC9, multiport locking, staging and Audio3D queue synchronization where contract-verified | 3/3 and 5/5 focused tests. Stutter not resolved |
| Frontend | Quit Home/PS, consumed Cross/Circle confirmation, loading UI through black video, ES-DE single guarded launcher/rollback | Fork PR #6 and curated input regression tests |

Do not reintroduce rejected October 2 visual-regression batch, game/shader
hash-specific workarounds, GPU-feedback atomics, forced precise readbacks,
automatic Cross presses, private RADV driver replacement, or changes to
Ollama, Xorg, Sunshine, saves, system packages or SSH state.

## Integration sequence

1. **Read-only host audit** of installed binary, both saved runtime binaries,
   local compiled-source fingerprints, and CUSA startup logs.
   Missing local artifacts or unknown hashes **stop promotion**.
2. Select upstream main as a pinned ancestor, then stage the proven generic
   GoW source/CPU/GPU stack in an *isolated* worktree. Record *all* merge
   conflicts at once. Do not use `git merge -X ours`/`-X theirs` to erase
   conflicting hardware semantics.
3. Port only the documented v51 Ghost stencil and visual-baseline source
   corrections that do not regress GoW. Dynamic GPU resource tables remain
   under an explicit visual correctness gate.
4. Reconcile quit/loading and audio layers; run focused GCN, UFFD, PM4, SRT,
   controller and image regressions, full native compile and CLI smoke test.
5. Test game launch and equal-scene graphics, FPS, sound, GPU faults and
   exit before changing the ES-DE selected core. Keep the current
   SHA-256/rollback available in any case.

Host-side files that have not been uploaded or committed cannot be certified
from GitHub alone. The accompanying audit script records their exact hashes
without modifying them.
