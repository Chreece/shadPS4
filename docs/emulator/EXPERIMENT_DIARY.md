# Combined shadPS4 test diary — append only

Created 2026-10-26. **This is the universal emulator diary for ALL games**, not a separate GoW log. The [canonical chronological ledger](TRIALS.jsonl) contains GoW/combined, GoT, PES/TLG and future-title events; the derived [universal fix registry](FIXES.json) answers whether an algorithm was kept, rejected, merged or still awaits hardware tests. Original game-specific notebooks retain screenshots and provenance but do not independently promote fixes. Never erase failed, superseded or rejected attempts.

## Cross-game engineering evidence

| Emulator-wide hypothesis / trial | Observed game(s) and effect | Recorded decision |
|---|---|---|
| Native CPUID/RDTSCP/RDPID, no DynamoRIO | Oct26 combined native GoW **build/link PASS**; gameplay and cross-title runtime pending | **BUILD VERIFIED — RUNTIME PENDING**; #63 is not merged to main |
| Full-process DynamoRIO | Earlier GoT performance rejected; combined GoW startup crash and GDB executable-restart complications | **REJECT**; never auto-select external CPU runtime |
| GPU-indexed strided T#/S# material tables | GoT #52 native test: 39 strided recognition events; incomplete world | **KEEP generic implementation on GoT progress tree**; selective main port pending |
| Real Precise linear-image GPU readback | GoT #57 hardware: 32 sampled image transfer events, silhouettes stopped blinking. Main #68 port fixture PASS, main native test pending | **MERGED main** `b8db275e...`, **hardware revalidation pending** |
| Real uniform-sampler indexed tables | GoT #64 native test: 13 new guarded bindings, terrain still incomplete | **KEEP GoT capability**; not yet in shared main |
| Incomplete host SRT walker code rollback | Existing GoT local source already guarded; main #66 reviewed source-port parity | **MERGED main** `8869651a...`, **native main build pending** |
| Tiled eager GPU image readbacks | GoT #58 ran native and severely reduced menu FPS without world recovery | **REJECT**; preserve negative evidence |
| SSA GVN alias-based descriptor acceptance | GoT #61 native: no newly accepted alias cases | **REJECT**, do not repeat on counts alone |
| Extra GPU page dirty marking | GoT #56: pages already dirty, no visual improvement | **REJECT duplicate** |
| Shader SRT parent-source diagnosis | GoT #62 hardware: 58 rejected events, #67 parent-read diagnostics proposed | **KEEP diagnostics**, #67 native test pending |
| Kernel sanitizer/thread-atexit imports | GoW: two focused regression suites PASS, duplicate linker fixed, translated runtime still crashed | **KEEP generic tested HLE**, native cross-title pending |
| PM4 WAIT ZPASS and base-instance handling | PES 2025 and TLG historical PRs describe unresolved rendering hypotheses | **OPEN PR / UNVERIFIED in current native baseline** |
| SDL/OpenAL port readiness and UFFD circular-wait safety | Prior GoW PRs include native error/deadlock evidence; not yet replayed on latest common main | **MAIN-PORT REGRESSION PENDING** |

Game names above identify **test evidence only**. They never enable a special emulator fix or runtime switch for a title. For any new change, consult the global ledger for previous positives, negatives, exact source hashes, affected engine subsystem and next falsifiable check **before building**.

## Original GoW/combined chronological archive (retained)

## Baseline currently observed

* Current upstream snapshot: `2cdefaa1298c6e4ece6c74bd84e90f3906282d51`; fork main before this documentary commit: `01beb8e0fbbea4462bbc661f0019624e959c53dd` (84 unique fork commits, 159 upstream commits missing from common ancestor). **Not rebased**.
* Latest native-only combined engine source: `a82db5964bd8a48305213171862d23268602a50c`. Exact Oct26 uploaded archive proves **full native build/link PASS** with DynamoRIO/CPU-mode option removed; **gameplay untested**. SHA256 `3e5c8c3f15d29313f20db584673c10fd52d581ed1f370ecf62fb8bc5dfbc3947`.
* Protected **prior gameplay-capable** GoW SHA256 `8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e`; previous combined crashing ELF `793a76912b4647677bc7c6c2b5087ae1f1b1c530c5664aaae623d689cdc6e847`.
* GoT [diary](../got/EXPERIMENT_DIARY.md) preserves KEEP #52/#57/#64 (generic indexed materials, real linear image readback, guarded uniform sampler) and diagnostic #62, rejects duplicate dirty marking #56, performance-regressing tiled eager copies #58, and ineffective matcher #61. Missing world geometry and ~5–6 FPS remain unresolved. Do not forward-port full unreviewed ancestry.

## All uploaded combined/GoW trials through October 26

| Date / immutable archive | Engine evidence | Actual game effect | Decision / next |
|---|---|---|---|
| 2026-10-25 `gow-upstream-got-20261025-145442-451241.tar.gz` | CMake preflight encountered intentional historical patch whitespace; protected ELF preserved | NOT_RUN | **SUPERSEDED** — repair preflight only |
| 2026-10-25 `gow-profile-resume-20261025-153128-1038367.tar.gz` | 49 Shader::Profile Vulkan fields verified; SamplerResource::NumBindings missing | NOT_RUN | **SUPERSEDED** — add proper generic sampler array binding count |
| 2026-10-25 `gow-sampler-resume-20261025-154146-1195243.tar.gz` | Sampler count compiled; vk_instance.cpp designated-initializer order failure | NOT_RUN | **SUPERSEDED** — correct actual Vulkan declaration order |
| 2026-10-25 `gow-vkfeatures-resume-20261025-155636-1418508.tar.gz` | Vulkan order compiled; sampler.cpp lacked existing integer sampler policy header | NOT_RUN | **SUPERSEDED** — include shared sampler_filter_policy.h |
| 2026-10-25 `gow-final-build-20261025-160815-1590861.tar.gz` | Native full executable linked, CLI/ldd passed; ELF d4d1a9c6; original protected unchanged | NOT_TESTED | **BUILD_PASS_RUNTIME_PENDING** — test isolated GoW/GoT |
| 2026-10-25 `gow-combined-gameplay-20261025-165326-2263378.tar.gz` | GPU free memory ~7 GiB below 8 GiB threshold | NOT_RUN | **KEEP_DIAGNOSTIC** — free Ollama VRAM conditionally, never force per-game CPU mode |
| 2026-10-25 `gow-combined-gameplay-20261025-170827-2492523.tar.gz` | Ollama 7.811->19.529 GiB free and Qwen ctx8192 restored; SIGABRT -6 host NULL read with DynamoRIO active | No gameplay, no audio initialization | **REJECT_TRANSLATED_RUNTIME** — find actual host fault caller |
| 2026-10-25 `shadps4-kernel-rebuild-20261025-173404-2874320.tar.gz` | Both sanitizer/atexit suites passed; duplicate Linker::FindByAddress broke link | NOT_RUN | **KEEP_HLE_REJECT_DUPLICATE** — deduplicate synchronized lookup |
| 2026-10-25 `gow-combined-gameplay-20261025-174617-3066055.tar.gz` | HLE adapter linked, GPU 8.593 GiB free; SIGABRT -6; RIP main shadPS4 ELF | Last log libSceMsgDialog, no gameplay | **KEEP_HLE_REJECT_DYNAMORIO** — symbolize 0x100f5c6 |
| 2026-10-25 `gow-native-crash-symbols-20261025-175251-3165536.tar.gz` | RIP 0x100f5c6 in fmt v12 parse_format_string, vmovdqa null read; caller unproven | NOT_RUN | **KEEP_DIAGNOSTIC** — capture native call stack, do not suppress fault |
| 2026-10-25 `shadps4-fmt-backtrace-20261025-181516-3501705.tar.gz` | DynamoRIO exec invalidated GDB breakpoint; SIGTRAP occurred but no backtrace; Ollama restored | No gameplay | **REJECT_DIAGNOSTIC_METHOD** — preserve negative debugger result; native-only CPU execution |
| 2026-10-26 `shadps4-native-rebuild-20261026-070115-2184798.tar.gz` | DynamoRIO fully absent in CMake/Ninja/CLI; native CPUID handled, binary 3e5c8c3f15d29313f20db584673c10fd52d581ed1f370ecf62fb8bc5dfbc3947 linked | NOT_TESTED | **BUILD_PASS_RUNTIME_PENDING** — 120s cross-game GoW/GoT runtime and actual loading/quit visuals |

## Strict per-run record contract

Archive file/hash, original source and changed files, pinned upstream commit, target PR, before/after binary SHA, test method, same-scene visuals and FPS (or NOT_OBSERVED), native exception/GPU fault, accepted/rejected reason, and next check. **Build pass is not gameplay pass.** No speculative promotion to fork main; proven algorithms are kept in the canonical game branch then selectively rebased and PR'd on current upstream plus retained main fixes.
