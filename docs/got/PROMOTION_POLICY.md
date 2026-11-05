# Promotion contract: GoT tree -> main

The user's definition of a proven emulator fix is **general correctness verified by native behavior and regression checks**, not merely improved screenshots. Accept a correct GPU-descriptor binding, image-transfer, safety or launch fix even if it does not restore GoT terrain. **Do not** accept changed warning counts across different shader workloads as proof.

## Worktree and branches

- `main`: shared emulator, clean general changes only.
- `got/proven-progress-20261026`: persisted GoT development tree, accepted candidate scripts/diagnostics, forward-ported controller GUIDE quit support, no merging of rejected PR #58 or #61.
- Native user-local worktree source `.../GhostOfTsushima/fresh-source/got-indexed-capacity-20261024-213908` at detached SHA `c925523...` is not a public GitHub commit, and its exact shader files **differ** from online PR heads. A GitHub PR that only contains a patcher script is not automatically the compiled source. Preserve that distinction.
- Prefer a separate `integration/main-<fix>` branch from latest main for every complete generic implementation; deliver a small main-targeted PR plus tested binary and regression log. Avoid merging the 267-ahead/83-behind GoT feature ancestry wholesale.

## Mandatory acceptance checklist

1. Identify exact user-local preimage blobs and GitHub target blobs; refuse unknown local modifications.
2. Make a minimal emulator-wide fix, no title IDs, shader hashes, CPU-substituted GPU indexes, fake textures, skipped draws or ungrounded readbacks.
3. Preserve the real loading splash path and controller GUIDE -> confirmation/cancellation -> SDL quit; source audit and actual controller acceptance should both be recorded.
4. Always use `timeout -s TERM -k 2s 118s` for the emulator **game runtime**; compilation may run beforehand. Do not terminate SSH; keep at least 8 GiB free GPU memory as feasible; don't change saved settings.
5. Compare previous same-scene snapshots and check GPU device loss, build warnings, shader cache version, real resource bindings, bad writes, and regressions in other games.
6. Retain the known-good candidate and put failed trial archives in the diary before cleaning inactive builds.
7. Main merge only after source-level integration and native regression validation. A visible graphics change is **not** required if a correctness fix has been proven and does not introduce regressions.
8. Record main commit, GoT commit, candidate SHA256, evidence archive and next experiment in the diary before marking complete.

**Current blocker for direct main merge:** public `main` and tested GoT branch diverge on dozens of core files. The PR #52/57/64 hardware tests use an exact unpushed local source. A wholesale PR merge would import far more than the proven changes, including rejected experiments. First create isolated main-targeted forward-port PRs; do not claim that the patcher-only PR was compiled as-is from GitHub source.
