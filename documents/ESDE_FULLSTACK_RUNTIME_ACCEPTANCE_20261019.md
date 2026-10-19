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
