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
| 9 | Buffer-cache DMA fault residency and stream-buffer fast-path distinction | General source audit + combined rendering. Keeps normal small read-only stream behavior and correctly populates the address-paging path. |
| 10 | Buffer/texture alias coherence, texel image subrange synchronization, and correct GPU/CPU ownership transitions | In tested general graphics stack and GoW108 manifest; render-visible combined test. |
| 11 | Bounded GPU write extents determined from real shader execution bounds, selective GPU-dirty tracking and CPU write-fault/readback coherence | General implementation in GoW108 manifest, carried into the clean stack; no hardcoded 4GiB cap, forced precise readbacks or game identifier. |
| 12 | Vulkan resource binding/permutation metadata and shader cache format versioning | In clean audit and rendered stack; cache versions bumped when serialization or SPIR-V behavior changes. |
| 13 | Dynamic/unresolved `ReadConst` retains real guest backing-memory semantics instead of flattened word zero, irrespective of global DMA *preference* | `c583c658eb48770eeb3970b5f7ea4ce89a1a85ff`; 75/75 GCN tests, regression tests for resolved versus unresolved reads; carries into rendered 76/76 build. Does not alter DMA setting. |
| 14 | SPIR-V Float64 scalar/vector Phi types and Float64 capability detection | `8b429401e903b5ee03698ea2546e48fc73ee4c29`; 76/76 GCN, removed concrete F64 Phi crash and immediately recovered rendered menu/character graphics. |

## Audio corrections

| # | General correction | Evidence |
|---|---|---|
| 15 | Multi-port `sceAudioOutOutputs` lock-ordering correction, release all port locks while waiting on a busy port and reacquire atomically | `9c4a99ed399f1ee9b2fe5c54a9bfe75ad12fb27e`; 3/3 concurrency checks and the 76/76 GCN suite; runs without graphics regression. **Not a proven stutter fix. Keep the correction.** |
| 16 | Stage guest samples before slow host backend playback, so the game can queue the next buffer without waiting for SDL/OpenAL pacing; backend operations serialized separately | `8674f61d61df0d2a5217b1b0d069d4fc3281b87b`. **Verified by 5/5 audio concurrency tests, 76/76 GCN tests, and a later rendering capture (`9526f716…e38a0a9de9`, 36 images, no Vulkan device loss). Still not proven to eliminate audible stutter. Retain the fix.** |

## Performance-correction candidate (NOT YET RUNTIME PROVEN)

- `efa903763d7789f3c9fb89be5ac3c8b2a3be2600`: classify structurally unsupported SRT offset expressions before JIT; leave dynamic reads for their existing guest-memory backing path. No fake values, shader hashes, game conditionals or log filtering; one per-shader DEBUG summary of actual deferred dynamic reads. Add memoization and three GCN regression cases. The initial measured workload includes 16,844 Phi offset failures and 16,851 failed JIT attempts per session. CPU benefit and screenshot parity must be measured in the next run.
- `8442535b6fa26ef90cf2a16f850c0a5ca429e995`: pinned preservation/testing runner; keeps the fully rendered binary and all previous generic CPU/GPU/audio corrections, rechecks GCN, standalone SRT arithmetic and audio tests, then runs the original-settings performance profile with a versioned shader cache.

## Outstanding work — do not claim solved

- Sound stutters and sluggish controls continue after fixes #15 and #16; audio timing/listening improvement is still unproven.
- About 800 distinct graphics-pipeline compilations and 1,100+ shader-module builds per 146-second run; no proof of duplicate pipeline-key compilation.
- Latest after-audio profile: 16,844 unsupported SRT/Phi offset reports and 44,208 very-large (0xffffffff-byte) descriptor clamp events across ~146 seconds.
- GPU busy averages only ~12%, emulator CPU ~376%; the game render/submission path is the candidate bottleneck.
- Synchronous logging and disk pipeline caching disabled in the existing user configuration were **observed**, not changed. Do not toggle them as a workaround.
- Full playable combat/performance and audio timing are **not** proven, even though the menus and rendered characters work.
- The installed ES-DE emulator remains unchanged and may differ from the tested project binary.

## Non-regression requirements for subsequent work

1. Preserve SHA-pinned rendering checkpoints `26dfc633…`, `8fb9a3c4…`, and latest runtime-tested `9526f716…`; every incremental build preserves its previous executable before linking.
2. Keep all 76 GCN tests, 126 SRT arithmetic checks where applicable, and 5 audio concurrency tests. The proposed structural SRT preflight adds 3 new GCN tests (79 total) and remains unproven until executed on the actual AMD server.
3. Any new general correction must be source-level and feature-independent; reject game/shader hash conditions and all forced settings.
4. Verify current source provenance and unchanged global/per-game config, render screenshots, source/runtime logs, and output binary SHA before promotion.
5. Resolve audio and graphics slowness separately and compare equal settings/inputs before claiming performance improvements.
