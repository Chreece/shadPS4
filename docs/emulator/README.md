# Shared shadPS4 engineering diary — ALL games

**Canonical home:** this directory on fork `main`. Keep **one universal history of every attempted emulator change**; separate game notebooks are *views and original evidence*, not competing logs. A correct engine change applies across titles; game IDs only label where behavior was observed. **Do not implement title-specific source workarounds, shader-hash exceptions, readback overrides, or CPU options.**

## Open these before trying any new idea

| Question | Definitive file |
|---|---|
| Have we already tested or rejected this? | [TRIALS.jsonl](TRIALS.jsonl) — **append-only** attempts across GoW, GoT, PES, TLG and future games, including build errors and diagnostic failures |
| What fixes are kept, rejected, merged or still awaiting validation? | [FIXES.json](FIXES.json) — derived 19-subsystem/fix status map, PR references and the next falsifiable tests |
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

[GoT progress](../got/EXPERIMENT_DIARY.md) — `got/proven-progress-20261026`. [Ragnarök progress](../gow/EXPERIMENT_DIARY.md) — `gow/proven-progress-20261026`. Both retain their exact tested source/binary checkpoints and game-specific *observations* while sharing this **one** emulator code/fix history.

Native game tests must use the same emulator semantics (no compatibility overrides), standard fullscreen and FPS, and the strict 118s SIGTERM + 2s SIGKILL limit. Conditionally unload/reload Ollama if GPU VRAM is under 8 GiB; restore the exact previous models/contexts after the actual game exits. Preserve SSH, Sunshine, ES-DE, all saves and known-good binaries.
