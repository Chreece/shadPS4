<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# NGS2 validation — 2026-09-30

## Initial waveform block replacement — 2026-10-01

Base: `c827aa1d5b052c70f938d6d34a4d704f5d21e088`.

The completed user trace has 1,431 bounded records and `exit_code=0`. It shows
flag-1 submissions containing finite initial segments, followed by flag-0
submissions containing infinite loops. One observed initial segment skips 75,408
samples and plays 20,064; the appended loop covers all 95,472 samples. Rejecting
flag 1 loses that initial segment. Other queried voices remain playing/empty
(`0x23`) with zero sample progress, while many accepted finite blocks complete
normally. These observations do not identify the exact guest cutscene wait.

This change interprets flag 1 as replacement of the queued blocks and flag 0 as
append. It preserves skipped initial segments and following loop blocks, validates
all payloads and limits, and rolls back an invalid replacement together with the
rest of its control batch. A replacement drops pending exit-loop requests for the
discarded queue. Current run state and cumulative counters are preserved. The
replacement semantics are an explicit HLE inference; no public native definition
of the flag was established by the preceding source/PR audit.

Six synthetic cases cover flag-1 mono ATRAC9 at 24 kHz through eight-channel
48 kHz output, finite skipped prefixes followed by infinite loops and loop exit,
invalid replacement rollback, replacement of a full queue, callback replacement
without discarding a grain, and pending exit-loop isolation. The first five cases
failed on the parent implementation before the change. Original synthetic audio
is used; game payloads are not copied into tests or repository documentation.

- GCC 13.3 Release: 128 unique cases; all nine CTest invocations passed.
- GCC 13.3 ASan/UBSan: all 30 public audio cases passed with and without
  diagnostics. Leak checking remains disabled in this environment.

The unknown-flag diagnostic fixtures now use flag 2. Non-identity filters remain
unsupported (the trace supplies direct type 0x20, location 0, channel mask 0).
Playback completion callbacks, filter DSP and continuous resampling between
separate blocks are still unfinished. No in-game cutscene, dialogue crackle or
physical speaker-layout fix is claimed. Local Docker compilation and the isolated
game test remain the deployment path; cross-platform CI is not requested.

## Cutscene wait diagnostic — 2026-10-01

Base: `9e95c1727d287514d0e85aef9f863e0b293f6e5b`.

Both new user traces have no recorded ATRAC9 setup or render errors, but the user
still observes stalled cutscenes with moving elements. Nonzero block flags and
non-identity direct filters are rejected. Some flag-1 requests are immediately
followed by successful block submissions; rejection alone does not establish the
cause of the cutscene wait. One capture ends with `exit_code=0`; the other has no
exit footer. No crash, complete audio fix, or cutscene success is inferred.

The upstream PR audit found no applicable open NGS2 fix among 63 open PRs.
[NGS2 #3891](https://github.com/shadps4-emu/shadPS4/pull/3891) was closed without
merging and does not implement ATRAC9 or filters. Audio/video sync #4761, audio
stop deadlock #4859 and ATRAC9 overread #4733 are already in this branch's ancestry;
the fixed LibAtrac9 submodule is in use. The public implementations inspected do
not establish the semantics of the nonzero waveform-block flag.

This revision changes diagnostics only, leaving unsupported controls rejected.
It records bounded block metadata (including null data, skip/repeat counts),
filter channel masks, per-voice completion counters and state-query results.
`control-stage` is explicitly provisional; `voice-commit` follows successful
publication of a batch and includes its last event or UINT32_MAX if none.
No audio payloads, guest addresses or user-data pointer values are logged.
State queries and block completions are throttled per voice, using fixed storage
for up to 64 keys per diagnostic bucket and the existing 2,048-line process cap.
Absence of a sampled event is not proof that the event did not occur.

Two new cases exercise invalid/unmapped/oversized block descriptions with tracing
enabled, preservation of a playing voice, failed-batch pause rollback and repeated
queries without advancement. The diagnostic CTest also verifies emitted block,
event, completion and state records rather than merely checking exit status.

- GCC 13.3 Release: all nine CTest invocations passed (122 unique cases).
- GCC 13.3 ASan/UBSan: all 24 public audio cases passed with and without
  diagnostics; leak detection remains disabled for the environment limitation
  documented below.

The next isolated test should leave the first stalled cutscene running for about
15 seconds, then close the emulator and collect the finished diagnostic. This is
an evidence-gathering build, not a claimed cutscene fix. Full Linux compilation is
performed by the local Docker helper; no cross-platform CI is requested.

## Local ATRAC9 config correction — 2026-10-01

Base: `66a2ef4d25e2029628dad50f5ec9a308ef072c47`.

The next uploaded diagnostic contains no recorded render/matrix failures, while
rendering continues at 48 kHz with eight output channels. It does contain sampler
setup failures for literal `configData=0xfe4005f0`, mono 24 kHz, followed by
UNINIT_VOICE. This is a valid ATRAC9 configuration whose bytes were reversed by
the public ABI adapter. The user reports brief cutscene audio, a stalled scene
with moving elements, then reaching gameplay. This capture has no exit footer;
no completed shutdown or full cutscene success is inferred from it.

The correction changes the two scalar/byte conversions, including public parser
output. Three new regression cases fail on the parent and pass after correction:
explicit public frame/block queries and parser output words; guest-literal setup,
nonzero ATRAC9 decode and 24-to-48 kHz rendering through an eight-channel buffer;
and malformed/mismatched words leaving outputs and a playing voice unchanged.
The tests use original synthetic encoded audio, with no game payloads.

- GCC 13.3 Release: 120 unique cases in eight executables; all nine CTest
  invocations passed, including the audio suite repeated with diagnostics enabled.
- GCC 13.3 ASan/UBSan: the 22-case public audio suite passed both with and without
  diagnostics. Leak detection remains disabled in this environment.
- Full Linux compilation is performed by the pinned local Docker helper before
  installation. No cross-platform CI or new in-game success is claimed.

Nonzero waveform-block flags and non-identity direct filters remain unsupported.
They are present in the trace and still need separate semantic/DSP work. This
conversion fix does not establish the cause of the cutscene stall or promise
complete dialogue/surround audio.

## Local callback/control correction — 2026-10-01

Base: `59566b916c3ff680616081c9bcde642e70f874a7`.

The completed user diagnostic reports a clean exit (`exit_code=0`), continued
48 kHz/eight-channel rendering, repeated INVALID_OPERATION render failures,
INVALID_NUM_MATRIX_LEVELS, and rejected waveform/filter requests. The user reports
a stalled cutscene with working pause controls. This is evidence of incomplete
in-game behavior, not a confirmed crash or an established cause of the stall.

Five regression scenarios fail against the base and pass after this correction:
callback pause, concurrent volume/gain changes, append before a source is rendered,
setup during a callback, and inactive incomplete routing. The added rack-destruction
case and existing system-destruction/permission checks retain safe abort behavior.
Queues and counters stay synchronized with controls; a fresh setup has independent
progress. Rejected setup/append/filter and active matrix requests now include bounded
numeric diagnostics without guest payloads or raw guest pointers.

- GCC 13.3 Release: all nine CTest invocations passed (19 public audio cases,
  also repeated with diagnostics enabled).
- GCC 13.3 ASan/UBSan: all nine CTest invocations passed. Leak detection remains
  disabled due to the execution environment limitation documented below.
- No new cross-platform CI or full emulator build is claimed. The pinned local
  Docker helper runs focused checks and builds Linux before installing the test core.
- Cutscene progression, dialogue crackles and physical speaker mapping still need
  the user's isolated game test. Unsupported formats/filter modes remain errors.

## Public audio bridge

Base: `c6f958582075e042d007b27f9d5d3f2d71e56fd9`.

| Check | Result |
| --- | --- |
| GCC 13.3 Debug, warnings as errors | 109/109 cases passed in seven executables |
| GCC 13.3 Release / NDEBUG | 109/109 cases passed |
| GCC 13.3 ASan + UBSan, including LibAtrac9 | 109/109 cases passed; local leak detection disabled as explained below |
| Public export registration/lifecycle/voice/waveform C++23 production syntax | Passed |
| clang-format 19 and `git diff --check` | Passed |
| Exact-revision full emulator build | Remote CI gate; see branch Actions |
| In-game audio and physical speaker mapping | Not tested in this environment |

The new 14 public audio cases cover nonzero eight-channel PCM and ATRAC9 through
parse/control/routing/render, sample duration and delay, source counters, pitch,
matrix fanout, two-source mixing, PCM16 saturation, transactional control rollback,
invalid buffers without advancement, stale/cyclic patches and UserFx callbacks.
Three further playback cases cover exit-loop lookahead and pitch/rate changes.
See [BRIDGE.md](BRIDGE.md) for ABI assumptions and unsupported features.

The previous revision's focused run
[36785878847](https://github.com/Chreece/shadPS4/actions/runs/36785878847) passed all
three configurations, including Clang sanitizer/leak checks. Its full run
[36785878916](https://github.com/Chreece/shadPS4/actions/runs/36785878916) passed
all three platform C++ test jobs and the macOS emulator build. Those results do
not substitute for building this newly connected public renderer.

## Decoding and playback milestone

Base: `9c4ecf9d268bb719674cfcb87adc7bd4cdd629af`.

| Check | Result |
| --- | --- |
| GCC 13.3 Debug, warnings as errors for HLE/tests | 92/92 cases passed in six executables |
| GCC 13.3 Release / NDEBUG | 92/92 cases passed |
| GCC 13.3 ASan + UBSan, including the LibAtrac9 C sources | 92/92 cases passed, local leak detection disabled as explained below |
| Production AJM and all nine affected NGS2 stub translation units, C++23 syntax | Passed; existing AJM multi-character constant warnings remain |
| clang-format 19 on changed/new C++ sources, `git diff --check` | Passed |
| Full emulator build for this milestone | Remote CI required; no local full-build claim |
| Guest voice controls, routing and system render | Not connected; no in-game audio validation |

The added cases comprise 13 decoder and 12 playback tests using original nonzero
ATRAC9 packets and PCM samples, including eight distinct channels. The independent
single-coefficient transform check, seek/delay/loop tests and grain-independent
rate conversion tests are described in [PLAYBACK.md](PLAYBACK.md).

Enabling UBSan on the pinned codec exposed a shift by 32 in its initialization.
The shared CMake helper corrects that and signed-left-shift sign extension in a
generated copy of utility.c. Tests now include negative spectral coefficients;
ASan/UBSan remain enabled for the entire codec. The submodule revision is unchanged.

The preceding full workflow [36783382595](https://github.com/Chreece/shadPS4/actions/runs/36783382595)
passed all three platform test jobs, formatting and REUSE, but full compilation
found obsolete `using namespace Libraries::Kernel` directives in NGS2 stubs after
the lifecycle header cleanup. This update removes those unused directives and
syntax-checks all affected translation units. A new full-build result is still
required before claiming a deployable binary.

## Earlier lifecycle milestone

Base: `fad0b3223c2117f53dc5a1119890fe91d55f1ecd`.

| Check | Result |
| --- | --- |
| GCC 13.3 Debug, warnings as errors | 67/67 cases passed in four executables |
| GCC 13.3 Release / NDEBUG | 67/67 cases passed |
| GCC 13.3 ASan + UBSan | 67/67 cases passed, leak detection disabled |
| GitHub Actions: GCC Debug/Release, Clang ASan/UBSan with leak checking | All three jobs passed |
| Existing six `tests/test_ngs2.cpp` GoogleTest cases | 6/6 passed |
| Production `ngs2.cpp`, `ngs2_impl.cpp`, `hle/guest_memory.cpp` C++23 syntax | Passed |
| Exact new MemoryManager method compiled with real production headers/types | Passed |
| `git diff --check` | Passed |
| Full emulator configure/build/link | Not run |
| In-game audio / eight-channel speaker routing | Not run; renderer is not implemented |

The standalone suites contain 23 waveform, 14 registry, 26 lifecycle and four
memory-access cases. All use synthetic metadata or host fixtures, with no game
assets. The lifecycle suite executes the actual public export implementations
with a strict test memory provider. The range algorithm is the same template used
by the production MemoryManager. It does not start a real guest process or pin
memory against concurrent unmapping.

LeakSanitizer could not initialize under this execution environment's tracing
(`/proc/.../task` unavailable / ptrace diagnostic). The sanitizer run was repeated
with `ASAN_OPTIONS=detect_leaks=0 UBSAN_OPTIONS=halt_on_error=1`; ASan and UBSan
remained enabled. This is not a leak-check pass. The focused GitHub Actions workflow
requests leak checking on a normal Ubuntu runner.

The six existing GoogleTest cases were built directly with the repository's
vendored fmt GoogleTest sources, the modified implementation, registry and memory
stub; the full root-CMake test target was not built. The production syntax check
used checked-out repository headers/submodules. The small MemoryManager method
was compiled separately with its real headers to avoid claiming a full renderer
translation-unit or emulator build.

No deployable emulator binary was produced, no host speaker settings were changed,
and no working sparse-queue installation was replaced. Remaining functionality
and compatibility assumptions are listed in [RUNTIME.md](RUNTIME.md).

## Remote CI

[Focused run 36783128151](https://github.com/Chreece/shadPS4/actions/runs/36783128151)
passed all three configurations for implementation commit
`918c1768523515a589748472ba8446eefda6d043`. Unlike the local container, the Clang
sanitizer runner completed with leak detection enabled. The full Build and Release
workflow was still running when this update was recorded; it is not counted as a
full-build pass. Repository lint identified missing SPDX tags in the milestone
Markdown notes and formatting in the earlier foundation/notification include;
this follow-up corrects those without changing runtime behavior.
