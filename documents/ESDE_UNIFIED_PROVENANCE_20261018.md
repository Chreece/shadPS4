<!-- SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project -->
<!-- SPDX-License-Identifier: GPL-2.0-or-later -->

# Unified ES-DE shadPS4 provenance and acceptance gate (2026-10-18)

**Status: isolated integration candidate, NOT a compiled or gameplay-validated release.**
GitHub branch: `Pre-release-shadPS4-esde-unified-20261018`.
All source SHA values are immutable; sources are checked before composition.
The user's default binary, ES-DE entries, saves, configuration, Sunshine and SSH
are not changed by the build script. A separate switch/rollback script is provided.

## Required base

- Upstream `shadps4-emu/shadPS4` main `cd3cc8022aef5be7766443f694e79d270544eaf4`
  (queried October 18; refuse if upstream main has moved, rather than claiming an
  unexpected combination was audited).
- Curated fork main `aa5b281c0016d64844e784566ef9dd092655ba8b`.
  Effective code includes the *reverts* of the Oct 2 visibly regressive PR batch
  documented in `documents/REVIEWED_PRS_20261002.md`.
- Gamepad Home/PS quit confirmation and consumed gamepad confirmation/cancellation:
  fork main `src/core/devtools/quit_dialog.h` and `src/core/devtools/layer.cpp`.
  Prior local normal controller exit was reported; new-upstream combined behavior
  needs its own manual test.
- Startup loading GUI PR `Chreece/shadPS4#6`, head
  `0b7ff78483e831db2d424b2351f5d931a56a1555`. Holds loading indication through
  black video frames; focused source tests accompany it.
- Vulkan 16-bit `uniformAndStorageBuffer16BitAccess` PR `Chreece/shadPS4#8`,
  head `047cc7cf992f8cf60fdc4f0adfdcfcfb27bbed7d`; on RADV the feature VUIDs
  disappeared, *not* the unrelated Ghost GPU hang.

## Retained in curated fork main (with boundaries)

- NGS2/ATRAC9 audio streaming, resampling, direct filters and destination-row
  speaker matrix routing: playback/cutscene progress and routing regressions were
  observed/fixed in previous builds; stutters and unreliable game audio remain.
- Sparse-capable GPU queue and buffer-device-address allocation: code/test-supported,
  not proof that RDR is playable or GPU device-loss-free.
- Missing-user startup guard, measured occlusion-query/count semantics,
  D32/D32S8 depth-copy handling, presentation waiting/barriers.
- Kernel unlink error reporting, callback/module lifetime handling, missing
  sanitizer handling. Some fixes have focused tests rather than game acceptance.
- Changes that damaged visual detail in the Oct 2 reviewed batch are explicitly
  rolled back in this tree; **do not cherry-pick their old heads wholesale**.

## Collected and deliberately quarantined — not silently merged

| Source | Evidence | Promotion blocker |
| --- | --- | --- |
| `pr-ready/guest-current-cpu-20261006` / upstream #5287 | 33/33 affinity homebrew and integrated PES gameplay; CPU hotplug revision not retested | Latest upstream thread/memory API reconciliation and game regression |
| `pr-ready/linux-guest-cpu-id-20261007` / upstream #5304 | 57/57 CPU-ID checks; alternate PES candidate reached gameplay | Linux x86-64 only; translated mode and RDPID limits; upstream native rebase #16 subsequently had early SIGSEGV |
| `pr-ready/sse4a-fallbacks-20261008` / upstream #5315 | Standalone/focused correctness | No independently accepted merged latest-main runtime |
| `fix/linux-userfaultfd-deferred-gpu-invalidation-20261016`, fork PR #10 | GoW observed moving gameplay over 10 FPS with corrected UFFD candidate | Later rebase/overlay provenance and early startup regressions; no multi-game acceptance |
| Fork PRs #11-#15 and `fix/general-readonly-stream-64k-20261018` | Targeted correctness/diagnostics and evolving builds | Missing graphics and sound, shader build errors, interrupted source audits |
| `proof/got-main-retained-graphics-20261015`, newer `got-*` experiments | GoT reaches menu with music, some shader evidence improved | White/incomplete menu, GPU hangs and severe host-freeze risk |
| PES WAIT ZPASS playtests (#1-#4) | Packet evidence; day-match progress | Night scene and missing detail not fixed/validated generally |
| Fork PR #17 audio backend health | 10/10 source tests for NULL/muted port semantics | No completed game audio playback verification |
| Old image/texture experiments rejected in `REVIEWED_PRS_20261002.md` | User visual acceptance failed | Must remain excluded |

**A change that compiles or fixes one warning is not automatically a proven
general-purpose gameplay fix.** Preserve all development branches and logs for
future A/B replay; the unified candidate must be reproducible, not an uncontrolled
merge of every interesting PR.

## Build and selection

Build: `bash ~/projects/shadps4/scripts/esde_unified.sh`.
Source, object files, test output and SHA256 are kept under
`~/projects/shadps4/ESDE/unified-<timestamp>/`; log is
`~/projects/shadps4/logs/esde-unified-<timestamp>.log`.
The script fails closed on conflicts, upstream movement, missing symbols/tests,
link problems, missing libraries or CLI-smoke failure.

Only after a passing build and with ES-DE and games closed:
`bash ~/projects/shadps4/scripts/esde_unified_select.sh`.
It preserves the original guarded launcher and backs up the previous core under
`~/projects/shadps4/ESDE/rollback/<timestamp>/`, printing an exact restore
command. The default ES-DE PS4 entries point at the selection through the
existing `~/Applications/shadps4/shadps4` path; no gamelists or ROM entries
are modified.

## Post-build acceptance tests

Launch unrelated games from ordinary ES-DE PS4 entries, verifying startup GUI
during black frames, first nonblack video, shader compilation, controller Home
quit confirmation and that confirm/cancel never leak to gameplay. Exercise
multiple game titles, return to ES-DE, repeat launch, and compare screenshots,
audio, FPS, device loss and GPU/host stability against saved known-good binaries.
Do not promote to release until an exact integrated build and user runtime
acceptance establish these behaviors.
