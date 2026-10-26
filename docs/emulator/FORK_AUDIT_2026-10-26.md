# Fork cleanup inventory — 2026-10-26

Snapshot contains **352 branch refs** and **32 open PR heads/bases**. Other simultaneous work may change these numbers; refresh at actual deletion time. Machine manifests live in [branch snapshot](BRANCH_SNAPSHOT_2026-10-26.json) and [open PR snapshot](OPEN_PRS_2026-10-26.json).

Latest upstream main: `2cdefaa1298c6e4ece6c74bd84e90f3906282d51`. Fork main pre-diary: `01beb8e0fbbea4462bbc661f0019624e959c53dd`; diverged 84 fork-only / 159 upstream-only commits. The native build branch (a82db59) is three upstream commits behind. Remote `sync/upstream-main-2cdefaa-20261026` preserves the upstream base reference; **no tested rebase/merge of main has been completed**.

**Protected branches:** `main`, `got/proven-progress-20261026`, `gow/proven-progress-20261026`, `maintenance/got-experiment-diary-20261026`, `sync/upstream-main-2cdefaa-20261026`, and every live PR head/base. [#63](https://github.com/Chreece/shadPS4/pull/63) native-only is compile-verified but runtime-pending; [#60](https://github.com/Chreece/shadPS4/pull/60) kernel hooks has native regressions + link but earlier translated runtime crash; [#52/#57/#64](../got/EXPERIMENT_DIARY.md) preserve GoT capabilities pending selective main-targeted port.

**Safe pruning gate:** a branch may be deleted only after confirming it has no unique unrecovered work relative to proven main or a fully protected PR, no active worktree/process, exact SHA unchanged since inventory, no protected baseline, and its rejected results in a permanent diary. Preserve full SHA manifest before cleanup. Dry-run first. **Nothing is deleted solely to reduce branch count.**

**Merge/rebase gate:** Build a disposable worktree from fresh upstream; carefully replay the current fork's *proven* fixes, excluding game hacks, rejected work and unverified source ancestry. Run native compile, core regressions, 118+2 capped GoW/GoT, splash/quit, and publish a small reviewable main-targeted PR. Never force-overwrite main or call an unmerged draft a fix.
