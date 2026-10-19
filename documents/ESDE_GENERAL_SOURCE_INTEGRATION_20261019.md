# ES-DE general shadPS4 — actual source integration stage

**Date:** 2026-10-19. **Status:** Integration code committed; server-native
source merge, compile and gameplay have not yet run for this stage.
This is not a release or a declaration of game compatibility.

## One emulator, no title/shader hacks

The build starts from the SHA-pinned existing unified upstream+GoW checkout:
`~/projects/shadps4/ESDE/fullstack-audit-20261019-165723/source`,
with its already compiled SHA-256
`48ae4ac2ec8f42e2155425521c8699359b466dd6459905948762de539865b5bc`.
The normal ES-DE core is **not modified**.

The engine-side implementation lives in
`tools/development/esde_general_engine_source_merge_20261019.py`.
It composes sources using **real Git three-way merging**, never wholesale
`-X ours`/`-X theirs`:

1. Reconcile the previously world-visible GoT 2026-10-17 generic
   shader/GPU source commit
   `e9f470b359845a456139a865aa1d7b3001f0dbfd`
   against the proven GoW/upstream history (common base
   `32021e41bc52f744c80d122e732e24e97496c98d`).
   This stages proper resource discovery, nonuniform image IR,
   shader compiler, guest T# arrays, general texture cache and
   Vulkan bindings—not per-title engine branches.
2. Separately replay **84 identified later general engine commits**,
   first-parent-delta-by-first-parent-delta, from the early world-visible
   branch through the bounded-table source head
   `71faef4a8d76282a1931445198571082f0e3c70e`.
   This includes complete GPU-indexed T#/S# resources, sampler pairing,
   bounded pointer-selected images and buffers, 27K texture-table lifetime,
   image-view capacity, packed and strided material specialization, real
   GPU attribute selectors, GCN Phi/LDS behavior, indirect GPU buffer
   read hazards and cached resource-table bounds.
3. **Deliberately exclude** diagnostic-only commits, per-fragment
   indexed sample-feedback instrumentation, per-shader hash rules,
   `SHADPS4_GOT_*`/`SHADPS4_GOW_*` switches, hardcoded
   `CUSA` branches and unknown resource fallback values.
   Per-shader IDs may appear in *captured log evidence* but not as
   production behavior switches.
4. Walk project-local include/header dependencies. Keep current upstream
   changes whenever both sides merge cleanly. If a source conflict is
   semantic, retain the complete conflict, stop before changing any source,
   and report it—never pick an arbitrary side.
5. Check the already-proven GoW deferred UFFD callback, pending invalidation
   drain, memory ownership, GnmDriver/Presenter teardown order, and
   write-extent semantics before and after the integration. **Any
   previously true contract that becomes false is a failed merge.**
   If a prior GoW write-extent implementation was already absent from
   the previous unified source, it is reported as an outstanding
   integration requirement. It is not silently called fixed.
6. Compile and run **five actual native C++ regression suites** on the
   merged code (PM4 register windows, indexed T# table, GPU image selector,
   strided material specialization, SRT provenance). Only after these
   pass does the script use the retained Clang19 Docker toolchain to
   rebuild and link shadPS4, verify ELF dependencies and run its CLI smoke.
7. Preserve the previous candidate ELF and all source preimages in the
   isolated `general-engine-merge-<timestamp>/` project directory.
   Neither the installed ES-DE binary nor protected GoW/GoT game source,
   saves, Ollama, Sunshine or SSH are modified.

## Evaluation, not compilation alone

A full compilation does not prove the correctness of guest graphics.
Promotion requires a single *new* general binary to reproduce the previously
rendered GoW menu/characters, make real improvement over the Ghost black
screen and preferably return to the horse/rider/terrain baseline, retain
reasonable audio, and exit through Home/PS to ES-DE. No automatically
selected per-game executables or runtime preset switching.

Run:
```bash
python3 -B ~/projects/shadps4/scripts/esde_general_engine_source_merge_20261019.py --self-test
python3 -B ~/projects/shadps4/scripts/esde_general_engine_source_merge_20261019.py --build
```

The script emits one
`~/projects/shadps4/logs/esde-general-engine-merge-<timestamp>.tar.gz`
containing source conflicts, native test errors, build diagnostics and a
bounded exact applied-source diff. Any unhandled source overlap is a concrete
required code review, **not evidence that another game-specific workaround
should be added**.

This is an **unreleased integration candidate**, not a promise that all
previous experimental changes are correct or complete. Only general,
validated semantics belong in production.

## 22:03 regression: retired old entry point, reconciled latest upstream

The uploaded `esde-unified-20261019-220318.log` shows that the October 18
`tools/development/esde_unified.sh` stopped at
`UPSTREAM_MAIN_CHANGED`. Its only proposed source was official
`cd3cc8022aef5be7766443f694e79d270544eaf4` with fork main from
October 5, which **omitted** later proven general CPU/UFFD/renderer fixes.
No merge, compile, or install occurred in that log.

The historical entry point has been retired on the integration branch.
Running `tools/development/esde_unified.sh` now downloads a
SHA-verified exact version of
`esde_general_engine_source_merge_20261019.py`, runs its offline
merge-policy selftests, and enters the general engine source integration
workspace. It does not create another obsolete October 18 clone.

Official upstream at the last confirmed October 19 check was commit
`f68923fe807f657348e6719ce658bcbd0a2b1c72`.
It is five commits beyond the earlier compiled general-source baseline
`d615b3887ba29025a59242e1a26f5b68bb176158`, containing:

- `#5433` texture-cache parent/subresource preservation;
- `#5429` singleton LDS workgroup barrier correction;
- `#5427` avoid treating `GetUserData` as a Phi-folding common expression;
- `#5421` `libSceVoice` disconnected microphone HLE behavior;
- `#5437` `sceNetGetSockInfo` receive queue length.

The general engine stage fetches exactly that reviewed official
revision and uses source-level three-way merges against its real earlier
upstream parent. Its changed files are reconciled **before** the
October 17 Ghost general resources and the 84 subsequent reviewed
GPU/compiler commits. Every unresolved overlap must be reviewed,
not resolved by choosing the entire old or new file.

GitHub Actions was also changed to run syntax, source pins and offline
merge regressions for this integration branch on push. It does **not**
claim CI native gameplay or an installed executable; the latter
requires the user's Debian/AMD test host. No release is claimed from the
22:03 log or from these source-wrapper changes.
