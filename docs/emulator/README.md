# Shared shadPS4 engineering diary — ALL games

**Canonical home:** this directory on fork `main`. Keep **one universal history of every attempted emulator change**; separate game notebooks are *views and original evidence*, not competing logs. A correct engine change applies across titles; game IDs only label where behavior was observed. **Do not implement title-specific source workarounds, shader-hash exceptions, readback overrides, or CPU options.**

**Current evidence (2026-11-04):** Native PR138/139 source builds; GoT reaches visible intro footage at 25s but loses GPU context at 63.272s, so GoW is skipped. This is not menu/gameplay acceptance or a proven causal effect. [Draft PR140](https://github.com/Chreece/shadPS4/pull/140) automatically corrects inactive-source zero behavior for compute DS swizzles, preserving the previous reduction fix and checked upstream. Full local link, exhaustive lane routing and emitted-SPIR-V CPU checks pass; native acceptance is pending. [Evidence and limits](reviews/20261104-native-intro-compute-swizzle.json). Main unchanged; DS seed/re-entry remains unfinished.

## Open these before trying any new idea

| Question | Definitive file |
|---|---|
| Have we already tested or rejected this? | [TRIALS.jsonl](TRIALS.jsonl) — **append-only** attempts across GoW, GoT, PES, TLG and future games, including build errors and diagnostic failures |
| What fixes are kept, rejected, merged or still awaiting validation? | [FIXES.json](FIXES.json) — derived subsystem/fix status map, PR references and the next falsifiable tests |
| Why did that test help/hurt the emulator? | [EXPERIMENT_DIARY.md](EXPERIMENT_DIARY.md) — readable cross-project outcomes; also consult the detailed game-specific source diaries linked below |
| What is allowed to reach our main? | [PROMOTION_POLICY.md](PROMOTION_POLICY.md) — preserve every prior fix, current upstream, native-only CPU and cross-title regression gates |
| What branches/PRs might we lose during cleanup? | [FORK_AUDIT_2026-10-26.md](FORK_AUDIT_2026-10-26.md) and [branch SHA inventory](BRANCH_SNAPSHOT_2026-10-26.json) — historic snapshot, *refresh before deletion* |

## Local history search (any game)

On a clone containing this directory:

```bash
python3 tools/development/query_emulator_diary.py --find "sampler" --all
python3 tools/development/query_emulator_diary.py --find "readback" --rejected
python3 tools/development/query_emulator_diary.py --fix gpu-linear-readback
python3 tools/development/query_emulator_diary.py --game CUSA34384 --all
python3 tools/development/query_emulator_diary.py --game CUSA11456 --next
python3 tools/development/query_emulator_diary.py --next --all
```

The query automatically combines committed historical records with `~/projects/shadps4/diary/trials.jsonl` if present. It never changes the emulator, the repository or game data.

## Record EVERY build, failed attempt and game run in the same file

The evidence recorder is universal; repeat `--game` when one generic build is compared across multiple titles. A selected game is **not** marked successfully launched unless the archive records a launch or a reviewer confirms it.

```bash
python3 tools/development/record_emulator_trial.py \
  --archive "$HOME/projects/shadps4/logs/ACTUAL_RUN.tar.gz" \
  --game CUSA34384 \
  --source FULL_40_CHAR_TESTED_SOURCE_SHA \
  --candidate-sha256 FULL_64_CHAR_CANDIDATE_BINARY_SHA \
  --fix-id native-cpu-id --pr 63 \
  --change "Describe the exact generic emulator change" \
  --emulator-effect "Document actual compiler, GPU, audio, memory, or crash evidence" \
  --decision PENDING_REVIEW \
  --next "State one falsifiable follow-up check"
```

The GoW universal native test in `gow/proven-progress-20261026` invokes this recorder automatically on exit. Other game-specific experimental scripts should be invoked with the shared recorder or have their generated `UPLOAD_THIS` archives ingested immediately, **including rejected runs**. Artifacts stay private on the host; the permanent git diary stores SHA/evidence metadata and reviewed observations, not raw game files.

To record more than one game in one run, add repeated `--game`, `--tested-game` (only actually launched), `--game-result GAME "what was observed"`, and `--visual GAME "what appeared"`, `--audio GAME "what was heard"`, `--fps GAME "same-scene measurement"`. Never infer missing visuals from a build pass.

## Important distinctions

* `FIXES.json` is the **current status view**, not a second chronological history. A status can change only with an appended evidence record and reviewed PR source.
* `TRIALS.jsonl` contains legacy GoW schema v1 records, imported GoT schema v2 records with explicit source/provenance, and any later host-sourced SHA-pinned records. **Never rewrite earlier lines**; append corrections that reference original evidence.
* An archived GoT experiment with no rehashed source archive is explicitly labelled diary-derived, not falsely "byte-verified". A PR description is a hypothesis unless an actual native test is recorded.
* A merged fork-main PR (#59/#66/#68) is a source milestone, not an automatic claim of current native hardware/cross-game compatibility.
* An emulator-wide design is not proven across games merely because one game passed. Keep `context_games` (mentioned/selected) distinct from `tested_games` (actually executed).
* Never repeat rejected full-process DynamoRIO translation, eager tiled GPU readbacks, duplicate dirty-page tracking, SSA-GVN aliasing or per-title GPU configuration without new independent proof and user approval.

## Canonical game progress worktrees

[GoT progress](../got/EXPERIMENT_DIARY.md) — `got/proven-progress-20261026`. [Ragnarök progress](https://github.com/Chreece/shadPS4/blob/gow/proven-progress-20261026/docs/gow/EXPERIMENT_DIARY.md) — `gow/proven-progress-20261026`. Both retain their exact tested source/binary checkpoints and game-specific *observations* while sharing this **one** emulator code/fix history.

Native game tests must use the same emulator semantics (no compatibility overrides), standard fullscreen and FPS, and the strict 118s SIGTERM + 2s SIGKILL limit. Conditionally unload/reload Ollama if GPU VRAM is under 8 GiB; restore the exact previous models/contexts after the actual game exits. Preserve SSH, Sunshine, ES-DE, all saves and known-good binaries.

## Reviewed evidence addenda

These are reviewed records in this same shared journal, linked to their historical ledger record; they do not replace or rewrite earlier GoW/GoT trials.

* [2026-10-28 PR95 native build and silent-runner review](reviews/20261028T112756671630889Z-pr95-native.json): native compile/link and GCC bounds test **PASS**, protected ELF unchanged, **117 build warnings**, **no game run and no verified graphics improvement**. The prior command hid its progress in log files. Preserve this isolated build for incremental work. This adds native evidence to `got-gpu-scalar-vsharp-stride-bounds-pr95-draft-20261028`; no zero-record bypass or main promotion.
* [2026-10-28 PR96 indexed-table source correction and focused local tests](reviews/20261028-indexed-table-revalidation-local.json): retains bounded table-capacity dependencies independently of null image representatives. Local GCC/Clang/sanitizer tests passed; that record predates native acceptance and does not claim recovered scenery.
* [2026-10-28 13:36 UTC PR96 native build and prelaunch Ollama failure](reviews/20261028T133609110195Z-pr96-native.json): **native incremental compile/link PASS**, **91,560 GCC checks PASS**, **zero warning/error lines** in the complete build log; candidate ELF `21a26822390451d4b21bde8feae4712b2bd66bf068096ad246055db914122fde`. Game **NOT_RUN** because the localhost Ollama `/api/ps` request reset while VRAM was 47.168 MiB below the required 8 GiB. No model had been unloaded. Protected ELF and original saves/configuration unchanged. Preserve this built shader correction; resume with local/Docker Ollama access rather than rebuild or reapply the shader patch. No visual or cross-game promotion.
* [2026-10-28 13:52 UTC PR96 actual menu result and preservation audit](reviews/20261028T135245496700Z-pr96-runtime.json): completed bounded capture, **graphics still incomplete**, zero indexed-table revalidations, seven material rejects, 269 SHARP warnings; typed 2D/3D/Cube binding retained, but binding does not prove executed samples. The old stencil-reference warning reaches occurrence 1024 despite previously accepted/merged PR69. Archived C925 texture constructor also lacks PR68; verify host source before restoring. Reject PR96 as demonstrated graphics recovery. Preserve negative evidence and all earlier GoW/GoT records.
* [2026-10-28 accepted-renderer restoration source and local tests](reviews/20261028-accepted-renderer-restoration-local.json): restores exact already-merged PR69 and PR68 semantics to two SHA-checked C925 files, while asserting retention of all six PR95/PR96 shader files. **556,283 actual extracted-code checks PASS** under GCC, Clang and Clang sanitizers; eight source/mock-runner tests PASS. Initial local fixture missing-header failure recorded and corrected. New combined native build/visual result **PENDING**. This is baseline-preservation repair, not a claim that stencil or readbacks alone restore the complete menu; earlier PR70 and readback-toggle negative results still apply.

* [2026-11-01 first real GoW ordered-count checkpoint and operand correction](reviews/20261101-ordered-gds-operand-correction.json): native build of `43b739c1` **PASS**; GoW captured all seven waves then deliberately stopped before continuation. The recovered capture incorrectly stored population count 1 for every wave instead of the decoded ADDR operands (six zeros, one 12). Candidate captures the explicit operand and checks it against the saved source VGPR, rejecting the old ABI. GoT lost its GPU context after 41.586 seconds; its black screen is **not fixed**. PR118 remains draft, main unchanged, guest re-entry still missing. The review separates local CPU/SPIR-V checks, full build status and pending native retest.

The same candidate also fixes a reproduced null dereference in lane-read optimization when the input, or the end of a write-lane chain, is constant. The production pass now passes 12,545 sanitizer checks, including all read/write lane pairs and preservation of distinct Phi predecessors. This is not attributed to GoT's GPU reset.

## 2026-11-02 upstream refresh

The native `7b62b61b` run stopped before building because upstream advanced. The retained patches rebase onto `16157ae6`, incorporating compute-clear component-count checks, the macOS dependency update and VFS cache/directory fixes. Patch preservation is verified; the unlink patch differs only in surrounding context. [Evidence and validation](reviews/20261102-upstream-refresh.json). No new graphics success is claimed; continue using the immutable two-game runner and keep PR118 draft.

The refresh also removes an unused parameter name in the new no-op VFS cache method, fixing the existing strict CPU build without suppressing warnings or changing behavior. Final local Release build, 31 CTest cases, three startup checks and 13 focused unlink cases pass. Native GPU retest is pending.

* [2026-11-02 native DS operand confirmation and SRT ordering repair](reviews/20261102-native-capture-srt-order.json): all seven corrected GPU operands match saved registers; GoW continuation remains blocked. GoT remains black and loses context at 41.413 s. Corrected a separately reproduced non-irreflexive SRT map comparator; 8,076 real-IR ASan/UBSan checks and full native/CPU/startup checks pass on upstream `d4ea43b9`. Native rendering retest pending; PR118 draft and main unchanged.

Publication freshness check found upstream `e25f2d34` after the local validation above. It removes only CI CMake-configuration caching. All three retained commits rebased cleanly; emulator source, build definitions and runners are byte-identical to the validated candidate. The published candidate uses this newer upstream.
