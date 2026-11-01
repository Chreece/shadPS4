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

Shader binary cache version is 14, newer than both source parents. Old compiled
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

## Graphics launch preflight correction

Run `universal-graphics-20261101T115426Z-3053374` never launched the emulator:
free VRAM was 200,368,128 bytes and the local Ollama API reset the connection.
The runner now bypasses proxy settings for loopback HTTP and falls back to
`ollama ps` / `ollama stop` inside identified running Ollama Docker containers.
It records command outcomes and retains the 8 GiB launch requirement. No sudo,
container/service restart, or emulator production change is involved.
Four Docker CLI subprocess fixtures and one actual loopback/proxy check pass;
the four earlier orchestration checks still pass. Host retry and graphics
acceptance remain pending. See `reviews/20261101-graphics-ollama-fallback.json`.

## November 1 graphics failure and guarded lane-read candidate

Run `universal-graphics-20261101T115754Z-3103856` successfully unloaded the
Ollama model and launched with 20,614,766,592 bytes free VRAM. The user reports
black screen with audio; screenshots confirm missing graphics. The driver
reported GPU context loss/soft recovery. Exit 0 is not graphics success.
The host log rejected 408 ReadLane, 4 ReadFirstLane, 72 Phi and 29 GetAttributeU32
offset expressions. The causal contribution of each rejected shader is not yet
established.

A narrow port from retained source now supports lane reads only when their
source is proven host-uniform, with cycle/depth/work guards. GPU-varying input
and unresolved Phi remain unsupported; no lane-zero substitution is introduced.
Shader cache version advances to 13. The extracted production JIT passes 2,825
checks; the old code fails the first case. Existing 10,418 SRT checks still pass.
Full native compilation and visual acceptance of this new source remain pending.

The observer now stops/reports GPU context loss even when the process exits 0.
The graphics runner accepts only a matching successful native-build record and
ELF hash for a new pin, records the actual title in its journal, and captures
up to 48 rejected-shader IR text files / 20 MiB. Shader dumping is enabled in
isolated diagnostic profiles; this is not an FPS benchmark. The two-game runner
builds first, tests GoW then GoT with no input, and stops the sequence on GPU loss.
It packages build and game archives into one `UPLOAD_THIS` archive.
See `reviews/20261101-uniform-lane-srt.json` for evidence and validation scope.


## November 1 GPU-dependent constant reads and capture recovery

The `ef5fd679` host build passed the complete native build, existing CPU suites
and startup checks. The GoT retry eliminated the 408 ReadLane and four
ReadFirstLane rejections, but still lost its GPU context at 52.612 seconds.
Phi (72) and GetAttributeU32 (29) rejections remain. GoW reached a loading screen
at 110 seconds; a post-SIGKILL wait timeout prevented its console summary and
native result from being archived. Its original `PREFLIGHT_FAILED` classification
is preserved in the journal and explicitly corrected in the review.

Five final GoT IR dumps retain 39 live unflattened ReadConst operations. The old
emitter substitutes flattened slot zero when global DMA is disabled. The candidate
now uses guest-memory reads when required by shader semantics, with a capability
check, full-width page-table bounds and atomic fault-bit updates. It also clears
the fault bitmap, tracks the involved buffers through normal renderer barriers,
bounds CPU fault readback, preserves overflowed fault bits and handles noncoherent
host readback. Cache version 14 forces new shader compilation. No title, shader
hash, GPU vendor or driver version controls these changes.

Extracted production routing and metadata pass eight cases; the prior emitter
fails the default-config dynamic-read case. Real Sirit-generated modules and the
compiled fault-processing shader pass Vulkan 1.3 SPIR-V validation. A CPU
interpreter executes 262 bounds/address/fault-bit checks on emitted instructions.
The extracted fault shader drains 3,072 unique pages over four rounds under
ASan/UBSan. These checks are not GPU execution. Full native compilation and
hardware validation of this changed source remain pending.

The observer now saves its result and console even if the owned process does not
confirm exit after SIGKILL. The batch stops on that condition, GPU loss or missing
evidence. A launched failure is classified correctly, and the next runner can
recover old missing console summaries read-only. Two real child-process cases,
one injected cleanup timeout, five orchestration cases, two build-record cases,
four batch cases and one read-only recovery check pass.

This still relies on deferred guest-memory fault handling, without exact shader
replay. BDA-free dynamic reads, GPU-dependent descriptor discovery and ordered-GDS
continuation remain open. Both game captures failed graphics acceptance; main is
not promoted. See `reviews/20261101-dynamic-srt-and-capture-recovery.json` for
immutable capture hashes, IR inventory, original records and validation limits.


## November 1 sparse fixture interface correction

Host run `universal-upstream-20261101T125736Z-4032847` at `515a50dd`
passed all 31 CTest cases, seven renderer contracts and 10,418 SRT JIT checks.
It then stopped because the existing sparse extraction fixture still required
`SynchronizeDmaBuffers()` after production gained a read callback. The previous
validation missed this coupled fixture. Native configure/build and both game
launches were not reached.

The existing fixture now uses the callback interface and checks one callback
per uploaded chunk, including its original owner, byte offset and size. All
288 ranges and seven scenarios pass in both recorder and real Vulkan-header
modes. Temporary negative controls reject missing callbacks and incorrect byte
offsets. The remaining descriptor, contiguous-VMA and HLE ABI checks also pass
locally with GCC 13. No emulator production code changed in this correction;
the native build and graphics tests remain pending on the host. The original
failed record and archive hashes are retained in
`reviews/20261101-sparse-dma-fixture-sync.json` and the universal journal.


## November 1 fault residency and indirect-command synchronization

The `db69c107` host build passed the full native build, existing CPU suites and
startup checks. GoW then exited at 12.833 seconds: shader compilation reports
unsupported `DS_ORDERED_COUNT` in `0xdbaa6ae4`, followed by translation failure.
GoT lost its GPU context at 42.378 seconds. Neither capture has a screenshot or
graphics acceptance. The old GoW console was recovered read-only; its loading
screen and cleanup-failure record remain unchanged.

The complete GoT console records 253 guest-memory faults, including 251 on one
16 KiB page. Fault recovery used default `ObtainBuffer` flags, allowing a small
clean read to return temporary streaming storage without making the guest page
resident or populating its BDA mapping. Only a later larger request reached
residency. Fault recovery now requests `IgnoreStreamBuffer`; ordinary small
buffer reads retain their fast path. This fixes a confirmed residency defect,
but its contribution to GPU context loss still needs a host retest.

The prior PR88 indirect-read correction is also ported: indirect argument and
count reads enter read-range tracking and use `DRAW_INDIRECT` for both draw and
dispatch. The current renderer already queues these buffers, so the shared
`ResetBindings` path applies the correction without duplicate access records.
PR88 previously passed a full native build and six filtered CPU assertions;
that evidence does not establish game acceptance of this new integration.

Temporary checks execute the actual fault callback and buffer-selection method
for 21 cases, and actual access tracking/reset methods for 26 cases with the
pinned Vulkan headers. The old code fails; independent negative controls catch
the missing stage and read mask. The existing sparse fixture still passes all
288 ranges/seven scenarios in both modes, and seven renderer contracts pass.
These checks use recorded host operations, not GPU execution. The changed full
native build and both game retests remain pending. Shader emission is unchanged
and cache version remains 14.

Ordered-GDS continuation remains unimplemented in this branch; PR117 does not
yet supply a proven complete backend. No title/hash/vendor condition or fake
opcode result is added. Deferred fault replay, BDA-free dynamic reads and
GPU-dependent descriptor discovery also remain open. Main is not promoted.
See `reviews/20261101-fault-residency-indirect-sync.json` for original records,
archive hashes, PR88 provenance, validation scope and the recovery review.


## November 1 native build verified; display preflight blocks both games

Host source `dbf5c545` passed the full GCC14 native build, existing CPU suites and
three native startup checks. The resulting ELF SHA256 is
`b34a9a539b88c5fb0dab9dc1cd597ca98f88e9049dbba1ed030671a2cb51d32d`.
Both subsequent game requests stopped before launch because `xdpyinfo -display
:0` timed out after eight seconds. These runs contain no new evidence about GPU
fault recurrence or graphics acceptance. They do not establish the cause of the
display failure.

Display preflight now saves its bounded output and classifies the failure. On
failure it collects read-only process/display/kernel diagnostics with bounded
commands and stops the remaining batch. It does not request sudo, restart the
display or change services. The original two records, including their incorrect
`stop_remaining_tests=false` flags, remain intact in the journal.

The two-game batch now accepts `SOURCE_PIN existing RUNNER_PIN` to reuse a
verified build with a separately pinned, checksum-verified capture harness.
The source commit, successful native-build record, ELF hash and observer bytes
are still checked. Harness commit and hash are recorded separately. A responsive
display leads to the usual GoW then GoT observations; a failed display check
returns diagnostics without launching either game or rebuilding the emulator.

Ten helper cases, three complete preflight-failure paths, twelve batch cases,
five existing runner cases and two build-record checks pass locally. These use
owned helper processes and mocked X11/game execution; host display recovery and
game acceptance are not claimed. No emulator production code changed. See
`reviews/20261101-display-preflight-existing-build.json` for build evidence,
archive hashes and the exact validation scope. Main remains unchanged.


## November 1 kernel crash explains the blocked display

Archive `gpu-crash-20261101T145748Z` records a graphics-ring soft recovery at
14:43:25 Europe/Berlin, close to the prior GoT observer's GPU-loss stop. That
kernel message does not identify a process owner. At 14:50 the driver reports
4,515 user-page failures and RLC faults, followed by MES/KFD queue failures and
SDMA timeouts. GPU recovery fails with `-62` at 14:51:33. Another reset begins
without a successful completion in the archive. The later fault PASID has no
owner mapping here; do not attribute it to an application from timing alone.

Xorg, Sunshine's capture/session threads and the reset worker are subsequently
blocked in kernel display/DRM paths. Sunshine service logs show repeated stop
attempts leaving its threads alive after SIGKILL. This explains the unresponsive
display; the `dbf5c545` native build occurred afterward and neither of its game
requests launched. No emulator command or shader defect can be isolated from
this system archive, and no runtime recovery is claimed.

The runner now checks for display tasks that remain in D state across two
250 ms-separated reads, preserving process/thread/start-time identity. A match
stops before X11 queries, VRAM telemetry or game work. It saves read-only
diagnostics and performs no service, driver or host restart. A separate filtered
GPU/DRM/kernel journal query retains reset evidence that the previous last-600
unfiltered lines would lose behind audit messages. Permission failures remain
recorded without a sudo prompt.

Eight task/filter cases and one new complete runner case pass; the existing ten
helper, three runner, twelve batch, five orchestration and two build-record
cases also pass. These are local collector checks, not hardware recovery tests.
The native emulator source and verified ELF remain unchanged. Host recovery is
required before the existing-build game retry. Full evidence, attribution limits
and hashes are in `reviews/20261101-kernel-gpu-recovery-failure.json`.
