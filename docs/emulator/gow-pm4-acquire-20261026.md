# Universal GPU AcquireMem — next graphics-correctness candidate (2026-10-26)

## Exact evidence from fault-ring trial

- `shadps4-srt-faultring-20261026-182400-2076676.log` and matching runtime/build archives, native source `877ae834841aaf0ebbed26f8cabff8aa41f1e6b2`; binary SHA256 `d57d6cd5cf3b76e15264581e99dad57ab55741a974973fbf8d9054bb5ded97ed`.
- 29/29 tests passed, 0 warnings in **incremental** build only. The observer ended the game at ~120.364 seconds with SIGKILL (-9) at test limit, not a natural game exit. Ollama snapshot/context restored. Both protected binaries, ES-DE, saves and SSH unchanged.
- Native gameplay screenshot PNGs at 58 and 104 seconds are SHA256-identical (each 390 bytes) and contain **only all-black pixels** at 1920x1080. The archive does not prove what the guest game's framebuffer contained; external X11 capture may also be black. Console records `STARTUP_UI event=shown elapsed_ms=1745` but no `first_game_frame` or `visible_game_frame` event.
- Runtime counts: `FindILsb32` unexpected=0; dynamic host-SRT `Phi`=3906; failed walker offsets=5591; unexpected `ReadFirstLane`=1616; unexpected `GetAttributeU32`=68; invalid sharp=19; shader GPU DMA=36. Same as the prior 120s baseline within scope. Ring saturation=0, ring recovery=0, deferred submission total=0: **new ring code was not exercised**. Source safety fix retained, no claim of gameplay improvement.
- RADV driver reports guilty context soft recovery and `X connection to :0 broken` only at the forced shutdown. The filtered `DeviceLost=0` count does not mean no GPU failure.
- Selected bounded console excerpt contains **137** repeated `BindBuffers` messages like `Clamped size from 4294967295 to 782641088`, across multiple shaders. This indicates massive/broad declared V# ranges and possible residency pressure, but no measured resident GPU allocation size from this archive. Do **not** impose arbitrary binding caps or shader ID exceptions. Separately audit correct guest descriptor range semantics and actual mapped memory.
- The shader's GPU first-touch `ReadConst` has a zero-value fallback for uncached pages, and deferred GPU residency cannot undo writes by an already executed compute dispatch. This is an independent correctness defect, but no first-touch fault count was measured in this run; do not claim it caused this specific absence of game video.

## Concrete next source omission

Both the graphics and compute `PM4ItOpcode::AcquireMem` cases in `src/video_core/amdgpu/liverpool.cpp` were **unimplemented no-ops**, meaning PS4 guest GPU memory acquire/visibility commands were dropped instead of translated into Vulkan synchronization.

New generic source candidate (unvalidated): add `Rasterizer::AcquireGuestMemory` which closes dynamic rendering before recording a Vulkan synchronization2 `vk::MemoryBarrier2` using `ALL_COMMANDS / MEMORY_WRITE` -> `ALL_COMMANDS / MEMORY_READ | MEMORY_WRITE`. Use this from graphics and compute PM4 decoders with the guest `cp_coher_cntl` value for diagnostics. Record bounded in-run activation counts (first / every 1024 calls) to avoid giant logs. No host waits, guest title IDs, shader IDs, forced readbacks or fake GPU values. The conservative full-resource barrier may cost performance; compare actual timing and screenshot result against 120-second baseline.

Four new unit tests validate the Vulkan barrier scopes; total expected focused regressions = **33** (25 proven + 4 fault-ring + 4 PM4 acquire). Native precompile first the graphics rasterizer and AMDGPU Liverpool objects, then run the bounded 118+2s real game test only after all tests and full link pass.

**Outcome of this new experiment: PENDING BUILD & GAMEPLAY.** Do not claim any Vulkan barrier has activated or game frames were recovered until the next game trace and screenshots are reviewed. The shared machine-readable trial ledger stays in `~/projects/shadps4/diary/trials.jsonl`; the central GitHub diary `docs/emulator/universal-experiment-diary.md` must include all passes, failures and rejected changes.

## Tested outcome — 20261026-184642-2262741

- Source `7fb645ac3b2e7712e94242b24067e2afd912a717`, linked candidate SHA256 `0ae62092f2debd2508c0c12ba0ade449835c0607b18c2d9ae8fb21132c090d0b`. All **33/33** universal regressions and native link passed. Build warning count zero is scoped **only** to incremental build.
- **One actual** PM4 AcquireMem Vulkan barrier marker observed: `cp_coher_cntl=0x80000000`. This confirms an executed queue-acquire code path, not any game visual result.
- Game ran 120.317s and was forcibly ended at observer cutoff (`-9`), no natural exit or gameplay. No first/visible game frame events; at 58 and 104 seconds external X11 captures were identical all-black 1920x1080 images (390 bytes each, exact same SHA256 as prior fault-ring test). RADV reported guilty GPU context soft recovery on shutdown; read-only kernel journal matched zero GPU events.
- Runtime full-console counters: dynamic `Phi` 3906, SRT offset misses 5591, `ReadFirstLane` 1616, `GetAttributeU32` 68, unknown sharp 20, on-demand DMA shaders 36, `FindILsb32` unsupported 0; VSharp 4GiB declared ranges clamped 172 times, zero logged first-touch GPU page loads, zero fault-ring deferrals.
- **Keep** new PM4 memory ordering as a real generic shader-data visibility correction with separate verification needed for GoT. **Do not promote as gameplay repair**; no first real Ragnarök frame has been observed. Reevaluate the unimplemented compute queue register protocol and GPU descriptor flow using exact guest packet semantics rather than storing arbitrary queue offsets or inventing selected Phi values. Failed and no-progress outcomes must remain in the universal diary.
