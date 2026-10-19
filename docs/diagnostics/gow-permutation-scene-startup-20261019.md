# GoW Ragnarök — 21:31 scene probe startup regression

Date: 2026-10-19. Game: CUSA34384. Trace candidate only; **no conclusion about shader-permutation graphics benefit**.

## Evidence from uploaded runtime archive

Archive: `gow-permutation-scene-20261019-213120-634583.tar.gz`. The launched executable is the isolated full-link diagnostic at `GodOfWarRagnarok/tests/permutation-scene-20261019-201332/build/shadps4`, SHA-256 `a6d600dac85bea7487025fa6d7d2838c69e713e37be4ea303ee8175b413cb00f`.

- Launch environment includes `SHADPS4_PERMUTATION_TRACE=1` and `SHADPS4_RUNTIME_LOG_FILTER=*:warn Lib.AudioOut:info Lib.Audio3d:info`.
- X11 window `0x60002f` was detected. Automated Continue menu was **not** found; `cross_at`, `forward_at`, `right_at`, `world_ready_at`, and `cutscene_at` are all null.
- Execution lasted **7.5 seconds**; there are no FPS/audio production samples. `PERM_TRACE_ACTIVE/CREATED/REUSED` counts are **0/0/0** (GetProgram not observed before the failure).
- The final console reports `SignalHandler: Unreachable code!` and `Unhandled access violation at code address 0x154d284: Write to address 0xac`.
- Near the fatal error, the guest tried to open `/app0/exec/wad/orbis_le/sys_files.texpack.toc` and `/app0/exec/wad/orbis_le/sys_files.lodpack.toc`, then called Audio3D/AJM termination routines.
- A previous read-only game-file audit (`tools/development/gow_game_file_error_audit_20261018.py`) listed the same optional-candidate TOC failures in 19:05 gameplay. **Their appearance alone is not proof of incomplete game files or causal involvement in this crash.**
- The trace test's own report confirms the candidate and protected binary SHA-256 values unchanged, original saves isolated, no recorded Vulkan device loss, and Ollama untouched (19.527 GiB VRAM free at launch).

## Interpretation and next gate

The shader-ownership issue still has a passing synthetic before/after behavioral replay, four passing real C++ object compilations, and a passing full isolated link. But the single game run **failed before shaders could be traced**. No permutation scene-relevance or missing-graphics fix has been established.

**Do not repeat an unchanged scene probe, discard the stable GoW binary, disable a video feature as a workaround, or blame `.toc` ENOENT without comparative evidence.**

Run `scripts/gow_permutation_startup_regression_audit_20261019.py` offline against the failed archive and known completed 2026-10-18/19 GoW gameplay archives. Compare startup event ordering, whether healthy logs also report the same ENOENT, and whether the faulting guest PC can be mapped to a readable ELF segment. That tool does not execute a game, access savedata contents, or modify Ollama/SSH/Sunshine.

Only after the first concrete divergence is established should a new build or single scene run be considered.
