# GoW native universal SRT build 44cae500 - display and warning evidence (2026-10-26)

## Exact uploaded evidence

- Build: `shadps4-srt-warnfix-20261026-140602-4067061-build.tar.gz`
- Runtime: `shadps4-srt-warnfix-20261026-140602-4067061.tar.gz`
- Additional worker log: `shadps4-srt-warnfix-20261026-140602-4067061.log`
- Source commit: `44cae5008d29e2f2d7af214744dc2905d18e7607`.
- Candidate executable: SHA256 `d642061720dfa2c6773e75050185f53ac60e11a1a859d01a5f3308747657c908`.
- 18/18 focused regressions passed; native binary built and linked.
- Native compiler warnings 104 (14 first-party, 84 vendor, 6 system header/inlined), GCN tests 0. First-party warnings all in `libc_internal/printf.h` at the three guarded scratch buffer sites 169, 173 and 380; generated source already eliminated prior first-party NP, NID, ATRAC9 and network warnings. The inlined atomic writes stem in part from `HandleTable::CreateStdHandles` and `PthreadSemWait`. Third-party: miniz 76, FDK-AAC 4, glslang 2, protobuf 1, ext-boost 1.
- Runtime: 29.191 seconds, exit `-11` (not a diagnosis on its own); after Ollama API unload there were 19.560 GiB VRAM free; original model restored.
- The final console-excerpt lines are exactly:
  `XIO: fatal IO error 2 (No such file or directory) on X server ":0"`
  `terminate called after throwing an instance of 'std::system_error'`
  `what(): Resource deadlock avoided`
- No explicit RADV guilty-context or Vulkan `ErrorDeviceLost` message in this run, unlike previous 32.508 and 36.867 second candidates. Journal `journalctl -k` was readable and returned zero matching GPU kernel events. These **do not prove** absence of a GPU-level issue; X11 loss could be cause or consequence.
- Screenshot observer returned an empty screenshot list (11,21,26 attempts failed or did not produce files); no visual evidence of gameplay.
- Native observer's selected-console-diagnostics truncated at its earliest 420 matches, so final cause was not captured there; the last-console excerpt rescued these lines.
- Across 4 previous runs: baseline 120.314 s terminated at test limit, late RADV soft-recovery and X connection lost; Phi-only 32.508 s `ErrorDeviceLost`; auto-BDA 36.867 s `ErrorDeviceLost`; latest warnfix 29.191 s X11 `fatal IO` and `std::system_error`. No evidence that any is playable or visually complete.
- Still thousands of host SRT walker errors; dynamically varying `Phi` offsets are not flattenable on CPU. Auto BDA for `ReadConst` does not by itself solve GPU-dynamic sharp descriptor identities. This must remain a separate generic rendering fix without shader-ID/game-ID exceptions.

## Further source corrections, unvalidated until build/runtime

1. `src/core/file_sys/fs.cpp`: `CreateStdHandles` now constructs and atomically publishes a ready File under one mutex; avoids unchecked GetFile pointer and concurrent delete window. Preserves the 0/1/2 file descriptor ABI.
2. `src/video_core/buffer_cache/fault_manager.cpp`: fault parser barrier now declares shader read **and write** access to atomically used bitset, includes transfer-written initialization, and explicit HOST_WRITE->compute and compute->HOST_READ dependencies for noncoherent download memory. Combined with previous fill/flush/invalidate and page request saturation, this fixes generic synchronization, not established to cure X11 disconnect.

## Safe next evidence

Run the committed **read-only** `tools/development/shadps4_x11_postmortem_20261026.py` with the prior tag, which uses the console file timestamp to search the exact kernel/system/user journal window, Xorg logs, Sunshine processes and coredump metadata. It does not launch or kill any game process, modify any X server, unload Ollama, or change the protected binary. If X11 disappearance is conclusively external, fix host session separately from Vulkan emulation; if the process crashes first, obtain native call stacks before changing shader code.

All results remain in the universal emulator diary; do not discard failed experiments or merge unverified candidates into main.

## X11 postmortem (attached 2026-10-26)

Archive `shadps4-srt-warnfix-20261026-140602-4067061-x11-postmortem.tar.gz`, inspected in full. The on-disk console mtime and 29.191-second duration imply game startup approximately 14:29:41 local time, though the startup second is inferred rather than directly timestamped.

- 14:30:07.975: Sunshine PID 649807 logged `Terminate handler called`, then a cancelled incoming-connection accept. This **precedes** the previous Xorg server's documented exit.
- 14:30:10: `start-headless-x.sh` (PID 646752) logged `xinit: connection to X server lost` and `waiting for X server to shut down`; an X11 client (PID 647700) independently reported fatal XIO on display `:0`.
- Previous `/var/log/Xorg.0.log.old` ends with AMDGPU failed page flips (EINVAL) and `Server terminated successfully (0). Closing log file.` A successful server shutdown does not tell us **who requested** it, or rule out a graphics fault preceding shutdown.
- 14:30:11: systemd journal recorded a new `sunshine-x11-prepare` invocation and Xorg startup, PID 250648. New Sunshine process PID 252862 initialized by 14:30:13 and found a Vulkan encoder.
- The `journalctl -k` query exited successfully with **no matching kernel events** in this selected interval. It cannot by itself exclude all RADV/device issues. Xorg `~/.local/share/xorg/Xorg.0.log` 79-line `/dev/tty0 Permission denied` logs were from **March 15**, and are irrelevant to the October Xorg restart.
- `coredumpctl` is not installed, so no native crash stack could be extracted from this archive.
- During the later postmortem, `:0` was healthy (1920x1080), with Xorg PID 250648 and Sunshine PID 252862 running. This supports successful host display recovery **after** the failed game, not in-game stability.

**New conclusion**: the October 26 29-second candidate lost its actively-used X11 server during a host Sunshine/headless-X session turnover. Thus the final `XIO` and subsequent `Resource deadlock avoided` cannot be attributed directly to the SRT GPU shaders. The order and motive of the *initial* 14:30:07 Sunshine termination are unknown. Do not disable the protective GPU-recovery watchdog or stop Sunshine on speculation. First identify the actor that requested Sunshine/Xorg shutdown by inspecting unfiltered system/user service journal and the relevant host supervisor scripts for 14:29:30–14:30:20, read-only.

No emulator binary, ES-DE installation, SSH session, game saves, or gameplay behavior was changed by this postmortem. Earlier failed SRT/BDA candidates retain their prior independently observed GPU-lost signatures, and dynamic texture/sampler resource discovery remains unresolved.
