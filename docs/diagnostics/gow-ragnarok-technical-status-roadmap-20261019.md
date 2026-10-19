---
title: "God of War Ragnarök — shadPS4 Technical Progress Report"
author: "Technical evidence review"
date: "19 October 2026"
lang: en
---

# God of War Ragnarök — shadPS4 Technical Progress Report

**Project:** `Chreece/shadPS4` · **Game:** CUSA34384 · **Host:** Debian 13, Intel i9-11900K, AMD RX 7900 XTX · **Status date:** 19 October 2026

> **Bottom line:** The customized emulator can start God of War Ragnarök, pass the Continue screen, enter gameplay, and complete automated test sessions without a Vulkan device loss. It is **not yet proven comfortably playable**: late-game performance is commonly around 18–20 FPS and guest audio production repeatedly misses its deadlines, causing audible stutter. Many shader and audio patches compiled correctly, but none of the recent candidates has demonstrated a material user-visible improvement. The known-working renderer must remain protected.

## 1. Executive assessment

| Area | What is actually established | Status |
|---|---|---|
| Starting the game | Automated Continue and navigation into the gameplay world work in completed trials | **Working for tested sequence** |
| GPU memory/rasterizer lifecycle | Protected Linux UFFD/deferred invalidation path builds and completes multiple sessions with no reported Vulkan device loss | **Retain; causality not fully isolated** |
| Rendering correctness | Gameplay images can be displayed; unresolved SRT resource warnings, buffer clamps and an unhandled compute command persist | **Incomplete / not visually validated end-to-end** |
| Audio | SDL output calls are short, but Wwise guest submission gaps exceed 100 ms and late buffers are often only 35–50% ready | **Not fixed** |
| Performance | Late captured samples typically average about 18–20 FPS; workloads and scenes were not exactly matched | **Below desirable smooth gameplay; cause not isolated** |
| Save/session/test safety | Detached SSH-safe runner, conditional Ollama handling, isolated saves and restoration checks have been validated | **Working test infrastructure** |
| Recent audio and SRT experiments | Audio3D mutex compiled; SRT retry and wave-uniform fallback produced no demonstrated practical benefit | **Do not promote as gameplay fixes** |

**What “running” means here:** **booting and reaching gameplay is achieved for the tested path**. “Running correctly/playably” would require correct graphics in the intended scenes, stable frame pacing, coherent save/load behavior and sustained audio without severe starvation. We do not have evidence to mark that larger goal complete.

## 2. What changed relative to upstream shadPS4

This is a **source-level comparison**, not a controlled comparison of official upstream and the customized binary in an identical GoW scene. Therefore, it identifies plausible enabling changes **without claiming every one independently caused the move into gameplay**. The game data itself was not changed as part of these documented fixes; the work was in the emulator and its test harness.

### 2.1 Foundational emulator changes: Linux userfaultfd / GPU coherency

The most substantial, directly verifiable runtime delta is the pinned Linux UFFD branch, comparing commit [`eabc3f12`](https://github.com/Chreece/shadPS4/commit/eabc3f127b58585811d4a7fbdb4308beac99894c) to [`dafe3c58`](https://github.com/Chreece/shadPS4/commit/dafe3c58057e9ffaec711dbcf56ad66425893c27). That change touched **five core files**: `page_manager.cpp/.h`, `vk_rasterizer.cpp/.h` and `gnmdriver.cpp`.

- **Thread-aware UFFD handling.** The fault handler distinguishes a GPU command-processor fault from a fault on another guest thread using the Linux faulting thread ID. A GPU-thread fault is handled directly rather than queuing work back to the same blocked processor.
- **Deferred GPU invalidation and correct wake ordering.** CPU-origin faults queue the complete cache invalidation onto the GPU command processor. The faulting writer is awakened only after invalidation/readback completes, rather than the UFFD event receiver synchronously waiting on the processor it must keep servicing.
- **Safe memory registration and shutdown.** Existing reserved VM ranges are registered with exact lengths and partial failures safely fall back; pending invalidations are drained during teardown, while presenter/rasterizer and command-processor lifetimes are coordinated.

These behaviors **are not all present in the official upstream `main` source examined on 19 October 2026**. For example, upstream `page_manager.cpp` does not expose `InvalidateMemoryOnGpuThread` or `pending_gpu_invalidations`, whereas the pinned GoW patch does. Recent protected builds report no Vulkan device loss. That supports **retaining the GPU lifecycle fix**, but does not prove that it alone solves GoW rendering or that every historic crash was caused by this race.

**Sources:** [pinned UFFD code](https://github.com/Chreece/shadPS4/blob/dafe3c58057e9ffaec711dbcf56ad66425893c27/src/video_core/page_manager.cpp), [pinned Vulkan implementation](https://github.com/Chreece/shadPS4/blob/dafe3c58057e9ffaec711dbcf56ad66425893c27/src/video_core/renderer_vulkan/vk_rasterizer.cpp), [upstream page manager](https://github.com/shadps4-emu/shadPS4/blob/main/src/video_core/page_manager.cpp), [exact patch comparison](https://github.com/Chreece/shadPS4/compare/eabc3f127b58585811d4a7fbdb4308beac99894c...dafe3c58057e9ffaec711dbcf56ad66425893c27).

### 2.2 Preserved graphics and related CPU compatibility

The runnable configuration is **not merely the tip of one Git branch**. The tested build workflow reconstructs the retained graphics/UFFD baseline and selectively stages known shader, command-processing, memory and CPU-related changes. It pins hashes of several protected executables so an experiment cannot overwrite the only good configuration. Earlier branches also pursued Linux guest **CPU-ID and affinity compatibility** and kernel/thread behavior.

This is part of the technical progress, but the available 16 captured runs **do not contain a clean isolated upstream-versus-CPU-patch trial** proving which specific CPUID or affinity change caused GoW to progress. Those earlier fixes must be reviewed through their independent regression tests and retained only where demonstrated. A branch name or successful compilation is not sufficient attribution.

### 2.3 Audio3D source correctness: synchronization, not a cure

In `sceAudio3dPortAdvance`, the customized branch adds protection for the shared PCM/mix queue using its existing **per-port mutex**, consistent with other queue operations. This is a justified race-prevention correction; it compiled and was exercised by a complete game run.

However, the corrected build still averaged **47.02% late Audio3D buffer readiness at 18.20 FPS**, with audible stutter. **Keep the mutex as a separable source-correctness patch, but do not label it an audio fix.**

Source: [Audio3D mutex change](https://github.com/Chreece/shadPS4/blob/fix/gow-audio3d-queue-coherency-20261019/src/core/libraries/audio3d/audio3d.cpp).

### 2.4 Shader SRT handling: experiments versus retained code

The protected shader source **already implements** the correct `constant << dynamic_shift` behavior via `SrtCodegen::EmitConstantLeftShiftByDynamicCount`. Rebuilding the later inline four-instruction correction was redundant. The attempted **deferred SRT dependency retry recovered zero producer dependencies**, and the later **wave-uniform scalar-read port** did not change observed game behavior; its INFO-level activity was also hidden by the logging filter.

It is therefore inaccurate to describe these recent SRT experiments as fixes to GoW. The reliable source correction for the shift is **already part of the protected implementation**; other SRT recovery candidates are still experimental and should not be promoted.

### 2.5 Compute command handling: better observability, no implemented semantics

Official upstream still logs the `PM4ItOpcode::SetQueueReg` compute packet and does not apply its register semantics in the examined `liverpool.cpp`. The GoW fork adds **queue ID, register offset, first payload word, payload length and rate-limited occurrence counts**. This makes the failure measurable; **it does not implement the missing GPU operation**.

The recurring reported packet is **virtual queue 4, register offset `0xb`**. This distinction matters: detailed logging is progress in diagnosis, **not a functional emulator fix**.

Sources: [upstream compute command](https://github.com/shadps4-emu/shadPS4/blob/main/src/video_core/amdgpu/liverpool.cpp), [GoW instrumented command](https://github.com/Chreece/shadPS4/blob/test/gow-audio3d-queue-isolated-build-20261019/src/video_core/amdgpu/liverpool.cpp).

### 2.6 Test harness and fault containment

The following changes are **real improvements in reproducibility and safety**, although they do not themselves improve game fidelity:

- Corrected automatic PS Cross/Continue recognition, bounded W/D/A steering and releases on exit; the initial A/B tests with manual intervention or wrong menu detection were invalid.
- Detachment from SSH (`nohup`/`setsid`) and broken-pipe-safe owned emulator shutdown; a previously orphaned process was positively identified and cleaned up.
- Isolated copies of starting saves and XDG profiles, SHA-pinned binaries, restoration checks, and preservation of the Sunshine session.
- Ollama unloading **only if free VRAM falls below 8 GiB**, followed by reload/restore.
- Frame-flip, guest audio, UFFD, buffer-sync and shader telemetry; however, the active log filter can suppress important INFO-level messages and duplicate suppression hides events.

## 3. What we can measure in the game today

These results are **not guaranteed to be the same in-game scene**; the comparison is directional, not causal.

| Test checkpoint | Late FPS | Late Audio3D ready | SRT walker warnings | Ignored compute queue packets |
|---|---:|---:|---:|---:|
| Protected/Audio3D 10:32 run | 18.20 | 47.02% | 44 | 2,490 |
| SRT provenance trace 14:23 | 18.52 | 45.37% | 170* | 2,620 |
| Deferred SRT retry 14:53 | 18.07 | 47.23% | 1,520* | 2,554 |
| Wave-uniform SRT 16:20 | 18.48 | 47.77% | 45 | 2,637 |

*Higher SRT counts during instrumentation/retry reflect altered logging frequency, not necessarily more distinct broken shaders. The retry recovered **zero** dependencies. The late-game audio source still sometimes falls to around **35–40% available buffers**, and guest submission gaps exceed **100 ms** even though SDL backend calls are short and no Vulkan device loss was reported.

The latest captured session includes approximately **1.21 million large-buffer synchronization calls** across ~136 seconds, **65.9 GB uploaded**, **9.35 seconds of cumulative dirty scanning** and **14.0 seconds of cumulative buffer-sync CPU time**. This is a measured workload, **not proof it is the direct audio bottleneck**. Sparse dirty-word enumeration passed **200,002 native regression cases** but delivered no validated same-scene gameplay improvement; keep it default-off.

Across the evidence ledger's **12 available gameplay archives / 16 recorded game-mode runs**, **248,554 recorded console lines** were analyzed; another **331,832 messages were represented only by duplicate-suppression counts**. This is extensive, but it is **not every underlying game event** nor the entirety of earlier October branch history.

Source: [full evidence ledger](https://github.com/Chreece/shadPS4/blob/diagnostic/gow-ragnarok-evidence-ledger-20261019/docs/diagnostics/gow-ragnarok-evidence-ledger-20261019.md).

## 4. What has NOT been fixed — priority order

### P0 — Prove and repair the actual visible graphics fault

**Symptoms/evidence:** roughly **44–45 SRT walker failures**, **63–64 unflattened shader-resource warnings**, **~630 oversized-buffer clamps**, and recurring shader-DMA warnings across normal late-game captures. The three detailed sampled SRT shaders were `0xdbaa6ae4`, `0x059d93c5` and `0x3b4583d0`, with original and canonical flattened-buffer offsets both zero.

**Necessary change:** Establish which specific shader/resource failure corresponds to an actual missing or incorrect on-screen object or material, then correct its source discovery, descriptor construction, or guest-memory read while preserving valid wave/lane semantics. Do not allocate arbitrary flattened offsets, blanket-accept nonuniform expressions, or retry unsuccessful producers without a producer being emitted.

**Acceptance criterion:** A deterministic reference scene shows the previously incorrect asset correctly rendered **and** a focused shader/IR regression fails before and passes after the fix. Lower warning counts alone do not count.

### P1 — Implement only the compute queue register semantics actually needed

**Symptoms/evidence:** queue `4` / offset `0xb` `SetQueueReg` packets are repeatedly logged and ignored, in upstream and the customized emulator. The fork improves logging but not queue state.

**Necessary change:** Decode the packet against the relevant PS4/AMD GCN compute hardware specification, determine which state/register is affected, prove it changes GoW's subsequent dispatch or synchronization behavior, and implement only those validated semantics. Unknown register writes must **not** be guessed from the warning text.

**Acceptance criterion:** A small PM4 regression verifies register-state effects and one identified GoW compute workload executes with the expected downstream state; then one gameplay confirmation shows a visible or deterministic effect.

**Priority caveat:** This is a highly repeated missing operation, but its **causal relationship to audio or visible artifacts is not yet established**. It competes with P0 rather than automatically superseding it.

### P1 — Fix the real guest audio-production dependency

**Symptoms/evidence:** Wwise `AK::AudioOutThread` supplies audio late; the main and Audio3D output starve together. `AUDIO_GUEST_SUBMIT` gaps reach over **100 ms** while SDL calls are short. Host run-queue wait is very small during starvation, the thread is usually blocked in futex wait, and recorded audio UFFD faults are below the 21.33-ms audio period.

**Necessary change:** Identify the **specific awaited event and the producer/waker responsible for it**. Candidate causes include guest synchronization, a game thread waiting on work from another subsystem, or guest timing/scheduling behavior—not necessarily shadPS4's SDL audio. Use a tightly scoped, debuggable guest wait/wake trace or an equivalent deterministic instrumentation test, not another global audio-pacing tweak.

**Acceptance criterion:** Guest submit cadence remains consistently near the expected **21.33 ms period**, buffer availability does not collapse into the 35–50% range, and the user hears continuous sound in the same problematic scene. Correct SDL output alone is insufficient.

### P2 — Bring frame pacing and memory traffic under control

At about **18–20 FPS**, the current tested scenes are not reliably smooth. Heavy buffer synchronization/UFFD work may be one contributor, but previous scan optimizations did not establish practical value. Separate rendering execution, cache synchronization, guest CPU scheduling, and audio stalls with same-scene timing evidence before spending more effort here.

**Acceptance criterion:** demonstrably smoother **same-scene** frame times while retaining correct resources and sound; no regression from the protected checkpoint.

### P2 — End-to-end gameplay, save and regression coverage

One automated entry into gameplay and an audio-based cutscene candidate does **not** prove the game is finishable. Future milestones need actual camera/game-state validation, in-game checkpoint/save/load continuity, long-session stability, and correctly rendered combat/cutscenes. The save system must be tested independently from temporary cloned profiles before any final playability claim.

## 5. Rejected or unproven directions — do not loop

| Experiment | Verdict | Why |
|---|---|---|
| SDL pacing / outer audio clock variants | **No proven practical gain** | SDL already quick; guest producer late |
| Boost host priority of AudioOut thread | **Not justified** | Host runnable wait low; blocked thread needs its dependency, not priority |
| Blame audio UFFD faults alone | **Rejected as direct cause** | No recorded fault exceeding one audio period |
| Sparse dirty-word scanning | **Native-equivalent, gameplay benefit unknown** | A/B scenes/manual input contaminated comparisons |
| Idle `last_output_time` clock | **Rejected** | No identified direct game import of that query API |
| Audio3D PortAdvance mutex as stutter fix | **Correctness only** | Audio stutter remained |
| Deferred SRT retry | **Reject** | Zero recoveries; 1,520 logged failures, no visual gain |
| Wave-uniform inline SRT port | **Unproven / not promoted** | No practical change; INFO telemetry filtered |
| Rebuilding existing SRT shift helper | **Redundant** | Protected source already contains correct helper |
| Repeated A/B with mistimed menu input | **Invalid evidence** | Manual control and non-equivalent game scenes |

The failed variants remain useful as **negative evidence**, not as progress. Revisit any row only if a newly identified cause changes the hypothesis.

## 6. Recommended plan to make the game actually playable

**Phase 1 — Freeze the best known working build.** Keep the protected executable, its exact shader/renderer source snapshots, and the existing saved game untouched. Preserve the Audio3D mutex patch separately as a correctness fix. Do not promote the last SRT candidate merely because it compiled.

**Phase 2 — Choose one demonstrable functional defect.** The best next gate is either (a) a specific shader hash tied to a visible missing resource, or (b) a verified `SetQueueReg` hardware-state rule tied to a subsequent GoW dispatch. Both require a small reproducible test **before** the next emulator build.

**Phase 3 — One minimal correction, one test.** Make the smallest source change that passes the failing native/IR/PM4 regression; keep every other setting and binary identical. One gameplay confirmation should use the same save/scene and show the expected *user-visible* effect. Do not use aggregate warnings or unrelated timestamps as substitutes.

**Phase 4 — Address audio's actual waker.** Once the blocked dependency is identified, implement and validate the fix in the relevant subsystem. Aim for sustained guest audio production without >100-ms gaps or missing-buffers cascades.

**Phase 5 — Long-run playability and upstreaming.** Verify several game areas and cutscenes, real save/load, graphics integrity, continuous audio and acceptable frame pacing. Only then integrate retained corrections into an upstream-compatible branch and propose isolated PRs with reproducible regression tests.

### Decision gates for further builds

No new gameplay build unless all four are true:

1. The suspected code is **actually reached** by a failing GoW workload.
2. A small deterministic regression proves **incorrect behavior before / correct behavior after**.
3. The expected user-visible effect is named **before** the run; test path and instrumentation make it observable.
4. The original binary/saves stay protected, the run is SSH-independent, and Ollama is unloaded only if free VRAM is below 8 GiB.

## 7. Verification limits and candid conclusion

- The current report is grounded in the available evidence ledger, checked GitHub source, exact UFFD patch comparison, and previously recorded tests. It does **not** contain a controlled identical-scene execution of today's official upstream `main` versus the patched build. Therefore the overall increase in playability cannot be accurately attributed to one isolated change or given an upstream FPS delta.
- Logs show launch and automated gameplay for the tested route, but **no complete start-to-finish successful game run**; they also show unresolved audible stutter and rendering/compute warnings.
- The latest user-visible claim supported by this record is **"boots and reaches tested gameplay, still incomplete"**, not "GoW Ragnarök fixed" or "fully playable."

**The practical objective now is to deliver one observable correction to a broken game scene, and subsequently eliminate the audio producer's actual blockage.** Repeating generic audio and SRT experiments without an effect on screen or sound would be more activity, not progress.

---

### Evidence and source links

- [Experiment ledger and complete per-run matrix](https://github.com/Chreece/shadPS4/blob/diagnostic/gow-ragnarok-evidence-ledger-20261019/docs/diagnostics/gow-ragnarok-evidence-ledger-20261019.md)
- [Pinned UFFD patch: six commits, five core files](https://github.com/Chreece/shadPS4/compare/eabc3f127b58585811d4a7fbdb4308beac99894c...dafe3c58057e9ffaec711dbcf56ad66425893c27)
- [Protected GPU UFFD / page-manager implementation](https://github.com/Chreece/shadPS4/blob/dafe3c58057e9ffaec711dbcf56ad66425893c27/src/video_core/page_manager.cpp)
- [GPU-side deferred invalidation implementation](https://github.com/Chreece/shadPS4/blob/dafe3c58057e9ffaec711dbcf56ad66425893c27/src/video_core/renderer_vulkan/vk_rasterizer.cpp)
- [Audio3D PortAdvance locking branch](https://github.com/Chreece/shadPS4/tree/fix/gow-audio3d-queue-coherency-20261019)
- [GoW observed compute-queue implementation](https://github.com/Chreece/shadPS4/blob/test/gow-audio3d-queue-isolated-build-20261019/src/video_core/amdgpu/liverpool.cpp)
- [Official shadPS4 main — compute-queue implementation](https://github.com/shadps4-emu/shadPS4/blob/main/src/video_core/amdgpu/liverpool.cpp)
- [Unpromoted SRT wave-uniform experiment](https://github.com/Chreece/shadPS4/tree/test/gow-srt-wave-uniform-inline-20261019)

*Prepared 19 October 2026. This is a technical status and engineering roadmap, not a declaration of complete GoW compatibility.*