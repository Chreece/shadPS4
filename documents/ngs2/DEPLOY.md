<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Isolated Linux test deployment

## Current local graphics test: 2026-10-02

At 12:28 UTC the host repair reports a 50-second timeout while starting the two
system services. The error alone does not identify the failed unit or establish
whether rollback completed; its own start can replace the original exception.
Do not repeat the installation until the service report has been reviewed.
`repair_sunshine_display.py --recover` saves the journal and service properties
before changes, finds a checksum-compatible backup of either published attempt,
and accepts files already rolled back or partially restored. It requires the
known service owner, shutdown scope and start commands, and still refuses games.
It restores only the matching original files and removes only its exact drop-in.
Service jobs are enqueued without blocking, then their states are checked with
bounded polling; a pending start is explicitly not a successful recovery.
The private `RECOVERY_REPORT` captures both before and after states. No Xorg,
emulator, audio configuration, or unrelated service is changed. This is a recovery
and evidence collection step, not another claimed blank-screen fix.

The 11:50 UTC capture confirms that the inherited `.Xauthority` is rejected,
while the running user's Xorg `-auth` file opens the same display successfully.
X11 is awake at 1920x1080, has no ES-DE client window, and ES-DE reports a
1024x768 screen. This is consistent with an offscreen fallback; the SDL video
driver itself is not logged. The AppImage parent and renderer child belong to
one launch, not two separate frontend sessions.

Per the user's correction, `repair_sunshine_display.py` fixes authorization at
**Sunshine's service start**, not in an ES-DE-specific launcher. A scoped systemd
drop-in executes the existing `/usr/bin/sunshine` with verified `DISPLAY=:0`
and a stable, private XAUTHORITY symlink. The current global KMS prep guard and
display watchdog refresh that symlink to a successfully probed, already readable
authority file when Xorg changes. It never copies cookies or changes X11 access
permissions. Applications inherit Sunshine's environment; apps.json, application
commands, video-driver selection, emulator binaries, saves and audio/7.1 settings
are unchanged. An application explicitly overriding XAUTHORITY may still need
separate review; global apps.json display overrides stop installation.

The installer checks the supplied guard hashes, service ownership/commands and
process scope, refuses a running game/other Sunshine app, backs up exact bytes,
and retains a checksum-verified restore command. It asks for sudo because both
services are system units. It briefly stops/restarts Sunshine and its watchdog,
disconnecting Moonlight and closing its existing ES-DE process pair; it does not
restart Xorg or unrelated services. Startup failure triggers rollback. A PASS
checks the actual running Sunshine process's X11 access, not just file contents.
Local fixture tests cover authority rotation, inheritance without audio changes,
failed auth with cleanup preserved, idempotence, rollback after failed start,
later-edit protection, and a live-game stop. Real host window/relaunch testing
remains pending: reconnect Moonlight, open ES-DE, exit, and launch it again.
No emulator build or GitHub CI is needed for this host-service repair.
AI assistance: Codex prepared and reviewed this change on
`fix/sunshine-display-inheritance`, using the user's supplied process/display log.

The first host attempt stopped before installation on `memfd:squashfus`, an
AppImage mount-worker name truncated by Linux. The preflight now recognizes
SquashFUSE workers using their executable identity and open `/dev/fuse` descriptor
inside the current user's Sunshine service. These workers can exit with the
service restart; every other process is still checked so a worker never hides
an active game. Regression checks cover a leftover worker, an accompanying game,
and a matching name without the required executable/FUSE descriptor. No broad
process-name kill or manual PID termination is introduced.

At 11:35 UTC the user reports a blank Moonlight screen after exiting and
relaunching ES-DE. Pause the renderer comparison until the frontend is usable.
The older Sunshine watchdog's `repair_x11` silently returns when `xrandr` cannot
open the display; its environment still selects the client's `.Xauthority`.
The shadPS4-only resolver did not update that service or the ES-DE app command.
This identifies a recovery gap, but does not prove the current blank screen's
cause. `collect_sunshine_display.py` now probes the running user's verified Xorg
authority as well as the inherited client path, captures window placement and
DPMS state, the current watchdog/idle-guard scripts, both service scopes, and
recent ES-DE/Sunshine logs. It does not restart services, signal processes, change
windows or copy authorization cookies. Its resulting 11:50 UTC capture provided
the evidence for the central repair above.
AI assistance: Codex prepared this collector on `diagnostics/esde-relaunch`.

The 11:20 UTC launch capture and completed trace confirm that `286d0cca` now
starts through the guarded wrapper with ES-DE's live environment. The resolver
selected Xorg authorization, one core was observed, and the game exited with
code zero. The user reports glitches throughout gameplay. The mip correction
was exercised (`micro-mip-detile` reaches count 8192); it did not resolve the
reported symptom. Sampled query results were complete with no missing mask.
These observations do not prove all queries correct. Validation was disabled.
The UI entry's earlier failure is not explained by this successful wrapper test.

`trace_launch.py --radv-sync-shaders` runs a controlled comparison with the same
installed core and `RADV_DEBUG=syncshaders` in only the child launch environment.
It waits for normal exit, then packages the completed bounded trace and renderer
log tails. This is a diagnostic test, not a renderer fix or a permanent setting.
The choice follows the user's earlier report that validation reduced glitches
while slowing the game; a change under serialization supports timing/scheduling
sensitivity, but does not establish an AMD driver bug or select a specific patch.
Mesa documents the option at https://docs.mesa3d.org/envvars.html#envvar-RADV_DEBUG.
It can reduce performance and does not flush every GPU cache.

Keep Moonlight/ES-DE open, start one test with the helper, compare the same area
and route, then exit normally using the existing gamepad exit control. Upload the
printed LAUNCH_REPORT archive and report whether geometry still disappears. No
second manual launch is needed. The report records the live core's flag, not just
the requested setting, and also reads the existing NGS2 entry's first line to
investigate why its UI launch differed. Existing cores prevent another launch;
an inherited RADV_DEBUG prevents mixed experiments. Audio/7.1 configuration,
installed files, GPU settings and services are not modified. The next ordinary
launch does not inherit this option. No build or GitHub CI is requested.

AI assistance: Codex prepared this diagnostic on `diagnostics/radv-sync-shaders`
after reviewing the supplied logs, renderer paths and current upstream PRs.

The supplied 10:17 UTC failure capture confirms that the local `286d0cca`
build completed and the binary started. X11 then rejected display authorization;
`SDL_Init(SDL_INIT_VIDEO)` stopped at `sdl_window.cpp:105` with "No available
video device" and SIGTRAP. No renderer or tiled-mip code ran in this captured
launch. The texture change's game result remains untested by this failed launch.

The display repair subsequently passed its live SSH check using Xorg PID 859660,
but the user reports that the NGS2 entry still fails. That check did not establish
a successful game launch. `trace_launch.py` makes one launch through the existing
guard with the live ES-DE environment, records guard stdout/stderr, trace times,
process state, selected display/library variables and available PS4 custom-system
commands, and produces a private LAUNCH_REPORT archive. It does not rebuild,
edit installed files or settings, or terminate a process. If a core is already
running it captures that fact and requests no additional launch. A running test
started by the capture is left open after the bounded observation window.

`repair_display_launch.py` addresses the launcher/session environment without
rebuilding the emulator. It verifies the retained `286d0cca` binary against its
deployment record, authenticates a read-only `xprop` request to the reported
headless display `:0`, and updates only the exact known shadPS4 session guard.
The guard resolves current display credentials before starting its child while
retaining the single-instance lock. A working inherited environment is kept;
fallback authority paths must come from same-user session processes or Xorg
inside this user's session cgroup, match the requested display, be readable,
and pass the live connection probe. No cookie contents are logged or copied,
no `xhost` access is granted, and no X server or Sunshine service is restarted.

The repair restores the already-built candidate's recorded test selection after
a rollback to `6e00d2cc`. It backs up the launcher, guard and optional sidecar,
prints a checked RESTORE command, refuses unknown edits, and stops if any game
is running. Emulator settings, saves and eight-channel routing are untouched.
Keep Moonlight/ES-DE open; after `DISPLAY_REPAIR_RESULT=PASS`, launch the existing
NGS2 entry once. Upload a graphics capture after reaching the affected scene.
If display authentication cannot be verified, no launcher files are changed.

Twelve local checks cover display selection, authorization failure, other-user
session rejection, retained-binary verification, exact restoration and an actual
three-launch process race with the patched guard. Host X11 access and game
rendering still require the user's local test. AI assistance: Codex prepared the
launcher repair and its regression checks on `fix/launcher-display-session`.

`recover_mip_test.py` remains available to capture a failed launch and restore
the verified retained `6e00d2cc` launcher without a build.

`main_build.py --docker` now pins combined main
`286d0cca483ce80f9d4a4fe98d4620b6b003e0ca`. This integrates
`fix/tiled-mip-layout` (`261f339244bd3179089ed131be371df4c1b670b2`), based on
the merged upstream PR #5196 (`7e77898756a50e2aa0bae0031790eb81b392e9b2`).
It retains the existing audio, sparse queue, startup, depth and presentation fixes.

The latest test of `6e00d2cc` still showed missing elements while entering gameplay.
Its capture contains tiled uploads but no raw-buffer/image synchronization events.
The upstream mip fix is a candidate for this symptom, not a confirmed game fix.
A bounded `micro-mip-detile` event now records affected image uploads.

The helper recognizes an upgrade from `6e00d2cc`, runs the five new Linux layout
regressions alongside existing focused checks, and compiles the full Linux core
in the existing Docker cache before the guarded launcher switch. No GitHub or
cross-platform build is required. Source is pinned; if main moves, the helper
stops without switching the launcher. Existing saves, configuration and 7.1 audio
are retained. The installer prints the verified rollback command.

After `MAIN_LOCAL_RESULT=PASS`, launch the existing NGS2 probe entry once. Compare
the initial load and the same route twice, then collect while elements are missing:

```sh
python3 "$HOME/Applications/shadps4/releases/ngs2-286d0cca/collect_graphics.py"
```

Upload the resulting `GRAPHICS_REPORT` archive. This test has not yet established
in-game graphics correctness; in-game audio also remains experimental.
Older revision-specific notes below are retained as deployment history.

## Graphics capture after the startup correction

The user confirmed that combined main 038bb3d8 starts without the immediate
crash, but light remains visible through buildings and other graphics glitches
remain. The texture-containment change is not a confirmed solution for those
symptoms. The current renderer still writes synthetic pixel-pipe counters;
upstream PR #4610 is an occlusion/predication candidate, currently a draft with
no reported RDR validation. Do not treat it as an established game fix.

Run `python3 collect_graphics.py` while the affected scene is visible. Collector
v3 selects the diagnostic trace for the running release instead of always
collecting f1c1c790. With no running core it uses the selected launcher's revision
and explicitly warns that renderer logs may be stale. GPU and Vulkan settings
are read alongside current renderer logs and process information. The helper
does not launch or stop the game, edit configuration, or upload the archive.
An existing normal-level log may contain no pixel-pipe events; absence is not
evidence that the game does not use occlusion queries. A screenshot of the
same scene can distinguish a sun flare from missing building geometry.

The supplied 14:25 capture confirms main 038bb3d8, one running emulator,
eight-channel/48 kHz output, and 16 layer-copy warnings (15 in the earlier
capture). No renderer error or device loss is recorded. The diagnostic combined-main
revision d5c5acc0 merges `diagnostics/graphics-visibility` (c2f872a7), an optional
bounded trace of pixel-pipe queries, predication/conditional execution, image
copies and containment failures. Existing rendering decisions are unchanged.
This is an evidence build, not a new graphics fix. The local runner enables it
and saves up to 512 graphics records independently of the audio trace limit.
Disabled/enabled trace checks and live runner capture-limit checks pass.

The supplied d5c5acc0 trace subsequently confirms active pixel-pipe dumps:
at least 16,384 in 104 seconds, with every dump advancing the fake counter by
0x2ffffff. No predication or containment-miss record was observed. Sampled
copy-layer warnings describe same-address R32_SFLOAT colour-array growth, not
depth copies. This gives a concrete occlusion candidate, not proof of the
indoor-light cause. That trace has no exit footer, so it does not establish a
clean game exit.

The user then tested 2b82d291: outside light no longer appeared indoors, but
geometry disappeared/reappeared more often. The supplied trace has exit_code=0
and successful sampled zero/positive query results. Review found the initial
occlusion adaptation incorrectly toggled counting on PIXEL_PIPE_STAT_CONTROL;
AMD specifies counter selection there, with counting controlled separately by
DB_COUNT_CONTROL. The next candidate corrects that distinction. This is a known
code error, while its responsibility for all visible flicker remains unconfirmed.

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
`10ff9e19a7d94340aaedd1e333f1a11abeeb9e75`. It fetches `main` and refuses to build
if that branch no longer matches the pinned revision. Main contains the clean
NGS2 audio integration, tested sparse queue/BDA fixes, the new graphics fix, and
the restored user-colour startup guard from `fix/userservice-missing-user`
(`e2251423f46f0bc20e07f5d9e127af141adb7e26`).
The previous graphics change was developed on `fix/texture-subresource-containment`
(`fc8945dfdd08dfa51fd2537e16b639240123b4f4`) and merged with its branch ancestry
preserved. The four-file delta from previous main applies upstream
[PR #4818](https://github.com/shadps4-emu/shadPS4/pull/4818), by jute-ado:
texture containment must check both mip levels and array layers. The old
lexicographical comparison could accept an image with too few layers.

The initial measured-query candidate is `fix/occlusion-query-writeback`, commit
`66db3a63949890267b2c2e32ba49dcf92ea30df2`. It adapts the query/readback subset
of cuesta4's draft [PR #4610](https://github.com/shadps4-emu/shadPS4/pull/4610),
replacing the active fake counters with GPU measurements. The aggregate sample
count is preserved across 8/16 guest pipes. Ordered callbacks avoid the draft's
nested scheduler-lock issue; guest fences wait for preceding counter writes.
This conservative synchronization may affect frame pacing. Conditional rendering
and unrelated graphics changes are excluded. Indoor-light improvement still
requires local testing after each change. The clean audio branch remains independent.

The follow-up is `fix/occlusion-count-control`, commit
`77155ef62cf2e83b91c40e8557930931c5bd57f6`, merged into the main revision above.
Repeated counter selection no longer toggles measurement. Each draw reads the
guest count register, and four counter banks retain independent totals and
resets. Unsupported count modes are marked incomplete and conservatively visible.
The paired-qword output and GPU completion ordering are retained. The trace now
includes raw control payloads, selected counter, register values and bank masks.
Check geometry stability and that outside light remains blocked indoors.

The helper reuses the existing `ca67919d-docker` workspace and compiler cache.
It runs the four texture-containment tests, the user-colour regression, the
bounded graphics-trace checks, both occlusion counter regressions, and the full Linux emulator
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

The prior integration passed the four upstream C++ containment tests and its
deployment checks. This candidate passes the counter/layout and enabled/disabled
trace tests, plus the register/control regression and GCC syntax checks for its
query manager, rasterizer, command processor and register layout. Deployment
validation checks the upgrade from 2b82d291, guarded normal
fallback, trace capture and exact rollback. Full Linux integration compilation
runs in Docker on the user's homeserver; no GPU game validation is claimed here.

After `MAIN_LOCAL_RESULT=PASS`, launch the existing NGS2 probe/test entry.
Move the camera near the affected trees, walk through the same area, and enter
the building where the sun remained visible. Report missing/flickering detail
and sun visibility separately, including any new frame-pacing regression.
The trace is `~/ngs2-diagnostic-10ff9e19.log`;
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

## Local depth-image correction test

`main_build.py --docker` pins combined main
`77c6bd3a1f116c605370e765464423a668f25ba1`. The graphics change lives separately on
`fix/depth-image-copy-sync` at `bc479ce3207347c1c0ed72df49755c9cf7121205`.
It fixes the D32/D32S8 depth copy path and orders consecutive image transitions,
following the user's 17:21 Vulkan validation report. That report confirms Precise
readbacks and active validation; it does not establish a successful graphics fix.
The existing audio, sparse-queue, startup and earlier graphics changes remain.

The helper accepts the current 10ff9e19 dispatcher and preserves the normal
launcher fallback, single-instance guard, saves and audio settings. If the known
temporary validation session is still enabled, its checksum-verified restore
helper restores the exact pre-test per-game profile before building. Unknown or
subsequently edited validation profiles are preserved and stop the deployment.
Readbacks mode remains unchanged. No global setting is edited.

The build reuses the existing Docker workspace, adds the three image-transfer
checks, runs the existing focused graphics/occlusion/startup checks, and switches
only after the full local Linux build and executable smoke check succeed. GitHub
CI is skipped. Local host checks also exercise the upgrade from 10ff9e19, normal
fallback, exact rollback and validation-session restoration.

After `MAIN_LOCAL_RESULT=PASS`, launch the existing NGS2 trace entry. Compare the
same street, character/building surfaces, unusual colours, indoor light and the
cutscene. Exit normally and upload `~/ngs2-diagnostic-77c6bd3a.log`. The earlier
core is retained and the installer prints a verified rollback command.
In-game graphics and audio remain experimental. Other validation findings
(swapchain presentation, arena buffer size and mapped-memory flush alignment)
are not fixed by this depth-image change.
