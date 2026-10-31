# Universal source integration — 2026-10-31

The candidate merges upstream `860d73b7e99559106a3ffa3d3d69f0f9237e115e`
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

Shader binary cache version is 12, newer than both source parents. Old compiled
shader cache entries must not be treated as equivalent to these emitters.

## Validation and promotion

The initial focused run passes 31 CTest cases in nine standalone suites,
seven renderer source-contract cases, 10,418 actual Xbyak SRT checks,
the descriptor pool fixture, the misaligned-stack fixture, and contiguous-VMA
checks. Sparse tests execute the extracted production allocation methods over
288 ranges and seven scenarios, both with recorded operations and real Vulkan
headers. These are CPU tests, not evidence of GPU execution.

Both newly identified arithmetic/extent regressions were tested against the
old behavior: each fails before its fix and passes afterward. The full native
build is tracked separately in `reviews/20261031-universal-upstream-integration.json`
and `TRIALS.jsonl`; an interrupted or unsuccessful build is not a pass.

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
