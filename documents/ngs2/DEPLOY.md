<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Isolated Linux test deployment

This installer selects runtime commit
f00bef80a74e6df0835ea0e9818c71c9d4ddf578 from Build and Release run
[36789001354](https://github.com/Chreece/shadPS4/actions/runs/36789001354).
The deployment scripts are kept on a separate branch so they do not replace the
runtime revision being tested or restart its build.

Run python3 deploy_test.py as the normal desktop user, without sudo, after closing
any running shadPS4 game. It expects the existing shell launcher at
~/.local/bin/shadps4-esde with one final "$CORE" --game invocation.
It recognizes an optional exec/setsid prefix and braced CORE spelling. Other
launcher shapes are rejected before changes, rather than guessed.
The known NGS2_PROBE_DISPATCH_V1 wrapper is also supported. Its complete
structure is checked before replacing only the probe runner invocation with
the pinned core's CUSA36843 launch. Ordinary entries retain the existing fallback.

The installer:

1. Checks the feature-branch revision, exact-revision focused tests, platform C++
   tests, lint, Linux compile/link and executable upload.
2. Waits up to 45 minutes for the Linux binary when necessary, printing a status
   each minute. Pending/failed builds never switch the launcher.
3. Downloads the public artifact through nightly.link, verifies its complete ZIP
   SHA-256 and length against independently fetched GitHub artifact metadata,
   and accepts only one Linux x86-64 ELF named shadps4.
4. Checks shared-library dependencies and runs --help in a temporary portable
   profile. It does not open a game, GPU window or the existing profile.
5. Stages the executable under ~/Applications/shadps4/releases/ngs2-f00bef80/.
   Existing release binaries and the normal core path are preserved.
6. Backs up the launcher and generates a verified restore script under
   ~/.local/state/shadps4-ngs2/<timestamp>-f00bef80/.
7. Inserts a CORE selection immediately before the existing final launch, after
   any per-game sparse-queue/core selection. The invocation, arguments and
   surrounding session/gamepad logic stay in place.
   For the recognized probe dispatcher, only the dedicated NGS2 test entry
   selects the new core; the normal-game fallback is preserved byte for byte.

The sole active installation change is the ES-DE shell wrapper. Audio settings,
7.1 selection, GPU settings, game files, firmware, saves and the Qt launcher are
not edited. The game itself continues to use the same existing user profile.
The original sparse-queue change is an ancestor of the pinned runtime commit.

Success prints NGS2_DEPLOY_RESULT=PASS, the core path, revision, binary hash and
the exact RESTORE command. For a probe dispatcher, select the existing NGS2
probe/test entry in ES-DE; ordinary game entries still select the old core.
For a direct CORE wrapper, launch through ES-DE normally. To restore,
close the game and execute the printed restore script. Restore checks that no
later wrapper edits would be overwritten. The SSH parent shell remains open.

The startup check is not an in-game audio test. Missing sounds/noise, playback
timing, scene transitions and physical channel routing still require the user's
test. General DSP and queued-block resampling limitations are documented in
tests/ngs2_hle/BRIDGE.md at the runtime revision.

## Installer checks

Run python3 documents/ngs2/test_deploy.py -v.

Ten regression cases pass using temporary homes and synthetic ZIP artifacts.
They exercise a real shell launch after a per-game override, install/restore,
file modes and preserved settings, later user edits, changes during download,
invalid launcher shapes, bad ZIP/startup failures, download hashes/size limits and
the CI readiness gate. Probe tests exercise CRLF token dispatch in a real shell,
ordinary-entry argument forwarding, exact rollback and unknown-logic rejection.
No test switches a real user installation. The actual
download and binary startup remain checked by the installer on the target host.

## Recovery after a local diagnostic startup crash

Run `python3 recover_crash.py` as the desktop user before launching another game.
It saves the existing emulator logs, diagnostic trace, local build tail,
deployment records and available recent core-dump metadata to the printed
`CRASH_ARCHIVE` path. Collection does not run the emulator or upload anything.
Each log is limited to its last 2 MiB; the archive is readable only by its owner.

After collection, recovery checks the saved deployment hashes and follows only
the known diagnostic launcher backup chain back to the working f00bef80 selection.
It verifies the earlier executable against its deployment record and atomically
restores the launcher. Unrecognized launcher edits, corrupt backups or a missing
earlier executable stop rollback; the crash archive remains available. Settings,
saves, game files and release binaries are not changed. Recovery is not a fix
for the diagnostic core's startup failure or the earlier build's audio limits.

Run `python3 documents/ngs2/test_recover_crash.py -v` for temporary-home recovery
checks covering log preservation, direct and two-stage rollback, edited launchers,
corrupt backups/binaries and repeated recovery.

## Local startup-fix test

`local_build.py --docker` pins runtime 59566b916c3ff680616081c9bcde642e70f874a7.
It checks that the game is closed, reuses the existing ca67919d-docker build
workspace and runs the focused NGS2 and user-colour regression checks before
building the emulator. The user-colour change fixes an unchecked null lookup
matching the reported startup fault; target-host startup is still unverified.
GPU changes and the controller Home/PS quit confirmation remain present.

The new runner combines stdout/stderr, preserves up to 2048 NGS2 diagnostic lines
and the last 2 MiB of ordinary emulator output, and records the terminating signal.
Its output path includes the runtime revision and is printed as `TRACE_FILE`.
Tests verify that a trap after both logging caps still retains the final stdout
and stderr errors. This update does not claim a fix for silent cutscenes or
dialogue crackles. Close the test normally after trying those scenes and upload
the new trace. Audio/channel and GPU settings are not edited by deployment.
