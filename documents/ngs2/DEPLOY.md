<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Isolated Linux test deployment

## Single-instance ES-DE launch guard

`python3 session_guard.py install` adds a reversible prefix to the existing
`~/.local/bin/shadps4-esde` Bash launcher. The original dispatcher, test core,
fallback entries and arguments are retained. No emulator build is required.
Run as the desktop user without sudo. Installation can run while a game is open;
it lists existing shadPS4 processes and does not terminate any of them.

Future launches through that wrapper take a per-user lock before starting a
game. If a same-user shadPS4 core already exists, including one started before
installation or surviving the old frontend, the guard waits for that game
instead of starting another. It requests window activation using existing
`wmctrl` or `xdotool` when available. Without those tools, duplicate blocking
still works but automatic focus is unavailable. When existing duplicates are
present, an identifiable active game is preferred; otherwise the newest core
is selected. No duplicate is automatically stopped and no game is queued to
start after the current game exits.

This is shadPS4 launcher protection, not a change to Sunshine disconnect policy
or the lifecycle of other emulators. Direct launches bypassing `shadps4-esde`
are outside its lock. AI services, game saves, audio/7.1 settings, renderer
settings and emulator binaries are not changed. Save and close pre-existing
duplicates normally before testing a fresh launch/disconnect/reconnect cycle.

The helper is installed at `~/.local/lib/shadps4-session-guard/guard.py` and the
original launcher is backed up under `~/.local/state/shadps4-session-guard/`.
To remove only the guard prefix, run:

```
python3 "$HOME/.local/lib/shadps4-session-guard/guard.py" uninstall
```

The current local deployment validates and preserves this exact prefix while
switching the test invocation. Modified guards are rejected. Older pinned local
installers can reject the guarded shape rather than silently bypassing it.

`python3 -m unittest discover -s documents/ngs2 -p test_session_guard.py -v`
checks simultaneous launches, an existing unguarded core, reconnection after
supervisor exit, lock release and inheritance, argument preservation, idempotent
installation and exact removal. It uses disposable Linux processes, not a game
or GPU. Actual Moonlight window restoration still needs the user's local test.

## Combined main: local graphics test

Develop each runtime fix on its own feature/fix branch, merge it into `main`,
and build the resulting combined main revision locally. Remote platform builds
follow successful local testing when requested.

`python3 main_build.py --docker` pins main revision
`038bb3d83e751e50328abb98f04fcb2c3ee7897e`. It fetches `main` and refuses to build
if that branch no longer matches the pinned revision. Main contains the clean
NGS2 audio integration, tested sparse queue/BDA fixes, the new graphics fix, and
the restored user-colour startup guard from `fix/userservice-missing-user`
(`e2251423f46f0bc20e07f5d9e127af141adb7e26`).
The graphics change was developed on `fix/texture-subresource-containment`
(`fc8945dfdd08dfa51fd2537e16b639240123b4f4`) and merged with its branch ancestry
preserved. The four-file delta from previous main applies upstream
[PR #4818](https://github.com/shadps4-emu/shadPS4/pull/4818), by jute-ado:
texture containment must check both mip levels and array layers. The old
lexicographical comparison could accept an image with too few layers.

The two renderer captures contained image-copy layer mismatch warnings. Those
warnings do not prove this bug causes the reported disappearing detail; this
build is a local test of combined main. Sun visibility through walls remains a
separate occlusion suspect. No predication, tiling, readback, or additional
graphics PR is included. The clean audio branch remains independent.

The helper reuses the existing `ca67919d-docker` workspace and compiler cache.
It runs the four texture-containment tests, the user-colour regression, and the full Linux emulator
build for this graphics change, then checks executable startup before switching
the existing NGS2 probe entry. Audio and sparse code are unchanged from the
previous main integration; their previous focused suites are not repeated for
this graphics change. The earlier main integration incorrectly omitted the
startup guard included in the working f1c1c790 build. Main now retains that guard
as a separate fix; unrelated controller changes remain outside this integration.
No remote or cross-platform CI build is requested.

The launcher switch preserves the single-instance guard and normal fallback,
backs up the complete current wrapper, and prints a checked `RESTORE` command.
Configuration, the working binary, saves, AI services, and 7.1 audio stay intact.
The game must be closed before starting the build and before the final switch.

Validation performed before publication: all four upstream C++ containment
tests passed with the repository's vendored Google Test; all 30 local deployment
checks passed, including a real guarded fallback launch and exact rollback.
Full Linux integration compilation runs in Docker on the user's homeserver.

After `MAIN_LOCAL_RESULT=PASS`, launch the existing NGS2 probe/test entry.
Move the camera near the affected trees, walk through the same area, and enter
the building where the sun remained visible. Report missing/flickering detail
and sun visibility separately. The trace is `~/ngs2-diagnostic-038bb3d8.log`;
the renderer log remains `~/.local/share/shadPS4/log/shad_log.txt`.

The earlier `graphics_build.py` and `test/rdr-texture-containment` revision are
retained as history; the deployment for this workflow uses `main_build.py`.

When the currently selected test entry is failed main 2abd0fb0, `main_build.py`
first verifies that deployment and its saved launcher backup. It verifies the
retained f1c1c790 executable against its original deployment hash, saves the
failed trace and renderer-log tails (up to 2 MiB each) in the private directory
printed as `CRASH_EVIDENCE`, and restores the exact guarded working launcher.
The Docker build then starts with that known working selection in place. A
later build failure leaves the working selection active. Unknown launcher
edits, changed binaries or a backup selecting another revision stop recovery.
No game is launched automatically and no configuration or save is edited.

The missing-user regression passes against the restored production function;
the same fixture reproduces SIGSEGV against 2abd0fb0's unchecked function.
The new host crash trace has not yet been supplied, so the fault attribution
remains provisional until local startup is retested. Recovery checks cover
exact guard preservation, saved trace content, idempotence, and refusal of
modified launchers or working executables.

## Original artifact installer

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

The previous local build pinned runtime 59566b916c3ff680616081c9bcde642e70f874a7.
It checks that the game is closed, reuses the existing ca67919d-docker build
workspace and runs the focused NGS2 and user-colour regression checks before
building the emulator. The user-colour change fixes an unchecked null lookup
matching the reported startup fault. The subsequent user log reached rendering
and exited cleanly, although the cutscene stalled with working pause controls.
GPU changes and the controller Home/PS quit confirmation remain present.

The new runner combines stdout/stderr, preserves up to 2048 NGS2 diagnostic lines
and the last 2 MiB of ordinary emulator output, and records the terminating signal.
Its output path includes the runtime revision and is printed as `TRACE_FILE`.
Tests verify that a trap after both logging caps still retains the final stdout
and stderr errors. This update does not claim a fix for silent cutscenes or
dialogue crackles. Close the test normally after trying those scenes and upload
the new trace. Audio/channel and GPU settings are not edited by deployment.

## Local callback/control test

The callback/control local test pinned runtime
`66a2ef4d25e2029628dad50f5ec9a308ef072c47`. It accepts the known 59566b91 test
dispatcher as well as earlier recorded test versions, keeps normal ES-DE fallback,
and reuses the existing Docker build cache. It installs only after the focused
NGS2/user-colour checks and full local Linux build succeed. Rollback recognizes
this revision and continues to verify the launcher and prior binary hashes.

This revision preserves grains across ordinary UserFx-time voice updates and
prevents idle incomplete routing from blocking other voices. The audio trace adds
bounded rejected-waveform/filter details. The runner also retains a separate
bounded error tail so later GPU info messages cannot evict the latest errors.
It still records exit status and the ordinary console tail after the game closes.

No full GitHub build is requested. In-game audio remains experimental: this is
an isolated test candidate, not confirmation that cutscenes or dialogue are fixed.
The new trace is `~/ngs2-diagnostic-66a2ef4d.log`. Exit after testing the affected
cutscene and dialogue, then upload that completed trace. Deployment preserves
7.1/eight-channel configuration, saves, the working installation and GPU settings.

## Local ATRAC9 config test

`local_build.py --docker` now pins
`9e95c1727d287514d0e85aef9f863e0b293f6e5b`, accepting the recorded 66a2ef4d
launcher alongside older test selections. It keeps the same isolated Docker
cache, pre-install checks, normal ES-DE fallback and verified rollback.

The new user trace had no recorded render/matrix errors but rejected valid
`0xfe4005f0` ATRAC9 setup. This revision corrects scalar-to-codec byte order in
both directions. Three new regression cases reproduce the boundary mistake;
focused Release and targeted audio sanitizer checks pass. This remains an
experimental in-game test; unsupported block flags and filters still need work.

Close the game before building. After installation, test the same NGS2 entry,
then exit and upload `~/ngs2-diagnostic-9e95c172.log`. No host audio/channel
settings or normal emulator deployment are changed. Cross-platform CI stays
skipped until local testing justifies it.
