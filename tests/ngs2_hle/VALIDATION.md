<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Lifecycle milestone validation — 2026-09-30

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
