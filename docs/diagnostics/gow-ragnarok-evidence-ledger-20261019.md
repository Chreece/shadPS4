# God of War Ragnarök (CUSA34384) — Evidence Ledger

**Date:** 2026-10-19. **Repository:** `Chreece/shadPS4`. **Status:** archived evidence; no source, executable, or game-save promotion authorized by this report.

## Scope, reproducibility, and the meaning of “every line”

- **12 available gameplay archives; 16 full-console game-mode recordings; 248,554 emitted console lines read and classified**, plus structured summaries, automation records, build logs, source checks, and the newly uploaded SRT wave-uniform archive. Reproducible parser: `gow_full_log_audit_20261019.py`. The JSON line-by-line category, counts, and representative source-line indexes: `gow_full_line_audit_20261019.json`.
- The emulator **suppressed 331,832 duplicate events** (recorded as 2,000+ `Skipped N duplicate messages..` markers in various logs); these underlying messages are **not** present and cannot be reconstructed. The audit reads every **recorded** line, not every event that occurred.
- This is **not all project history**: some earlier 2026-10-13 through 2026-10-17 experiments exist as GitHub branches or tests but their complete runtime archives are **not in the available evidence set**. A branch, a successful build, or an anecdotal “breakthrough” is not proof of better gameplay.
- The tested `SHADPS4_RUNTIME_LOG_FILTER` is `*:warn Lib.AudioOut:info Lib.Audio3d:info`. **Render.Recompiler info-level messages are filtered out**, including `SrtInlineReadConstResult`. Thus zero such records **cannot establish that inline-read logic never executed**. This omission invalidates previous claims of definitively inactive code. A separate instrumented counter or warn-level event would be required, but a new run is NOT authorized solely to get another counter.
- Cross-run FPS, total shader warning counts and audio availability are **not scene-matched**; the automation explicitly sets `scene_equivalence_proven=false`, and its cutscene indication is only an audio-cadence **candidate**, not definitive visual proof. The 2026-10-19 05:26 dense A run was **manually intervened** and is not valid as a clean A/B comparison.

## Practical outcome of the most recent experiment (16:20)

- Experiment: original renderer + UFFD, Audio3D PortAdvance mutex, plus conservative scalar-read fallback ported from GoT. Source SRT changed and linked successfully; build, navigation and save/binary restoration passed.
- SRT walker failures **45**, unflattened resource warnings **64**, ignored compute `SetQueueReg` on queue **4**, register offset **`0xb`**, **2,637**, oversized buffer clamps **632**, shader DMA enabled errors **90**. Old working Audio3D capture (10:32): **44**, **63**, **2,490**, **634**, respectively; differences are minor/unmatched scenes.
- Late-game FPS **18.48** vs old **18.20**. Late Audio3D buffer availability **47.77%** vs old **47.02%**. Both runs drop into the **35–40% ready range** during worst late windows. **No demonstrated improvement in playable graphics or stuttering.**
- Audio SDL calls remain short compared with the **21.333 ms** period. `AUDIO_GUEST_SUBMIT` gaps exceed **100 ms**; there is no direct evidence SDL is holding up audio. Latest UFFD log: zero audio faults slower than 21 ms; huge aggregate CPU-fault and buffer-sync traffic remains, but neither alone proves a guest scheduling dependency.
- The candidate is **experimental, not promoted**. The protected binary and SRT sources retained their original hashes, and Ollama was untouched/restored as reported.

## Results for all available runtime captures

`FPS` and `Audio ready` refer to each run’s saved *late* summary, not a verified common scene. `SRT info` is not observable under current log filter.

| Archived run | Mode | Auto navigation finished | Late FPS | Late audio ready | SRT failure messages | SRT info messages | Ignored queue packets |
|---|---|---:|---:|---:|---:|---:|---:|
| gow-audio-onset-ab-20261019-052606-analyzed | B | True | 18.82 | 47.71% | 45 | 0 | 3844 |
| gow-audio-onset-ab-20261019-052606-analyzed | A | False | 18.95 | 40.91% | 42 | 0 | 5507 |
| gow-audio3d-lock-20261019-103258 | A | True | 18.2 | 47.02% | 44 | 0 | 2490 |
| gow-auto-scene-20261018-205053 | A | not recorded | 18.65 | 48.24% | 45 | 0 | 5630 |
| gow-auto-scene-20261018-205053 | B | not recorded | 20.27 | 44.26% | 44 | 0 | 6948 |
| gow-auto-scene-20261018-214744 | A | False | 17.7 | 99.04% | 38 | 0 | 6464 |
| gow-auto-scene-20261018-214744 | B | False | 17.38 | 98.46% | 38 | 0 | 6382 |
| gow-continue-live-calibration-20261019-06524 | A | not recorded | — | — | — | 0 | 2209 |
| gow-nav-calibration-20261018-224752 | A | True | 18.82 | 45.32% | 45 | 0 | 2633 |
| gow-sparse-upload-20261018-201012 | B | not recorded | 17.15 | 69.1% | 41 | 0 | 2880 |
| gow-sparse-upload-20261018-201012 | A | not recorded | 20.3 | 36.27% | 59 | 0 | 2518 |
| gow-srt-dependency-fix-20261019-145304 | A | True | 18.07 | 47.23% | 1520 | 0 | 2554 |
| gow-srt-flatbuf-20261019-142308 | A | True | 18.52 | 45.37% | 170 | 0 | 2620 |
| gow-srt-wave-uniform-20261019-162036 | A | True | 18.48 | 47.77% | 45 | 0 | 2637 |
| gow-ssh-safe-20261019-073226-1207417 | A | True | 18.85 | 42.45% | 45 | 0 | 2575 |
| gow-ssh-safe-20261019-085204-2331337 | A | True | 18.47 | 46.49% | 45 | 0 | 2657 |

### What works and should remain protected

- **Known functioning GoW graphics checkpoint and deferred Linux UFFD renderer behavior:** current and prior instrumented runs complete with **zero reported Vulkan device losses**. Retain all known working shader/graphics/CPU patches; do not overwrite or rebase the sole protected executable to test a hypothesis.
- **Accurate fixed-sequence Continue and bounded X11 forward/right/left steering** after the 2026-10-19 menu detector repair. Run success means automatic keyboard input and logging only; exact cutscene and scene matching remain unconfirmed.
- **Detached SSH-independent game execution, owned-process shutdown and Ollama conditional VRAM reload** after 2026-10-19 HUP cleanup. Protect original saves through cloned XDG profiles, preserve Sunshine and SSH.
- **Audio3D `sceAudio3dPortAdvance` mutex correction:** source-level data race protection and compile/cleanup validated; **no evidence it fixes sound**. Preserve as a separate correctness patch, not as a gameplay improvement.
- **SRT constant-left/dynamic-right shift is already correctly implemented in the protected source** through `SrtCodegen::EmitConstantLeftShiftByDynamicCount`. Reapplying its inline alternative creates a redundant build and risks losing the older verified implementation.
- **Sparse upload native equivalence test** passed 200,002 cases; tested optimization does not establish a gameplay benefit. Its default-off behavior and existing page-watch semantics should remain unchanged.

### Experimental ideas rejected or on hold (avoid loops)

| Hypothesis / variant | Evidence | Status / repeat policy |
|---|---|---|
| SDL outer pacing, AudioOut backend patch variants | Short backend calls / zero reported errors, audio source continues to starve | **No demonstrated in-game fix.** No new pure pacing build |
| Linux host-priority boost for `AK::AudioOutThread` | Runnable host wait low during starvation, sleeping futex predominant; priority implementation gap not causal proof | **Reject** as default fix; do not force scheduler class |
| Direct UFFD audio page fault source of stutter | 0 audio UFFD faults >21ms in latest trace | **Reject** direct audio-fault explanation; aggregate CPU load remains open |
| GPU dirty-word sparse CPU scanning | 200,002 native checks pass, scene-unmatched A/B gives inconsistent FPS/audio | **Unproven practical benefit; default off** |
| Audio idle-output-clock hypothesis | GoW executable has no identified direct import of `sceAudioOutGetLastOutputTime` | **Reject** unless new evidence |
| Audio3D queue-mutex as sound fix | 10:32 run 47.02% availability / 18.20 FPS, still stuttering | **Correctness fix only**, not an audio cure |
| Deferred SRT producer retry | 14:53 build: **0 recoveries**, 1,520 *logged* errors (multiple retries amplify logging), 18.07 FPS, 47.23% audio | **Reject retry implementation**; do not promote |
| Wave-uniform SRT inline scalar guest reads | 16:20 build: SRT 45 vs ~44 baseline, no practical improvement; `LOG_INFO` marker filtered so activation not proven | **No practical win; do not repeat blindly** |
| SRT arithmetic shift reimplementation | Already correct in protected source using verified helper | **Redundant; do not rebuild** |
| Automated dense/sparse navigation A/B (05:26, 21:47 etc.) | A required human control; earlier runs stuck at menu/right wall; not same scene | **Not valid efficacy measurements**; do not cite as performance gains |
| Shader SRT logging increases | 44 → 170 → 1,520 in successive diagnostics/retry | **Different instrumented log behavior**; cannot treat raw count as regressions or fixes without comparing actual source and emitted shaders |

### What is still genuinely unresolved

- **Practical milestone remains unmet**: the same segment in GoW Ragnarök must render correctly and play without audio stuttering, confirmed by the user; current logs alone do not establish that.
- `SetQueueReg` queue 4 / offset `0xb`: a **high-volume explicitly unhandled PM4 command** (roughly 2,500–2,650 recorded warnings per completed late-game run). Its hardware semantics and actual runtime impact have **not** been established; no guessed write semantics or fake register values.
- ~44–45 logged SRT walker failures and ~63–64 unflattened-source warnings persist; the many SRT candidates have not changed the user-visible game. We need a proven connection from a specific shader's invalid descriptor to a **missing rendered resource**, not another generic SRT traversal change.
- `Enabling DMA for shader` (~90) and enormous buffer clamp values (~630 occurrences) recur. They might be relevant to invalid resource descriptors, but no causal chain is proven.
- Heavy memory tracking: latest 135.84-s capture has ~1.21 million `GOW_BIG_BUFFER_SYNC` calls, ~65.9 GB real upload, 9.35 s cumulative dirty scanning, 14.0 s cumulative sync CPU time. These are measured workloads, not automatically bugs. UFFD logs include nearly 1 million CPU faults in some 5-s windows; further interpretation needs thread/workload attribution.
- Wwise audio production has >100-ms guest-submit gaps, while backend calls are short and host AudioOut runnable wait is low. We have the symptom; **the awaited dependency has not been identified**.

## Hard stop criteria before another user gameplay build

1. A specific **source semantic defect** with a reproducible native or unit regression test that fails before the change and passes after it. The changed code must be demonstrably used in the failing GoW scenario. Passing compilation alone is not progress.
2. A distinct expected **practical user-visible effect** stated beforehand (missing material appears; cutscene audio continuity; measurable deterministic state). Do not substitute warnings/FPS if scenes differ.
3. Do not launch another one-off experiment whose hypothesis matches a rejected row above. Revisit only on **new, named evidence** from a different source.
4. Record exact branch/commit, protected binary SHA, source SHA, flags including **logging filter**, build preflight, all test outputs, input sequence, manual intervention, explicit scene-equivalence flag, and restoration evidence.
5. Require warnings/instrumentation that are **actually visible under the selected log filter**. Preserve suppressed-message counts; do not equate no `LOG_INFO` rows with code not running.
6. A single failed or neutral targeted game test means the candidate stays isolated. No automatic promotion, no repeated A/B until the new evidence proves the previous cause/assumption changed.
7. All project script paths `~/projects/shadps4/scripts`, logs `~/projects/shadps4/logs`, never terminate the SSH session.

## Next recommended action (non-gameplay)

**Freeze the wave-uniform candidate.** First establish the meaning and *effect on queue state* of `SetQueueReg` queue 4, offset `0xb` using the precise PM4 packet and AMD GCN/GFX hardware references, and test the implementation in a small native packet regression **before** any game build. Alternatively, connect one specific unresolved SRT shader hash to a missing rendered object; without that link, generic SRT work has not delivered any benefit. Neither route authorizes a new gameplay build now.

## Evidence provenance

Original source archives: `gow-audio-onset-ab-20261019-052606-analyzed.tar.gz`, `gow-auto-scene-20261018-205053.tar.gz`, `gow-auto-scene-20261018-214744.tar.gz`, `gow-nav-calibration-20261018-224752.tar.gz`, `gow-sparse-upload-20261018-201012.tar.gz`, `gow-ssh-safe-20261019-073226-1207417.tar.gz`, `gow-ssh-safe-20261019-085204-2331337.tar.gz`, `gow-audio3d-lock-20261019-103258.tar.gz`, `gow-srt-flatbuf-20261019-142308.tar.gz`, `gow-srt-dependency-fix-20261019-145304.tar.gz`, `gow-srt-wave-uniform-20261019-162036.tar.gz`, and the separate SSH-interrupted calibration. Full code/data archive audit is reproducible with `gow_full_log_audit_20261019.py` and `gow_full_line_audit_20261019.json`.