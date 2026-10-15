# shadPS4: verified generic-fix inventory — 15 October 2026

## Preservation contract

Upstream shadPS4 `main` at `b2d7ec7d31f9ad9426db93afe079db8679b3e1e0` plus generic CPU/GPU stack `966fa45bc36ce2e46b8c14f77acb5fdd95d96fbe`, with the later independently verified generic corrections below. No game ID / shader hash branches, `SHADPS4_GOW_*` behavior variables, artificial readback mode, shader skips, forced values, or arbitrary buffer-size caps. Driver, ES-DE installation, save files and existing SSH sessions must not be altered by development or proof runs.

The source audit in `gow-main-proof-20261015-151529-3107006.tar.gz` found no forbidden game-specific patterns and verified the dependency planner, selective readback, bounded write extent, shader permutation metadata and CPU translation.

## Actual executable evidence

| Binary SHA-256 | Evidence | Outcome |
|---|---|---|
| `7ee847328072f9d65772fc85ee19f93143cbaaafe69e381d70379479ddd81a30` | GoW108, exact executable + 79 replacement objects + source overlay manifest; `gow-generic-source-parity-20261015-175930-1154162.tar.gz` | Previously rendered menu, with no game-specific compiler paths and normal readbacks. Under the later unchanged configuration it did not reproduce the rendering and encountered GPU loss; not a current performance baseline. |
| `26dfc633c3d8ebbfc72150b58319532236256e8e72fc9b1522d3c59ca7b2ccd0` | `gow-general-f64-phi-20261015-190240-1853096.tar.gz` + `gow-generic-performance-20261015-192250-2083055.tar.gz` | Actual rendered UI and characters in screenshots, 76/76 GCN tests, no Vulkan device loss, but slow gameplay/audio. Preserved as rollback checkpoint before audio locking. |
| `8fb9a3c478b9229dc478b68703cc303ac1520f7af7c3e42a1ac467910ea68ae6` | `gow-general-audio-lock-20261015-194809-2376475.tar.gz`, `gow-generic-performance-20261015-194818-2378434.tar.gz` | Rendered graphics continue, 76/76 GCN, 3/3 audio concurrency tests, no device loss; still stutters, no meaningful speed improvement. This is the latest *runtime-tested* executable. |

The tested project binary is in `~/projects/shadps4/GodOfWarRagnarok/tests/main-proof-20261015-151529-3107006/build/shadps4`, **not** the installed `~/Applications/shadps4/shadps4`.

## Proven CPU, HLE and GCN fixes

| # | General emulator correction | Evidence level |
|---|---|---|
| 1 | x86-64 guest CPU identity translation, CPUID/RDPID/TSC behavior with preserved DynamoRIO runtime | In verified generic stack; launch and guest execution reached rendering. |
| 2 | Guest CPU-mode/mask and affinity/topology support, thread state, TLS and signal fault recovery | In generic stack; combined runtime rendering verified. Do not claim each file has independent gameplay proof. |
| 3 | Guest-to-host `sceKernelLoadStartModule` native stack alignment (previous Oct 9–11 correction, restored `c76e11c0fa4e60338acc427457599fad8e0d4863`) | Regressed when omitted; afterwards passed `libSceMsgDialog` startup. Evidence is comparative, not proof it solves all crashes. |
| 4 | GCN 64-bit `S_BITCMP0_B64` and `S_BITCMP1_B64`: no unsupported U64→U32 narrowing; correct SCC for bits 0, 32, 63 and masked index | Verified 73/73 GCN tests after `7da3f43857894b7d4f2e4b0209418e2440d0198b`; still 76/76 in current build. |
| 5 | DS ordered-count/wave ordering, ReadFirstLane, scalar and vector lane operations, instruction translation and thread-group sizing | Specific named GCN regressions passed in the 76-test suite, plus combined runtime. |

## Proven shader, resource and GPU-memory corrections

| # | General emulator correction | Evidence level |
|---|---|---|
| 6 | Shader Resource Table (SRT) root dependency ordering across nested pointer/readconst dependencies | Previously changed root ordering 2→8→6 with successful visible notice; current generic planner retained and rendering later verified. No shader/hash conditional is part of the clean candidate. |
| 7 | SRT stack balance and arithmetic, runtime shift operand ordering, zero/full-width bit-extract handling, SRT Phi-dependent fallbacks | `9165f7e9bbab0d3fb846c980f63dcdfa272d4f53`; 126/126 standalone Xbyak arithmetic checks plus preserved rendering after subsequent fixes. Dynamic Phi addresses are still *not* fully flattened, and errors persist. |
| 8 | Resource discovery/patching for dynamic buffers, descriptor metadata and image/sharp associations | In proven general rendering stack and GoW108 overlay. Shader patching still reports unresolved SHARPs; do not mark complete. |
| 9 | Buffer-cache DMA fault residency and stream-buffer fast-path distinction | **Combined-build evidence only.** Part of a broad diagnostic/general-stack collection, not an independently tested upstream-ready fix. |
| 10 | Buffer/texture alias coherence, texel image subrange synchronization, and correct GPU/CPU ownership transitions | In tested general graphics stack and GoW108 manifest; render-visible combined test. |
| 11 | Bounded GPU write extents determined from real shader execution bounds, selective GPU-dirty tracking and CPU write-fault/readback coherence | General implementation in GoW108 manifest, carried into the clean stack; no hardcoded 4GiB cap, forced precise readbacks or game identifier. |
| 12 | Vulkan binding/permutation metadata and shader-cache versioning | Combined rendering/source audit. A ShaderBinary version bump already invalidates cached metadata through `LoadShaderMeta`; a redundant ShaderMeta bump is not necessary when its format is unchanged. |
| 13 | Dynamic/unresolved `ReadConst` retains real guest backing-memory semantics instead of flattened word zero, irrespective of global DMA *preference* | `c583c658eb48770eeb3970b5f7ea4ce89a1a85ff`; 75/75 GCN tests, regression tests for resolved versus unresolved reads; carries into rendered 76/76 build. Does not alter DMA setting. |
| 14 | SPIR-V Float64 scalar/vector Phi types and Float64 capability detection | `8b429401e903b5ee03698ea2546e48fc73ee4c29`; 76/76 GCN, removed concrete F64 Phi crash and immediately recovered rendered menu/character graphics. |

## Audio corrections

| # | General correction | Evidence |
|---|---|---|
| 15 | Multi-port `sceAudioOutOutputs` lock-ordering correction, release all port locks while waiting on a busy port and reacquire atomically | `9c4a99ed399f1ee9b2fe5c54a9bfe75ad12fb27e`; 3/3 concurrency checks and the 76/76 GCN suite; runs without graphics regression. **Not a proven stutter fix. Keep the correction.** |
| 16 | Stage guest samples before slow host backend playback, so the game can queue the next buffer without waiting for SDL/OpenAL pacing; backend operations serialized separately | `8674f61d61df0d2a5217b1b0d069d4fc3281b87b`. **Verified by 5/5 audio concurrency tests, 76/76 GCN tests, and a later rendering capture (`9526f716…e38a0a9de9`, 36 images, no Vulkan device loss). Still not proven to eliminate audible stutter. Retain the fix.** |

## SRT structural preflight (tested, no demonstrated speedup)

- `efa903763d7789f3c9fb89be5ac3c8b2a3be2600`: classify structurally unsupported SRT offset expressions before JIT; leave dynamic reads for their existing guest-memory backing path. No fake values, shader hashes, game conditionals or log filtering; one per-shader DEBUG summary of actual deferred dynamic reads. Add memoization and three GCN regression cases. The initial measured workload includes 16,844 Phi offset failures and 16,851 failed JIT attempts per session. Later evidence: **79/79 GCN, 126/126 arithmetic, 5/5 audio tests and rendered screenshots passed; SRT Phi errors fell from 16,844 to 0, but CPU time/FPS did not measurably improve.**
- `8442535b6fa26ef90cf2a16f850c0a5ca429e995`: pinned preservation/testing runner; keeps the fully rendered binary and all previous generic CPU/GPU/audio corrections, rechecks GCN, standalone SRT arithmetic and audio tests, then runs the original-settings performance profile with a versioned shader cache.

## Outstanding work — do not claim solved

- Sound stutters and sluggish controls continue after fixes #15 and #16; audio timing/listening improvement is still unproven.
- FPS capture: **806 unique graphics pipeline keys** (no duplicated keys); 1,127 shader compilations across 754 stage/hash pairs (specializations may legitimately repeat).
- Latest FPS-instrumented profile: **0 unsupported SRT Phi errors, 23 other SRT failures, 41,226 `Clamped size` events**, all starting from 0xffffffff-byte descriptor size, across 37 shader-stage hashes.
- GPU busy averages 12–14% and emulator CPU 375–380%. Heavy guest, culling, task-manager and GpuComm thread workloads suggest a CPU/command-preparation bottleneck; specific hot functions remain unprofiled.
- Synchronous logging and disk pipeline caching disabled in the existing user configuration were **observed**, not changed. Do not toggle them as a workaround.
- Full playable combat/performance and audio timing are **not** proven, even though the menus and rendered characters work.
- The installed ES-DE emulator remains unchanged and may differ from the tested project binary.

## Non-regression requirements for subsequent work

1. Preserve SHA-pinned rendering checkpoints `26dfc633…`, `8fb9a3c4…`, and latest runtime-tested `9526f716…`; every incremental build preserves its previous executable before linking.
2. Keep **79/79 GCN**, **126/126 SRT arithmetic**, and **5/5 audio concurrency** regression gates. The structural SRT preflight passed, but its performance benefit is unproven.
3. Any new general correction must be source-level and feature-independent; reject game/shader hash conditions and all forced settings.
4. Verify current source provenance and unchanged global/per-game config, render screenshots, source/runtime logs, and output binary SHA before promotion.
5. Resolve audio and graphics slowness separately and compare equal settings/inputs before claiming performance improvements.

## Evidence and upstream-readiness audit — 15 October 2026, 21:19 local

**Current quantified rendered baseline.** Tested emulator SHA-256: `a22d1eab27f1b4dc1d0bcf384d00eca7118320affe7a82ec6e15b69cec047214` (previous binary `3546bd6d…` preserved). Native FPS from `gow-frame-fps-20261015-211915-3395335.tar.gz`, CPU/GPU profile from `gow-generic-performance-20261015-211928-3399206.tar.gz`, screenshot/console from `gow-auto-gameplay-20261015-211929-3399234.tar.gz`.

- **Animated Kratos scene, steady comparable period ~70–130 s:** 6.83 new guest frames/s, 6.83 host presents/s, ~5.0 FPS 1% low; median frame interval 133.4 ms, p95 166.9 ms, p99 200.0 ms. The lower full-capture FPS includes loading and shutdown gaps; **combat FPS has not been demonstrated**.
- CPU 374.96% aggregate, RX 7900 XTX 12.6% busy over 146 s. `Game:Main` 92.54 CPU-seconds, `shadPS4:GpuComm` 91.21 s, `GpuCullMonitor` 85.2 s and heavily loaded TaskManager threads. No Vulkan device loss.
- **41,226 clamp events** in 11.4 MB of log data; every request starts with `4294967295`, across 37 shader-stage hashes. Correct descriptor extent is unresolved. Do not silence the errors or hardcode buffer caps without understanding valid unbounded-resource semantics.
- FPS telemetry is opt-in, tested with actual game flips separately from host swapchain presentations. The instrumentation itself has no validated before/after speed improvement.
- `proof/gow-upstream-clean-generic-20261015` holds one evidence document plus 15 proof scripts; **not** a consolidated implementation branch. The fixes are separate upstream-based `fix/*` branches and working-tree patches.

**Upstream PR coordination after independent Claude review:**

- **#13 unresolved/dynamic ReadConst:** user-provided independent review reports an equivalent correction tested in both GoW and GoT. Prefer coordinating one PR, not racing duplicate submissions. Our additional metadata-version bump is unnecessary if only the cached binary semantics changed; `LoadShaderMeta` compares both versions.
- **#4 bit comparison and #14 Float64 Phi:** independently reproduced GoW/GCN failures. Strong candidates for separate focused PRs with regression tests. Broader cross-game testing remains useful but their semantic failures are already concrete.
- **#3 stack alignment, #9 DMA diagnostic stack, audio #15/#16 and SRT preflight:** do not label as standalone upstream-proven gameplay/performance fixes. Audio locking is concurrency-test-verified, not stutter-resolved; SRT preflight reduces failures, not CPU load.
- Keep existing tested local work intact, but upstream only focused source changes with isolated tests and honest evidence tiers.
