# Generic SRT FindILsb32 recovery - GoW evidence, 2026-10-26

## Objective

Move Ragnarök beyond the loading screen by repairing resource-table offsets within the emulator itself, not by resetting host display services or using game IDs/shader IDs, visibility hacks, fake descriptor writes, shader skipping or forced readback modes.

## Quantified unresolved resource operations in prior runtime archives

The bounded excerpts in the previous three GoW runs show, respectively:

| Candidate | Unsupported `FindILsb32` | Dynamic `Phi` | Failed host SRT walker offsets |
|---|---:|---:|---:|
| Phi-only, game ran 32.508 s | 2,351 | 635 | 2,987 |
| Auto BDA, game ran 36.867 s | 2,340 | 635 | 2,976 |
| Fault/warning repair, game ran 29.191 s | 2,357 | 635 | 2,993 |

These are occurrences in uploaded **bounded excerpts**, not uniquely identified shaders or full-program diagnostic counts. Different runtimes may compile a different subset of shaders. Their count does not measure graphics correctness.

**Concrete recompiler omission:** `FlattenExtendedUserdataPass::ComputeOffset` logged `Unexpected instruction for offset computation, FindILsb32`, because it did not recognize the opcode. `IsAllowedOffsetInstruction` also did not include it, so dependency analysis could omit reachable host-available SRT leaves. `SrtGvnTable` did not identify equivalent `FindILsb32` expressions.

## New generic candidate

- Add host SRT `FindILsb32` with identical SPIR-V behavior: least-significant one-bit index, `0xFFFFFFFF` when the source is zero. x86 BSF leaves the result undefined on zero; CMOVZ supplies the correct sentinel.
- Allow `FindILsb32` in the permitted CPU SRT dependency graph; retain recursive failure for genuinely GPU-dependent `Phi`. Never choose a control-flow side on the host.
- Include pure unary `FindILsb32` in SRT value numbering to deduplicate equivalent resource offsets.
- Add seven focused tests, including an **executable Xbyak JIT test** that calls the production lowering on 2,081 inputs. Prior 18 tests remain; total planned 25 (on x86-64).
- Preserve the existing branch's unrelated pending GPU fault/BDA fixes and first-party compiler warning work, but do not attribute crash changes to FindILsb until compared by exact game observations.
- The remaining 635 `Phi` errors need separate GPU-side treatment, likely dynamic texture/sampler descriptor discovery; this candidate does **not** claim to handle them.

## Acceptance and next comparison

Native shadPS4 must compile and all 25 focused tests must pass **before** launching the emulator. Use an immutable new source worktree, the existing native observer, >=8 GiB VRAM headroom, and no automatic game inputs. Preserve the stable 120-second baseline SHA256 `7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1` and production executable SHA256 `8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e`.

Count `FindILsb32`/dynamic `Phi`/flatbuf warnings after game exit and inspect the actual image frames. A drop in `FindILsb32` diagnostics only demonstrates successful lowering; it is not a claim of playable graphics. Keep all trial outcomes (including failures) in `~/projects/shadps4/diary/trials.jsonl`.

## Experiment: 20261026-160302-916073 - build failed, no gameplay

- Exact source: `60384c87bd83a90a8b41141d60a64a8dc579436e`.
- Evidence: `shadps4-srt-findilsb-20261026-160302-916073-build.tar.gz` and matching worker log.
- Tests: **25/25 passed**, including `SrtFindILsb32.ExecutableJitMatchesScalarReference` (2,081 inputs). The missing SRT operation therefore passed its isolated unit tests.
- Native compilation: **FAILED** at `[2261/2633] core/file_sys/fs.cpp.o`; compiler produced three error sites at `fs.cpp:719-721`: cannot convert `shared_ptr<Core::Devices::Logger>` to `shared_ptr<Core::Devices::BaseDevice>`.
- Root cause in existing source: `class Logger final : BaseDevice` declared **private** inheritance. An earlier standard-handle cleanup replaced an unsafe `reinterpret_cast` with type-safe `std::shared_ptr` polymorphism but failed to make the inheritance public. The defect is unrelated to `FindILsb32` or Vulkan shaders.
- No emulator ELF was linked, no game was run, no video/audio/FPS outcome exists for this experiment. The 120-second baseline and production executable hashes remained unchanged; SSH remained open. `DECISION=BLOCKED`; keep the tested `FindILsb32` work but **do not promote or claim improved rendering**.
- Generic correction: make `Logger` publicly inherit `BaseDevice`, and assert `std::is_convertible_v<Logger*,BaseDevice*>` when compiling `fs.cpp`. Both edits are now committed on the isolated branch (commit `14975c44f2ab4840a2d73f15e60ef72314f6cef5`).
- Next: resume the **existing isolated cached build directory** after verifying its prior source commit and clean tracked state, then compile `fs.cpp.o` as a fail-fast target before completing the remaining native build. Preserve the failed build log/archive and append a distinct trial entry for the new run. If linked, run the same bounded native observer and count full-console `FindILsb32` and `Phi` diagnostics, checking actual rendered frames.

## Experiment: 20261026-170628-1424127 - native recovery succeeds, graphics blocked

- Build source: `14975c44f2ab4840a2d73f15e60ef72314f6cef5`.
- Native binary SHA-256: `8316d6cb2243f0d4ffd8f838a02046738d67bff426e6f344698b25b6f421cf28`. Native compile succeeded after targeted `fs.cpp` preflight; 25/25 tests passed.
- Evidence: `shadps4-srt-findilsb-20261026-170628-1424127.log`, matching `-build.tar.gz`, `.tar.gz`, `-ollama-state.json` and `.exit`. The native runtime archive has 11s, 58s and 104s PNG frames and a bounded console excerpt; the complete 2,764,180-byte console remains on the server.
- **Visible outcome**: frames at 58s and 104s are byte-identical. All inspected frames show the emulator's `Loading game... Waiting for game video...` splash. The visual timer advances from 0:07 to 0:08 and remains frozen at 0:08; the 60 FPS HUD is **not** evidence of an active game framebuffer. No menu, cutscene, terrain or gameplay graphics. Audio not observed.
- Runtime outcome: killed intentionally by the 118s+2s observer at 120.265s, `TEST_118S_DURATION_LIMIT`, observer exit 0, no early crash, no live game left behind. The game's reported 0 exit is test termination, not a proof that its work finished.
- **Shader evidence**: `CURRENT_FINDILSB32_UNSUPPORTED=0` (the previous ~2,357 bounded-excerpt occurrences are eliminated); `CURRENT_DYNAMIC_PHI=3906`, `CURRENT_FAILED_SRT_OFFSETS=5591`, `CURRENT_INVALID_SHARP_SOURCES=19`, `CURRENT_DMA_SHADERS=36`. Previous Phi ~635 and SRT ~2,993 were from **bounded excerpts** of different durations; avoid interpreting raw counts as a regression.
- **GPU evidence**: the instrumented console counter reported `DeviceLost=0` and `XIO=0`; however a `radv/amdgpu: The CS has been cancelled because the context is lost. This context is guilty of a soft recovery.` line occurs in the final excerpt during shutdown. The kernel journal was readable with no matching kernel messages; do not equate a zero DeviceLost count with zero GPU concerns.
- CPU warning audit from **incremental** build: 16 new compiler warnings (14 first-party from `printf.h`, 2 inlined/system), 0 new test warnings. The earlier full build emitted 104; these totals cover **different compilation sets**, so 16 is not a full-build warning comparison.
- Ollama `qwen3.8-greek:latest` correctly unloaded and restored with context 16,384; free AMD VRAM after unload ~19.529 GiB. Original stable binary `7604...`, protected production `8de2106a...`, saves, ES-DE and SSH remained untouched.
- Decision: **KEEP** proven `FindILsb32` host-walker implementation and standard-handle compile correction; do not mark Ragnarök playable. Preserve this now-120s-running binary SHA as a comparison candidate. Continue with GPU-controlled image/sampler resource selection, not speculative host X11 investigations.
- Next isolated experiment: handle **finite, proven immediate-offset Phi selections inside `ReadConstBuffer` descriptor words**; materialize separate texture/sampler sharp alternatives but retain the original GPU `Phi` to select the actual sampled result. Reject loop-carried/unbounded Phi sources, avoid title/shader IDs and record exact visual results.


## Experiment: 20261026-173859-1718732 — rejected zero-activation Phi matcher

- Exact source `6551cd3476bd606d44f1d6d3fd49af6abc413bc7`, binary SHA-256 `07f52d8c9f666b2b7dffcf6edba0a4e3a375684da706b4982a3e196fd0e01e01`.
- Evidence: `shadps4-srt-gpu-phi-20261026-173859-1718732-build.tar.gz` and `shadps4-srt-gpu-phi-20261026-173859-1718732.tar.gz`.
- Build and 30/30 regression tests passed. **Zero** `GPU-indexed Phi sharp selection lowered` events, so the proposed finite descriptor-branch transform **did not match the actual loaded shaders**.
- Exit `-11` at **32.856 seconds**; RADV reports a guilty context soft recovery and a Vulkan `ErrorDeviceLost` semaphore assertion. Kernel journal readable, zero matching AMD reset messages (not proof the GPU was healthy).
- Counter comparison against immediately preceding complete 120-second FindILsb trial: `FindILsb32` unsupported 0 → 0, dynamic Phi 3,906 → 3,906, failed SRT offsets 5,591 → 5,592, invalid sharp sources 19 → 20, GPU-paged shaders 36 → 36. The old run completed 120s and this run only 33s; raw counts reflect different observation windows, and remain diagnostic rather than visual acceptance.
- Frame-011s.png and frame-021s.png are **byte-identical**; both display `Loading game / Waiting for game video / 0:08 elapsed`. No game image, menu, terrain or playback observed. Audio not observed.
- Incremental native compilation emitted zero warnings, which is **not equivalent** to a clean full rebuild and cannot be compared directly to earlier full-build counts.
- Stable 120-second candidate SHA `8316d6cb2243f0d4ffd8f838a02046738d67bff426e6f344698b25b6f421cf28` was copied to an immutable test snapshot before rebuilding. Original production binary, ES-DE, game saves and SSH remained unchanged. Ollama original model/context 16,384 was restored.
- **REJECT / REVERT** the finite-branch indexed-Phi source implementation, because no source path was exercised and the game did not progress. Reverted to exact previously tested version of `image_phi_selection.inc` (`88a267c53c8458d6bd5ace7d22240fb301d4a953`). Keep the five experimental tests in history, no longer counted in the active test gate.
- Fresh diagnostic grouping from 1.5 MB retained console excerpt: **1,111** unsupported `ReadFirstLane` offset operations, **30** unsupported `GetAttributeU32`, **1,777** `Phi` complaints and **2,920** failed offsets in that *excerpt* (full output only available on host). Neither `GetAttributeU32` nor a lane-dependent `ReadFirstLane` may be evaluated on CPU. This is a concrete opcode omission with a safe subset: `ReadFirstLane` can be unwrapped iff its operand is proved scalar-uniform. New uniformity analysis rejects GPU-dependent inputs and has nine tests.
- **Next:** only compile host-uniform `ReadFirstLane` expressions, preserve all runtime-dependent GPU values; measure accepted/rejected counts and first real game video. Native build and runtime pending; no fabricated graphics claim.

