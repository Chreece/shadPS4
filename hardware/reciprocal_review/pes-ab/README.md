# PES scalar reciprocal comparison

This runner compares native scalar RCPSS/RSQRTSS (including VEX forms) with the optimized Jaguar correction from `0e5a0e1273df701d8069f17c347ec5372d94068d`. It builds one temporary executable and selects the mode before patch registration. Native mode removes the four scalar patch entries; fixed mode emits the verified optimized generator. Packed reciprocal behavior and all other emulator code are identical.

The installed emulator is not an A/B participant: the comparison uses the same pinned candidate on both sides, with the current PES saves and settings copied into a portable profile. This avoids attributing unrelated changes between installed and experimental builds to the scalar fix.

## CPU baseline

Both modes use the bundled CPU stack at `731ababdb7819486eca8dcc7a7f622efa3b75cc7`, with the current affinity implementation from `9811afcdcfd14172adc7a13514b91442c5e5ef88`. The scalar and packed reciprocal generators come from `0e5a0e1273df701d8069f17c347ec5372d94068d`. The source integration preserves the CPU-ID handlers, corrected SSE4a paths, guest feature filtering, pending-signal handling, thread cleanup and bundled Linux runtime. It does not replace the installed emulator or claim the combined candidate has already passed PES gameplay.

`cpu-baseline.patch` adds only the two affinity files and the reciprocal generators/registrations to that CPU stack. `cpu-baseline.json` pins and hashes its required source files before instrumentation. Build completion requires the CPU identity command-line option and all six runtime executables/libraries. Both captures explicitly use translated CPU identity mode, and the report records runtime hashes. A manual timing result requires the emulator's actual “CPU identity translation active” log and its recorded scalar configuration to match the requested mode.

The CPU-ID client translates CPUID, RDTSCP and RDPID only; it has no separate reciprocal handler that could silently override the scalar switch. The optional generated-reciprocal extension is not included. This comparison measures the static scalar correction from the PR, with the same packed correction and CPU prerequisites on both sides.

The previous standalone scalar baseline omitted these prerequisites and must not be used for this PES comparison. The launch detector now uses the same lock as the ES-DE bridge and reads the current state while holding it, avoiding the stale `ready` check that stopped the earlier capture. The final capture state is retained in the archive.

## Manual launch workflow

`pes-manual.py` is a single-download package containing the runner and all helpers. It verifies and writes the bundled files locally, then starts `run.py --manual`; no per-helper network requests are made. The source and pinned dependencies still need to be downloaded and built once.

After preparation, wait for **READY — BEFORE**, then launch PES normally from ES-DE. Reach a repeatable match or replay scene and press Enter in the coordinating SSH terminal. A 90-second measurement starts at that marker. Controller input is allowed. Two screenshots are requested 3 and 9 seconds after the measurement window; normal emulator exit is requested 15 seconds after the window.

Next, wait for **READY — AFTER** and launch PES again. Reach the same scene and press Enter again. The script records the same window, captures screenshots, exits, restores the launcher, verifies original files, archives the evidence and removes its temporary build and profiles. A game that exits early remains in the report and does not prevent trying the other mode. Ctrl+C in the coordinating terminal stops the owned capture, restores the launcher and archives partial evidence.

The same executable uses scalar correction **off** for the first launch and **on** for the second. The temporary launcher route applies only to PES `CUSA18676`, after the existing session guard. Other games retain their original launch command; launching another emulator stops this comparison. While preparing, and after cleanup, the original launcher is used. The route is restored only if it still matches the runner's own edit; an external conflicting edit is preserved and the backup/work directory retained for recovery.

Both launches start with identical copied settings and saves. The report contains the user-selected measurement boundaries, raw frame timestamps, process samples, input timestamps, screenshots and logs. One manual run per mode cannot establish repeatability. Scene equivalence must be checked before interpreting the observed FPS difference. Execution/correction counters are disabled in both timed modes, so these two runs alone do not prove that changed scalar results fixed a game bug.

## Automated sequence (optional original mode)

1. Resolve PES `CUSA18676` from the existing game configuration and identify the local ES-DE/Sunshine desktop session.
2. Refuse to start while another shadPS4 instance is active. Fetch a fresh shallow source checkout and pinned submodules without borrowing Git objects from existing caches. Build in a temporary directory without changing the installed executable, wrapper, or system packages.
3. Create a profile snapshot including the existing PES saves, audio and graphics settings, required resources, and existing emulator caches. Deep-copy that snapshot before each run. Driver caches start empty in separate temporary directories for every run.
4. Run **native → fixed → fixed → native**, followed by a fixed diagnostic run. Each launch lasts 135 seconds, with a 20-second cooldown beforehand.
5. Measure game presentations between **30 and 120 seconds after process launch**. Request game-only screenshots around seconds 123 and 129, after that interval. This also prevents a screenshot queued during a slow boot from landing inside the timing window. No game input is injected. Button, key, or mouse clicks invalidate the speed comparison.
6. Request normal emulator exit. If it hangs, terminate only the process group created by the runner, escalating after bounded waits. Record whether cleanup was forced. An early game exit is recorded and the remaining variants still run, so a crash in one mode does not prevent testing the other.
7. Recheck original file hashes, archive the evidence, and remove the temporary source, build, profiles, and caches. The runner never restores stale copies over externally modified originals.

Keep Moonlight connected with ES-DE open and no game running. Do not provide game input during the capture. The unattended test may remain at a title screen; it does **not** establish match gameplay performance. The screenshots must be inspected to establish that equivalent scenes were reached. Rendering differences are evidence to investigate, not automatic proof that a bug was fixed.

Progress messages confirm the bundled helpers, then identify preflight checks, profile copying, file hashing and each build command. Long build commands report their elapsed time and log path every 15 seconds.

## Evidence

- Raw timestamps for actual new game frames presented by the Vulkan presenter. Reused frames and UI-only presentations are excluded. These are CPU-side presentation timestamps, not GPU execution durations or display scanout times.
- First-frame latency; average FPS over the full 90-second window; median, p95, p99 and maximum frame intervals; counts above 33.3 ms and 50 ms.
- Per-second process CPU time, RSS, thread count and affinity, system CPU/load samples, and available thermal/DRM GPU activity counters. Process CPU percentage uses 100% for one fully busy core. GPU counters describe the whole device.
- Game screenshots, emulator logs, exit status, source/build hashes, copied-profile hashes, and original-state verification.
- A separate diagnostic run sampling counters for executed scalar patches and nonzero corrections for each instruction form. Those counters add work and are **excluded from speed results**. Discovered patch sites are recorded separately from executed instructions.

A nonzero correction count proves the scalar fix changed numerical results during this captured run. It does not by itself prove a visual, physics, crash, or gameplay improvement. Zero hits only describes the observed scene. Two runs per mode and ABBA ordering provide an exploratory comparison, not statistical significance; the report includes run-to-run spread and requires screenshot review.

## Isolation and prerequisites

Python 3.11+, the existing GCC 14 toolchain, CMake, Ninja and Git are required. No packages are installed. The runner recognizes the previously observed guarded launcher and conventional profile; unexpected launch arrangements stop for inspection. It requires an Intel x86-64 host because the correction is gated to Intel in the candidate.

The portable profile redirects save home, fonts, system modules, add-ons and caches into the temporary area. The emulator mounts the game directory read-only. When Linux Landlock ABI 3+ is available, child processes additionally receive filesystem write restrictions; this is tested before use and recorded. Otherwise the copied portable profile and path redirection are used. This does not alter system security settings.

Original configs/users, custom configs, patches, cheats, PES saves, installed binary, launcher/guard and game metadata/eboot are hashed before and after. Game saves, settings and the installed binary are never overwritten. Manual mode temporarily routes the PES launcher and restores its original contents, mode and timestamps before this verification. A home-directory inode lock prevents concurrent copies of this runner; process checks detect other emulator runs. Other processes are not terminated.

The source patch also adds the missing `<optional>` include needed to compile the unchanged notification code. That build-only correction is identical in both modes and does not enter the production scalar PR.

## Validation

See `validation.txt`. Harness runs validate the orchestration and cleanup; they are not PES results. The actual game run occurs on the user's machine. A completed or failed capture produces `PES_AB_ARCHIVE=...tar.gz` for inspection.

## SIGBUS diagnostic after the manual A/B run

Run the single-file bundle with `--debug-crash`. This selects only scalar ON,
using the same pinned CPU baseline, reciprocal generators and C++ instrumentation
as the timing run. Existing GDB with Python support is required. No package is
installed and no global ptrace policy is changed. The owned emulator grants its
parent and the parent's debugger permission to attach when Yama is available.

Before building, an isolated helper deliberately raises SIGBUS. The runner
requires GDB to preserve its signal, registers, stack and maps, and verifies that
the helper really exits from SIGBUS. An attach failure stops preparation and
archives the diagnostics.

After READY, launch PES through ES-DE, reach the same scene and press Enter in
SSH. Screenshots are requested 2, 20 and 40 seconds after the marker. Capture
ends after 90 seconds plus the existing 15-second exit allowance, or sooner if
the emulator exits. Original launcher/profile/save verification and cleanup
remain active.

GDB records SIGBUS and SIGABRT events, then delivers the original signal to the
emulator. Handled runtime safe-read faults therefore do not get mislabeled as
fatal crashes; the parent records the actual process return code independently.
Up to 16 unique signal sites get full snapshots, and the latest later event
replaces `signal-last.*`. Metadata for the first 512 events is retained.
Snapshots include registers, disassembly, current/all-thread backtraces, maps,
and small code/stack memory excerpts. The archive includes `gdb.log`, attach
status and any debugger error.

Debugger timings are never benchmark results: `speed_valid` is false and
`fps_over_full_window` is null. Incomplete ordinary manual runs also have null
full-window FPS, instead of dividing partial frame counts by 90 seconds.
