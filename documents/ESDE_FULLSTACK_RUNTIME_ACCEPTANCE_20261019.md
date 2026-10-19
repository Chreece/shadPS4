# Unified shadPS4 full-stack acceptance — 19 October 2026

## Built candidate (not promoted)

The host archive `esde-fullstack-srt-resume-20261019-182344.tar.gz` records
`PASS_COMPILED_ONLY`. The complete C++ object build, native link,
ELF/dependency checks and CLI `--help` smoke pass. This is **not** evidence
that God of War Ragnarök or Ghost of Tsushima boots.

- Executable: `~/projects/shadps4/ESDE/fullstack-audit-20261019-165723/build/shadps4`
- SHA-256: `48ae4ac2ec8f42e2155425521c8699359b466dd6459905948762de539865b5bc`.
- Working GoW A/B reference (remain intact):
  `8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef`.
- Prior installed Oct 18 ES-DE executable (remain intact):
  `d8ef5b2ae1b12464a5d4ca83d8e5e9c3b0f2f0053148c531eda7457c9c21117d`.

The fixed SRT `srt_offset_preflight.h` came from an existing protected
GoW local source at SHA-256 `4b7b8a6e4422d1620c3c2910e94554bb3fa99a4e05d4ee82bb7ee9a43b88ae1b`;
it was intentionally not replaced by the older GitHub snapshot.

## Runtime testing

`tools/development/esde_unified_game_observer_20261019.py` is a
small title-parametrized adaptation of the prior GoW observation runner.
It accepts `--game CUSA34384` or `--game CUSA11456`, checks a
specified candidate SHA, refuses to launch over an existing emulator,
logs guest crashes, captures CPU/GPU-UFFD deadlock evidence and
preserves the live game process for manual Home/PS exit. It must not
be run against the user's *production* game data if strict
save-data isolation is desired; use a separate portable `user/` directory
under `--cwd`.

For both games:
- Use the **same** built candidate and
  `--cpu-id-mode builtin --userfaultfd` (those options were confirmed
  in the pinned Linux `src/main.cpp`).
- Record whether the process remains alive after 45–180 seconds,
  module loading, guest window progress, GPU device loss and host
  stability. A process being alive is not a visual acceptance pass.
- GoW: compare an actual rendered menu, characters, geometry and
  sound against the saved fullgfx checkpoint. Audio stutter remains
  known and unresolved.
- Ghost: confirm the initial module-start null-read is gone, then
  observe menu/world geometry and visible horse/rider. Shader compilation
  alone, a white menu, or a partly moving terrain is insufficient.
- Controller Home/PS quit confirmation and clean return to ES-DE are
  separate manual acceptance gates.

Only after at least one comparable live scene in BOTH titles should
the new core be considered for selection by
`esde_unified_select.sh`. Keep the previous launcher, backup, saved
game builds, and never replace a live process.

## Pinned isolated test launcher

`tools/development/esde_fullstack_game_playtest_20261019.sh` can be
downloaded together with the generic observer from the integration
branch at commit `0e871e3edf115d9cbb89f97c52ed97ae81673407`.

Run `bash .../esde_fullstack_game_playtest_20261019.sh CUSA34384 180`
for GoW first, and only after it exits and its game process is closed,
`bash .../esde_fullstack_game_playtest_20261019.sh CUSA11456 180`
for Ghost. The launcher:
- Checks the built candidate SHA-256 before execution.
- Refuses another active emulator/ES-DE or an unavailable X11 display.
- Requires 8 GiB currently free VRAM on the AMD GPU; **does not stop Ollama**.
- Clones the existing user profile to
  `~/projects/shadps4/ESDE/playtest-<CUSA>-<timestamp>/user/`,
  skipping stale shader caches and logs. No symlinks are copied; a
  strict 2 GiB copied-profile limit prevents unexpectedly large clones.
- Clears guest home, firmware/font and add-on override directories in
  the **cloned** `config.json`, leaving original saves and settings alone.
- Preserves the original process if it's still live at the end of the
  observation; it never kills a game to start another test. Use Home/PS
  to quit normally.
- Writes runtime evidence under the project `logs/` folder and prints
  `UPLOAD_THIS=...tar.gz`.

The general-purpose executable has **not** yet passed either game's
live visual acceptance. An audio-log line, process-alive status or
shader-pipeline count alone is insufficient for promotion.

## October 19 18:51 / 18:50 attached runtime evidence — regression

Both files were inspected locally from their uploaded tar archives, including
`full-console.log`, `gameplay-result.json`, and the observer errors:

**CUSA34384 GoW Ragnarök**

- Current unified ELF SHA-256 `48ae4ac2ec8f42e2155425521c8699359b466dd6459905948762de539865b5bc`.
- Exact command: `--cpu-id-mode builtin --userfaultfd --game CUSA34384`.
- `Game:Main` SIGSEGV, exit `-11`, writing `0xac` at guest code address
  `0x154d284`. The process exited before the first 15-second sample.
- Prior to the failure, numerous `Root` named 2-MiB direct-memory mappings,
  then `sceAudio3dTerminate` and unsupported `sceSysmoduleUnloadModule`
  for audio3D. That teardown may be a consequence of earlier failure, rather
  than the primary defect. No faulting C++ call stack was captured.
- The log also reports missing `sys_files.texpack.toc` and
  `sys_files.lodpack.toc` lookups, and a dummy
  `__sys_regmgr_call` which leaves its output unchanged. Neither is
  independently proven causal.
- No Vulkan device-lost condition is reported. Do not target GoW's renderer
  or sound backend as a proven cause of this *early* fault.
- **Source provenance gap:** the previous audit marked the last
  fully rendered GoW renderer source fingerprint
  `bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a`
  missing from both currently modified local worktrees, but then allowed
  a non-proven renderer into the combined build. That explicit omission must
  be investigated separately from early CPU/HLE abort.
- First controlled falsification: launch the SHA-pinned previously rendered
  GoW executable `8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef`
  with the **same isolated user/config copy** that crashed with new unified.
  If it also faults, investigate profile/assets and host changes; if it
  reaches its old scene, this is a binary/source regression.

**CUSA11456 Ghost of Tsushima**

- Full process stayed alive beyond 120 seconds and ultimately exited with
  code 0 after the Home/PS exit was accepted (the observer erroneously reports
  `GAME_EXITED_EARLY_WITH_CODE_0` as failure because its full observation
  period was not completed).
- The game's soundtrack and shaders advanced, but the user saw a black
  screen after logos; no evidence of Vulkan device loss or segfault.
- Material compute shader `0x5c4c2b6f` still reports failed
  SRT walker offset calculations and GPU/DMA fallback. Other DMA fallback
  shaders include `0x2ba74851`, `0xa4f19a`, and `0x8ced785b`.
  The log also has repeated ~4GiB descriptor size clamps.
- The last earlier Ghost source producing visible horse/rider and partial
  world is `e9f470b359845a456139a865aa1d7b3001f0dbfd`, documented
  in `docs/diagnostics/got-visual-baseline-20261017.md`.
  Its GPU-indexed sampled images/real guest T# table and binding
  improvements are not simply equivalent to the limited SRT helper
  and shader modules in the general merged GoW branch. However,
  later fragment-feedback instrumentation caused visual regressions,
  so do **not** promote the whole newer experimental branch.

### Source-proof recovery procedure

`tools/development/esde_fullstack_regression_triage_20261019.py` is
the paired, read-only audit. It:
1. Searches only existing local GoW project source files and reachable
   Git history for the exact missing renderer SHA, without overwriting any
   working tree or guessing a source replacement.
2. Fetches the pinned **pre-feedback** Ghost world-visible Git object
   into the existing isolated source's object database and runs a
   Git `merge-tree --write-tree` conflict preview, **not** a merge.
   The source tree, installed core and executable remain unchanged.
3. With `--reference-gow`, tests the already saved GoW
   `8b0cbcba...` binary with a new copy of the same portable
   user configuration used for the failed unified GoW run. It refuses
   to start with under 8 GiB free RX 7900 XTX VRAM, any active emulator,
   missing X11, or altered binary SHA; never stops Ollama.
   The existing generic observer records process exit and UFFD/GPU
   observations. Manual gameplay/visual comparison still matters.

This is a **regression isolation and source recovery gate**; it does not
yet establish that either missing implementation can be transplanted into
a single upstream main build without breaking the other title. There has
been no new native build, and neither game's runtime issue is claimed fixed.
