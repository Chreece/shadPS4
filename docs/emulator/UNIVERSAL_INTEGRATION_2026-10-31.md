# Universal source integration — 2026-10-31

The candidate is rebased onto upstream `55a97e46955b2ece7c7ff8bddeb043ca57a506c6`
with fork main `bea8a87da2d84ca8b80e206fe0e5e3cde2f46f45`, retaining both
histories and selectively integrating available generic fixes. It is not yet
a verified universal release. No title ID, shader hash, GPU vendor, or driver
version selects any new behavior in this integration.

## Source included

| Change | Source/evidence | Qualification |
| --- | --- | --- |
| Existing main fixes | Main, including PR116 renderer source | Retained through upstream conflicts; patch-only PRs are not counted as production integration |
| Guest CPU topology and affinity | Upstream PR5287, head `9811afcd`; Linux 33/33 homebrew, 10,240 callbacks | PR closed unmerged upstream; Windows/BSD runtime not established |
| SSE4a fallback preserving upper YMM | Upstream PR5315, `954159aa`; 48/48 homebrew, 352 checks | PR closed unmerged upstream; selected source retained |
| Native CPUID/generated instructions | Native-only donor branch ending `a82db596` | No DynamoRIO dependency; guest native runtime still requires combined retest |
| HLE entry stack alignment | `52962289` plus misaligned-stack fixture | Only x86 GCC/Clang ABI attribute; guest behavior unchanged |
| Descriptor allocation | `d9a5c4ca`, `ff916b8d`, `f3051fa0` | Exact-set fallback passed six real allocations; subsequent texture crash was not gameplay success |
| SRT integer evaluation | Selected `54a550a3`/`ad985c2e` functionality | Correct first-set-bit zero sentinel and remove duplicate immediate shift; GPU-dependent Phi remains unsupported |
| Startup UI and callback signals | `45f158f3`, `afdd2d02` | Source integrated; focused UI tests pass |
| Contiguous guest mapping | PR81 `b447795e` | Only adjacent mapped VMAs may extend a buffer; fixture passes |
| Sparse host-size correction | PR109 immutable shared patch | Port retains upstream cache changes; prior native legal-size result preserved; alias GPU lifecycle still unproven |
| Depth image extent growth | New component-wise maximum | Mip count and layer count grow independently; old code fails crossed-extent regression |
| User-data directory startup | New recursive directory creation | Actual native CLI aborted with missing parents; corrected binary passes fresh, existing, and conflicting path cases |

Shader binary cache version is 12, newer than both source parents. Old compiled
shader cache entries must not be treated as equivalent to these emitters.

## Validation and promotion

The initial focused run passes 31 CTest cases in nine standalone suites,
seven renderer source-contract cases, 10,418 actual Xbyak SRT checks,
the descriptor pool fixture, the misaligned-stack fixture, and contiguous-VMA
checks. Sparse tests execute the extracted production allocation methods over
288 ranges and seven scenarios, both with recorded operations and real Vulkan
headers. These are CPU tests, not evidence of GPU execution.

The arithmetic and extent regressions were tested against the
old behavior: each fails before its fix and passes afterward. For the October 31 source (before the November 1 upstream refresh), full native
compilation and link were verified on Linux with GCC 14. All 442 emulator
translation units compiled. The generated link command needed the isolated SDK
library search path for UUID and udev; rerunning that command succeeded. The
actual executable then exposed a missing-parent user-directory startup abort.
After the one-line production correction, recompilation and linking, it passes
three real CLI startup cases. No game or GPU was launched.

Details, failed attempts, exact source-tree and executable hashes are in
`reviews/20261031-universal-native-link-startup.json` and `TRIALS.jsonl`.
The focused CPU runner also passes with GCC 14; its output is retained in
`reviews/20261031-universal-cpu-tests.txt`. A clean CMake run on the user's host
is still part of the pinned runner. No cross-platform or graphics pass is claimed.

Retain the candidate until native build and cross-title GPU evidence support
promotion. The prior host-tested fixes remain preserved in their existing PRs.
Do not merge all of PR117: authentic ordered-GDS capture and continuation after
DS execution are not established. The latest 95-second GoT observer completed,
but its image is a dark silhouette/katana at 1 FPS without a readable menu.

## Outstanding source and evidence

Some accepted C925 renderer/resource changes exist only in the user's retained
working trees. The available source-rescue archive has three restored files,
not a complete reconstructable source tree. Avoid substituting old whole files
for current upstream code. The build runner captures source-only snapshots of
three exact retained paths so those changes can be ported with provenance.

`PR_SNAPSHOT_2026-10-31.json` preserves the 117-PR audit. The existing queue dated
October 26 remains a historical snapshot, not a current promotion manifest.
Eighty-nine additional PR117 trial records have been appended without erasing
previous failures. Raw private game shader binaries are not in this repository.

## Reproduction

Run `tests/run_universal_cpu_regressions.sh /absolute/test/output` in this tree.
Use GCC 14 or a compatible C++23 compiler for the full upstream app. The host
runner `scripts/shadps4-universal-upstream-build.sh <immutable-commit>` verifies
the toolchain and upstream ancestry, builds an isolated checkout, appends the
journal even after failure, and packages its logs. It does not install or launch
the candidate. Upstream `ENABLE_TESTS=ON` replaces the app target, so the runner
uses separate focused test trees and `ENABLE_TESTS=OFF` for the app.

## November 1 upstream refresh

The host run `universal-upstream-20261101T111638Z-2463058` passed GCC 14
preflight but stopped at upstream freshness (exit 3). Its original `FAILED`
result is retained in the journal; classification is blocked by upstream
advancement. It did not capture retained source, compile, or launch a game.

Rebased the integration onto upstream PR5592 (`55a97e46`), retaining the previous
integration resolutions and startup correction. The only production delta from
`9d621138` is upstream's three-file filesystem change: read-only access errors,
directory inodes, and host owner permissions for chmod/fchmod.

Retained-source capture now precedes the upstream fetch and ancestry gate.
An upstream advance reports `BLOCKED_UPSTREAM_ADVANCED` with exit 3 and a
reintegration decision. Two temporary runner fixtures verify that source is
archived and journaled before either an upstream advance or fetch failure,
without starting a build. These are orchestration checks, not emulator tests.
Bash syntax and whitespace checks pass. The refreshed source still needs the
host native build, startup checks, and combined GPU validation before promotion.
See `reviews/20261101-universal-upstream-refresh.json` for provenance and results.

## November 1 host build accepted

Host run `universal-upstream-20261101T112712Z-2616640` completed the stock
CMake/Ninja native build, all nine CPU suites (31 CTest cases), the SRT and sparse
checks, and all three real startup checks at source `ed0fe5be055ee712756351479ac5411366e5385b`.
Verified ELF SHA256: `2866fdd415a3e3a82c9fa216530acb26fd4a4f4ef388e0aeec2b7f3ce8abc548`.
No game was launched. All 2,863 retained-source file hashes match their manifests.
Their differences are indexed in `reviews/20261101-host-native-pass.json`;
experimental renderer code is not promoted solely because its source was captured.

`tools/development/run_universal_verified_graphics.py` reuses this exact host ELF
without a rebuild or installation, copies a private profile, omits title-specific
configs/patches/cheats/caches, checks 8 GiB free VRAM, and runs the existing bounded
native observer without automatic input. If necessary it unloads currently loaded
local Ollama models; model files and configuration remain unchanged, and models
stay unloaded until next use. Screenshots and process/log evidence are archived;
both the universal and GoT journals receive a result even on preflight failure.
Four orchestration fixtures pass locally. Real graphics acceptance remains pending.
