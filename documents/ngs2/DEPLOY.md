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

The sole active installation change is the ES-DE shell wrapper. Audio settings,
7.1 selection, GPU settings, game files, firmware, saves and the Qt launcher are
not edited. The game itself continues to use the same existing user profile.
The original sparse-queue change is an ancestor of the pinned runtime commit.

Success prints NGS2_DEPLOY_RESULT=PASS, the core path, revision, binary hash and
the exact RESTORE command. Then launch through ES-DE normally. To restore,
close the game and execute the printed restore script. Restore checks that no
later wrapper edits would be overwritten. The SSH parent shell remains open.

The startup check is not an in-game audio test. Missing sounds/noise, playback
timing, scene transitions and physical channel routing still require the user's
test. General DSP and queued-block resampling limitations are documented in
tests/ngs2_hle/BRIDGE.md at the runtime revision.

## Installer checks

Run python3 documents/ngs2/test_deploy.py -v.

Eight regression cases pass using temporary homes and synthetic ZIP artifacts.
They exercise a real shell launch after a per-game override, install/restore,
file modes and preserved settings, later user edits, changes during download,
invalid launcher shapes, bad ZIP/startup failures, download hashes/size limits and
the CI readiness gate. No test switches a real user installation. The actual
download and binary startup remain checked by the installer on the target host.
