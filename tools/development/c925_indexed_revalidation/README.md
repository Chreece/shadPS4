# C925 indexed-table dependency revalidation

This is an **exact-source adapter**, not a replacement renderer and not a main-promoted fix. It modifies six files in the already compiled isolated C925 + PR95 candidate. The actual source is the user-uploaded C925 snapshot, whose typed 2D/3D/Cube renderer and per-permutation Info ownership differ from fork main. Do not replace that snapshot with an older branch file.

## Confirmed source defect

`PatchImageSharp` previously retained the table header source and layout only after successful nonzero capacity recognition. Empty or rejected tables therefore lost that dependency. Moreover, `ImageResource::GetSharp` returns a null representative for indexed images; the ordinary image-specialization path can skip those images. A table's baked descriptor-array capacity was not an independent shader-permutation key.

The adapter retains the real source and host limit even when the current table is unbindable, snapshots its fully bounded capacity, and compares that snapshot independently of the ordinary image-bound bit. The existing per-permutation refresh selects or recompiles an appropriate shader when that capacity changes. Metadata and shader binary cache versions are advanced together and the snapshot is serialized. A bounded log records capacity changes without claiming which shader caused missing pixels.

**Zero remains zero.** There are no guessed record counts, expanded buffers, shader-hash exceptions, per-game renderer options, skipped draws, CPU-ID overrides or synthetic GPU values. Existing nonuniform GPU texture selection, valid samplers, mixed dimensional image routing and source ownership are preserved.

## Limits of the evidence

The seven sampled GoT compute-stage rejects had zero `num_records`. That does not establish that the header was corrupted or that it later became nonzero. Other buffer-specialization changes can sometimes incidentally trigger recompilation. This repair addresses the missing explicit cache dependency; it does **not** prove this was the cause of all missing GoT scenery. A fresh changed-source native run is required.

PR95's uploaded native build passed, candidate SHA256 `75788b566f0b53b1e342d7a0eca121cc034d64eb165e0474884af4d12e998527`. The original protected ELF was unchanged. PR95 itself has no native game acceptance yet. The latest official main checked during preparation was `45837a89a617ac394c1844ec95a28471fb79ec08`; this isolated C925 experiment is **not rebased onto that commit**. Broader integration remains separate.

## Actual local validation

- GCC and Clang C++20, `-Wall -Wextra -Werror -pedantic`: focused capacity/state regression PASS, **91,560 runtime checks** each, plus compile-time assertions.
- Clang address/undefined-behavior sanitizers: same focused regression PASS.
- Exact source transformation: five uploaded C925 files (including the PR95 helper) PASS; serializer changes tested on a small fixture containing the reviewed GitHub serializer anchors, **not the complete host serializer**. Host adapter refuses missing or ambiguous anchors before source mutation.
- Failed-context fixtures preserve every source byte; reapplication refuses; backup/restore fixture returns all six files to their original bytes.
- Runner subprocess success/failure reporting and private save-copy/path relocation fixtures PASS. A real 16-second quiet subprocess emitted a heartbeat at 15 seconds and completed.
- The first local fixture used an incorrect directory root and failed before any test execution; corrected to the actual six-file snapshot and rerun PASS. This was a fixture path error, not an emulator compiler failure.
- **No full emulator build or GPU run of this new adapter has occurred in the model environment.**

## Host execution

Download the three sibling files from one immutable commit, verify their hashes and run `python3 -u run_c925_indexed_revalidation.py`.

The runner verifies and checkpoints the existing native PR95 ELF, checks all source anchors, applies the actual source correction, runs available GCC/Clang unit tests and performs an **incremental** Ninja build with complete failure logging. It prints its stage and evidence directory immediately and emits live progress plus a 15-second heartbeat during quiet child commands. Build failure restores the candidate's previous source and ELF; it never launches a game after a build failure.

After a successful build, it checks X11 without restarting it, copies the user profile/saves into an isolated portable directory without changing GPU or CPU options, enforces 8 GiB free VRAM and launches one menu-only run. It refuses external save roots that cannot be isolated. Only Ollama models with known context and expiry may be unloaded, only below the VRAM threshold, with restoration/expiry handling afterward. It does not stop services or containers.

The test is TERM-limited at 118 seconds with a two-second KILL fallback for its own process group. Screenshots are requested at absolute elapsed times 40/55/85/112; actual timestamps and game-liveness are recorded so a late desktop frame cannot be treated as a game graphics failure. No automatic input is sent. All outcomes are archived beneath `~/projects/shadps4/logs`; raw saves/profile contents and game binaries are excluded from the evidence archive. The original executable, launcher, SSH, Sunshine and display configuration are not rewritten.

The shared engineering journal is `docs/emulator` on fork main. The reviewed PR95 build is indexed in its README. New host outcomes must be reviewed there, including preflight/build failures, runtime crashes and visually unchanged results. This experimental branch does not establish graphics recovery or cross-game compatibility.
