<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Consolidated local default — 2026-10-02

This integration removes unsuccessful rendering experiments from the fork's
combined main. It does not fix the remaining disappearing geometry. It keeps
user-confirmed behavior and specific correctness bugs supported by code or
validation evidence; those two evidence levels must not be confused.

| Retained change | Evidence and limit |
| --- | --- |
| Independent NGS2 audio/ATRAC9 streaming, `c3421308` | User confirmed cutscene sound and progress after the streaming correction. Later intermittent stalls/crackles remain possible; audio is experimental. |
| Sparse queue/BDA allocation, `22023ff9` integration | Existing tested sparse-capable queue selection and device-address allocation are retained. Audio remains independently branched. |
| Missing-user startup guard, `e2251423` | Crash was traced to missing user lookup; user confirmed successful startup after restoration. |
| Measured occlusion queries, `66db3a63`, with count-control correction `77155ef6` | User confirmed outdoor light stopped appearing indoors. The initial implementation increased flicker; retain its identified register/control correction, not the erroneous toggle behavior. Disappearing geometry remains unresolved. |
| Depth transfer/transition correction, `bc479ce3` | Actual validation report identified incompatible D32/D32S8 image copies and unordered dependent transitions. Focused regression checks pass. This is not a confirmed visual-glitch cure. |
| Presentation synchronization, `287e0d09` | Fixes insufficient wait stages for producer semaphores and missing fragment-read coverage. This is not a confirmed visual-glitch cure. |
| Speaker matrix routing, `a98e113f` | Packed/asymmetric matrix regression reproduces wrong channel routing before the fix; checks pass afterward. No confirmed physical-subwoofer result or bass boost is claimed. |
| Home/PS quit confirmation, `ac36a0ed` reapplied on `fix/gamepad-home-exit` | Restores the previously requested controller behavior omitted from the earlier clean-audio main integration. It opens the existing host confirmation, not a PS4 system menu. |

| Removed candidate | Reason |
| --- | --- |
| Texture containment, `fc8945df` | User still reported the same light and geometry symptoms. No established local benefit. |
| Raw buffer/image synchronization, `aec526ed` | Glitches persisted; the collected run did not exercise its added synchronization event. |
| Tiled mip integration, `261f3392` | The added path was exercised but the user's glitches persisted. Upstream correctness claims are not a demonstrated benefit in this local comparison. |
| On-demand audio trace, `d72840e9` | Diagnostic experiment, not a playback fix. |
| Runtime serialization/validation experiments | No persistent `syncshaders` or validation override is introduced by the default launcher. The controlled `syncshaders` test still glitched. |

The older commits and issue branches remain in Git history for review. Main is
changed by forward commits, without rewriting history. Existing upstream changes
are preserved. The normal launcher disables the optional audio/graphics diagnostic
environment switches; ordinary emulator logging remains available.

## Local installation

`scripts/install_local_default.py --revision <40-character-main-commit>` builds
only Linux x86-64 in Docker on the user's computer. The pinned main revision must
match the fetched branch. It reuses the existing compiler/cache workspace and
includes `clang-tools-19` and the explicit dependency-scanner path.

Run as the normal desktop user after closing both the game and ES-DE. The script
does not request sudo, restart services, repair displays, or change Sunshine.
It runs the focused audio, startup, occlusion, image-transfer and optional-trace
checks, compiles the full emulator, then verifies dependencies and CLI startup.
A compile failure leaves the working installation and launch entries unchanged.

After success, `~/Applications/shadps4/shadps4` selects the new main core and the
existing guarded `~/.local/bin/shadps4-esde` launches ordinary CUSA game entries.
It preserves the installed single-instance/display helper and uses the same home
working directory as the observed successful game sessions. Configuration,
saves, game files, audio/eight-channel settings and other launchers are untouched.

The cleanup removes recognized test `.ps4` entries and their gamelist records,
keeping or creating an ordinary entry for each affected game. Old executable
payloads under `Applications/shadps4/releases` and their known diagnostic helpers
are removed. Save/configuration directories, libraries and unknown files remain.
One previous core is copied outside the active releases as a rollback; the printed
RESTORE command selects it with ordinary entries. Old archives/logs and the Docker
build cache are not additional active emulator installations and are retained.

Installer fixtures cover a failed compile, failed switch rollback, argument handling,
normal-entry preservation, data/guard preservation, cleanup, external symlink
protection, and a usable previous-core restore. CPU-focused checks do not establish
game correctness; the complete cleaned combination still needs the local game run.

The first installation stopped before the build on a legacy
`Red Dead Redemption [shadPS4 0.10.0].ps4` entry. Entry classification now handles
version labels and the older `CUSAxxxxx|selector` stubs, retiring every recognized
selector without executing it. Version-only games receive an ordinary entry;
region annotations survive, and colliding titles cannot overwrite another game.
Unexpected contents still stop preflight, with all unrecognized entries reported
together. This installer-only correction does not change emulator code.

AI assistance: Codex prepared and reviewed the cleanup, controller integration,
installer and focused regression fixtures. No upstream PR or multi-platform CI
build is requested by this integration.
