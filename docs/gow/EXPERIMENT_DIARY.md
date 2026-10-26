# Ragnarök development notebook — append only

> **UNIVERSAL_EMULATOR_DIARY_AUTHORITY (2026-10-26):** This is a **game evidence view**, not a separate emulator fix ledger. One canonical append-only history for **all games** is [docs/emulator/TRIALS.jsonl](https://github.com/Chreece/shadPS4/blob/main/docs/emulator/TRIALS.jsonl); current accepted/rejected/merged state is [docs/emulator/FIXES.json](https://github.com/Chreece/shadPS4/blob/main/docs/emulator/FIXES.json). GoW compile, crash, audio, and visual observations; universal fix decisions live on fork main. Source-level fixes remain emulator-wide, with no title or shader-hash overrides. Never erase failed attempts.

Canonical GoW branch `gow/proven-progress-20261026`; started **2026-10-26**. This tree is the native-only combined candidate source with **build proof, not gameplay proof**. Keep every accepted, rejected, blocked and superseded experiment. No title-dependent source fix or launch configuration.

## Protected proof

* Last **gameplay-capable** binary: `~/projects/shadps4/GodOfWarRagnarok/build/shadps4`, SHA256 `8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e`; do not replace.
* Last **linked translated combined binary** preserved at `shadps4.pre-native-793a76912b4647677bc7c6c2b5087ae1f1b1c530c5664aaae623d689cdc6e847`, same SHA256 fragment `793a...`; rejected as the normal runtime because DynamoRIO was previously rejected.
* Current **native-only, full-build PASS** commit `a82db5964bd8a48305213171862d23268602a50c`, built ELF SHA256 `3e5c8c3f15d29313f20db584673c10fd52d581ed1f370ecf62fb8bc5dfbc3947`, archive `shadps4-native-rebuild-20261026-070115-2184798.tar.gz`. No gameplay result from this executable.
* Exact upstream at last source integration `30f4170896ce974353a6f6486d0ecec179aec54b`; latest confirmed upstream on 2026-10-26 is `2cdefaa1298c6e4ece6c74bd84e90f3906282d51` (**three newer upstream commits absent**); no latest-upstream claim before rebasing/retesting.
* PR [#60](https://github.com/Chreece/shadPS4/pull/60) generic kernel sanitizer and atexit fixes; PR [#63](https://github.com/Chreece/shadPS4/pull/63) native CPU only. Both require final shared-main promotion review.
* Mandatory game loading splash and GUIDE -> quit dialog, Cross/A confirm, Circle/B cancel, standard fullscreen/FPS; actual gamepad and visual acceptance still to be tested on the new native binary.

## Tests and effects

| Date/run | Exact generic work and observed engine result | GoW Ragnarök effect | Decision/next |
|---|---|---|---|
| 2026-10-25 14:54 `gow-upstream-got-20261025-145442-451241.tar.gz` (`62dd7a3`) | Failed early preflight due to historical dynamorio.patch intentional whitespace; original ELF unchanged | No game | **SUPERSEDED** — repair precise source preflight; never confuse with renderer regression |
| 2026-10-25 15:31 `gow-profile-resume-20261025-153128-1038367.tar.gz` (`c842cb65`) | Shader profile 49 Vulkan fields PASS; SamplerResource::NumBindings missing broke compilation | No game | **SUPERSEDED** — correct descriptor count for real GPU-selected sampler arrays |
| 2026-10-25 15:41 `gow-sampler-resume-20261025-154146-1195243.tar.gz` (`e5f7dd89`) | Sampler binding fix compiled; C++ designated Vulkan feature order failed | No game | **SUPERSEDED** — keep support and correct field declaration order |
| 2026-10-25 15:56 `gow-vkfeatures-resume-20261025-155636-1418508.tar.gz` (`99ad960f`) | Vulkan device feature order compiled; integer sampler policy functions undeclared due to missing existing header | No game | **SUPERSEDED** — add header without changing integer texture sampling semantics |
| 2026-10-25 16:08 `gow-final-build-20261025-160815-1590861.tar.gz` (`b0b6dcb2`) | Native full combined engine linked; CLI+ldd PASS, binary d4d1a9c6; original GoW ELF unchanged | Unverified | **KEEP_BUILD_ONLY** — test actual gameplay and compare protected GoW build |
| 2026-10-25 16:53 `gow-combined-gameplay-20261025-165326-2263378.tar.gz` (`b0b6dcb2`) | Only ~7 GiB free VRAM, protected 8 GiB minimum stopped before game | Not launched | **BLOCKED** — conditional Ollama model unload only if low VRAM |
| 2026-10-25 17:08 `gow-combined-gameplay-20261025-170827-2492523.tar.gz` (`b0b6dcb2`) | Ollama unload 7.811->19.529 GiB free; restore Qwen ctx8192; user SIGABRT -6 after null host read while DynamoRIO active | No audio/gameplay | **KEEP_VRAM_REJECT_TRANSLATOR** — identify host fault without inventing game-specific CPU flags |
| 2026-10-25 17:34 `shadps4-kernel-rebuild-20261025-173404-2874320.tar.gz` (`ed2c09ec`) | Both sanitizer and thread-atexit regression tests passed, final link failed from duplicate Linker::FindByAddress | Not launched | **KEEP_TESTS_REJECT_LINK_DUPLICATE** — consolidate one synchronized function |
| 2026-10-25 17:46 `gow-combined-gameplay-20261025-174617-3066055.tar.gz` (`104cec06`) | Kernel hooks linked; Ollama not needed at 8.593 GiB free; null read still SIGABRT -6 | Stopped near libSceMsgDialog loading | **KEEP_HLE_REJECT_TRANSLATOR** — resolve exact crash address in main ELF |
| 2026-10-25 17:52 `gow-native-crash-symbols-20261025-175251-3165536.tar.gz` (`104cec06`) | ELF address 0x100f5c6 falls inside fmt::v12::detail::parse_format_string; vmovdqa attempts NULL argument record read | No game, static investigation | **KEEP_DIAGNOSTIC** — original caller stack unknown; no source workaround |
| 2026-10-25 18:15 `shadps4-fmt-backtrace-20261025-181516-3501705.tar.gz` (`104cec06`) | GDB lost pre-exec breakpoint under DynamoRIO; fatal SIGTRAP but no caller stack captured; Ollama restoration verified | No gameplay | **REJECT_DEBUGGER_METHOD** — native-only execution, use real native breakpoints |
| 2026-10-26 07:01 `shadps4-native-rebuild-20261026-070115-2184798.tar.gz` (`a82db596`) | No DynamoRIO CMake/CLI/Ninja/runtime, native CPUID methods preserved, full binary linked and CLI+ldd PASS; ELF sha256 3e5c8c3f15d29313f20db584673c10fd52d581ed1f370ecf62fb8bc5dfbc3947 | Not run yet | **KEEP_BUILD_ONLY** — native 118+2 GoW/GoT runtime and visual regression |

## Rules for all future runs

A later fix to the tests **does not erase** the earlier failure. Every record must include exact user-local source SHA, blob diff, upstream parent, all relevant PRs, archive SHA, binary SHA, exit/cause, observed renderer/audio/emulator effect, *separately* observed per-title visuals/FPS, decision and next test. Run all games with the **same** generic native emulator; only the game selector varies, plus standard fullscreen/FPS. No forced CPU-ID, no DynamoRIO, no game ID or shader-hash exceptions, no automatic game inputs. Enforce **118s TERM + 2s KILL**; conditional Ollama model unload if GPU VRAM is below 8GiB and post-exit exact-model restoration. Keep SSH/Sunshine and saves untouched.

## Next falsifiable investigations

1. Replay current native `3e5c...` ELF against the exact previous 17:46 startup null-read scene, read-only native stack capture if it persists; do not assume fmt or the message-dialog HLE itself is at fault without a caller stack.
2. GoT native cross-title smoke against protected [GoT baseline](../got/BASELINES.json), preserving PR #52/#57/#64 status while auditing GPU coherence and dynamic samplers; do not merge entire game ancestor stack.
3. Capture same-scene GoW menu/gameplay image and FPS, shader warnings, missing snow/ground, audio/video clock and stability; protect original working ELF and previous baseline.
4. Rebase verified generic stack onto latest upstream in isolated worktree, not a production install; release only proven modifications through main-targeted PRs.

In this game notebook, `PASS_BUILD` means code compilation/link only; a gameplay result always requires independently collected run evidence.

## 2026-10-26 08:23 — native-only baseline crash and next verified diagnostic

* **Evidence:** `shadps4-native-universal-20261026-082312-3381759.tar.gz`, SHA256 `0841f4d6845bd353961e2b4e3e93495b547c9c15f6f98997db3b074aeb7acab7`. This run is also recorded in the single [universal emulator diary](https://github.com/Chreece/shadPS4/blob/main/docs/emulator/TRIALS.jsonl), which is authoritative across all games.
* **Exact source/binary:** `a82db5964bd8a48305213171862d23268602a50c`, ELF SHA256 `3e5c8c3f15d29313f20db584673c10fd52d581ed1f370ecf62fb8bc5dfbc3947` (native CPUID/RDTSCP/RDPID, **no DynamoRIO**).
* **Emulator evidence:** launched using only the normal `CUSA34384 --fullscreen true --show-fps`; exited with **SIGSEGV -11 after 4.804 seconds**, after logging `Loading libSceMsgDialog`. Fault read address `0x0`, native program RIP `0x55bf345a65c6`, owning main-executable ELF virtual address `0x10095c6`. Previous translated binary reported a similar fmt parse crash at shifted ELF offset `0x100f5c6`; this proves DynamoRIO is **not necessary** to reproduce it, but **does not prove the actual caller or source defect**.
* **Environment:** Ollama `qwen3.8-greek:latest` unloaded only after low VRAM (`7.839 -> 19.560 GiB` free) and restored successfully with original 8192 context. Isolated profile had **no dumped system fonts** warning; potential test fidelity limitation, not a proven fault cause.
* **Game outcome:** no gameplay, visuals, FPS or audio were observed. Do not infer any renderer change. Protected earlier gameplay-capable GoW ELF SHA256 `8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e` remains reference.
* **Decision:** **KEEP DIAGNOSTIC / GAMEPLAY BLOCKED.** Universal fault class `native-startup-null-fault` now in shared FIXES registry. The new generic native GDB script captures actual C++ caller/threads/ELF mappings at `unreachable_impl`, without code or GPU changes. Fix only a causally established shared emulator defect, then cross-test GoW and GoT, keeping all rejected attempts in the universal ledger.


## 2026-10-26 — host kernel ABI alignment regression integrated on latest upstream (test pending)

- **New exact native diagnostic:** `shadps4-native-fatal-gdb-20261026-084822-3754276.tar.gz`, SHA256 `46f8fb9a93b947362dbdc47898b97b543fd7796e3e4466187bb91d949d3f7994`. Game:Main stack unequivocally shows `sceKernelLoadStartModule()` initial `LOG_INFO` -> `Common::Log::VLog` -> `fmt::v12::detail::parse_format_string` -> SIGSEGV address zero in aligned SIMD load, then native signal handler. **No gameplay achieved**; GDB itself exited normally after recording evidence.
- **Previously attempted universal fix located:** `fix/kernel-hle-stack-align-upstream-20261015` at `c76e11c0` applied `__attribute__((force_align_arg_pointer))` to the common kernel module-load HLE entry. That tested/source solution was omitted from the 2026-10-25 combined native C++ source. This is not a CUSA-specific override; it addresses the PS4 guest/native host ABI mismatch for every game calling the API.
- **Restored exact source + test:** `fix/generic-hle-entry-stack-alignment-20261026` at `c32812b59f30f90f495bd43952020ab25486fc35`, draft [PR #71](https://github.com/Chreece/shadPS4/pull/71). The standalone generic x86-64 fixture simulates misaligned guest call and checks a nested host call before/after native stack realignment. A focused GCC -O2 mechanism probe succeeded offline, but the user-hardware CTest and full emulator runtime are **pending**.
- **Latest upstream + inherited shared fixes:** `integration/native-universal-upstream-hle-20261026` at `803bb428731be794bdc44e38b56313d030a7403a`, draft [PR #73](https://github.com/Chreece/shadPS4/pull/73), based on upstream main `2cdefaa1298c6e4ece6c74bd84e90f3906282d51`. Includes all ten latest upstream BDA/RenderDoc source hunks and the divergent fork-main generic SRT host-code rollback #66 and real Precise linear-image GPU readback #68, in addition to prior combined native CPU/GPU/HLE features. No per-title compatibility logic or DynamoRIO.
- **Decision:** `KEEP_SOURCE_CANDIDATE / BUILD_AND_GAMEPLAY_PENDING`. Compile and link new source using `scripts/shadps4_upstream_native_hle_rebuild_20261026.sh`, which preserves original GoW gameplay ELF `8de210...` and prior native ELF `3e5c8c...`, runs native HLE alignment and sanitizer/atexit CTests, then schedules the same 118s+2s universal fullscreen/FPS GoW test with conditional Ollama restore and universal diary ingestion. Do not call crash fixed without an actual game run. If fault persists, capture `si_code`, `RAX` and `RSP` at original fault and revisit the ABI hypothesis.

