# PES scalar reciprocal comparison

This runner compares native scalar RCPSS/RSQRTSS (including VEX forms) with the optimized Jaguar correction from `0e5a0e1273df701d8069f17c347ec5372d94068d`. It builds one temporary executable and selects the mode before patch registration. Native mode removes the four scalar patch entries; fixed mode emits the verified optimized generator. Packed reciprocal behavior and all other emulator code are identical.

The installed emulator is not an A/B participant: the comparison uses the same pinned candidate on both sides, with the current PES saves and settings copied into a portable profile. This avoids attributing unrelated changes between installed and experimental builds to the scalar fix.

## Automated sequence

1. Resolve PES `CUSA18676` from the existing game configuration and identify the local ES-DE/Sunshine desktop session.
2. Refuse to start while another shadPS4 instance is active. Fetch a fresh shallow source checkout and pinned submodules without borrowing Git objects from existing caches. Build in a temporary directory without changing the installed executable, wrapper, or system packages.
3. Create a profile snapshot including the existing PES saves, audio and graphics settings, required resources, and existing emulator caches. Deep-copy that snapshot before each run. Driver caches start empty in separate temporary directories for every run.
4. Run **native → fixed → fixed → native**, followed by a fixed diagnostic run. Each launch lasts 135 seconds, with a 20-second cooldown beforehand.
5. Measure game presentations between **30 and 120 seconds after process launch**. Request game-only screenshots around seconds 123 and 129, after that interval. This also prevents a screenshot queued during a slow boot from landing inside the timing window. No game input is injected. Button, key, or mouse clicks invalidate the speed comparison.
6. Request normal emulator exit. If it hangs, terminate only the process group created by the runner, escalating after bounded waits. Record whether cleanup was forced. An early game exit is recorded and the remaining variants still run, so a crash in one mode does not prevent testing the other.
7. Recheck original file hashes, archive the evidence, and remove the temporary source, build, profiles, and caches. The runner never restores stale copies over externally modified originals.

Keep Moonlight connected with ES-DE open and no game running. Do not provide game input during the capture. The unattended test may remain at a title screen; it does **not** establish match gameplay performance. The screenshots must be inspected to establish that equivalent scenes were reached. Rendering differences are evidence to investigate, not automatic proof that a bug was fixed.

Progress messages begin with helper downloads, then identify preflight checks, profile copying, file hashing and each build command. Long build commands report their elapsed time and log path every 15 seconds.

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

Original configs/users, custom configs, patches, cheats, PES saves, installed binary, launcher/guard and game metadata/eboot are hashed before and after. Original files are never overwritten. A home-directory inode lock prevents concurrent copies of this runner; process checks detect other emulator runs. Other processes are not terminated.

The source patch also adds the missing `<optional>` include needed to compile the unchanged notification code. That build-only correction is identical in both modes and does not enter the production scalar PR.

## Validation

See `validation.txt`. Harness runs validate the orchestration and cleanup; they are not PES results. The actual game run occurs on the user's machine. A completed or failed capture produces `PES_AB_ARCHIVE=...tar.gz` for inspection.
