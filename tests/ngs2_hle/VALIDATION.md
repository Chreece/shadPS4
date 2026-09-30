<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# NGS2 validation — 2026-09-30

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
