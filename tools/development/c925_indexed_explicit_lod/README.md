# Bounded GPU-indexed explicit LOD sampling for exact C925

This directory implements a new **generic shader capability**, not another readback/stencil/cache replay: GPU-selected normalized, non-depth, non-array 2D `SAMPLE_L` and `SAMPLE_LZ`. The captured C925 indexed eligibility helper rejects explicit mip selection and level-zero opcodes; its indexed backend has implicit/gradient sampling but no explicit-LOD IR operation.

`apply_indexed_explicit_lod.py` is an eight-file, SHA-checked production source adapter. It adds `ImageSampleExplicitLodIndexed` to IR and SPIR-V emission, preserves the original GPU texture ID and mip operand, and uses the existing real uniform/indexed sampler and out-of-range null slot. It retains zero-record/capacity/mapping checks. SAMPLE_LZ uses the opcode-defined zero produced by existing lowering, not an invented mip workaround. Unsupported depth, gather, array, r128, unnormalized, clamp and contradictory Bias/Grad combinations remain excluded. Previously supported implicit/gradient mode combinations are unchanged. Existing mixed 2D/3D/Cube paths and restored PR69/68 renderer files are retained. Shader binary and metadata versions change together to reject stale serialized variants. Bounded `IndexedExplicitLodBound` messages mean successful compile-time lowering, **not executed GPU sample proof**.

## Why the runner has a no-build gate

The shared history records many compute resources rejected for unsupported sample modes, but it does not prove which ones are within this new subset. The four uploaded compute shaders (`04950cf6`, `55d17c16`, `52b6e003`, `5c4c2b6f`) do **not** demonstrate a newly eligible indexed-2D explicit-LOD case: the first three use gradient samples; the fourth's non-array LZ resources are already flattened or have a different raw source, and its array LZ cases remain unsupported. The seven zero-record 536-byte tables are **not fixed by this change**.

Therefore `probe_indexed_lod.py` first reads existing pre-resource-patching IR dumps only for compute shader hashes actually present in the latest run's unindexed provenance reports. It requires new L/LZ flags, eight unresolved buffer-read image words from a consistent four-word header, a supported strided GPU address pattern, and complete descriptor placement. This is IR eligibility, **not proof of runtime nonzero bounds or sampler validity**; the actual binder retains those checks.

**No matching IR -> no source patch, no compile, no game launch.** The script instead archives a bounded set of relevant existing IR plus its analysis. It does not turn on dumping or repeat an unchanged menu run. With a match, it checkpoints the accepted native ELF, applies the real source correction, runs tests, incrementally builds and performs one bounded menu-only test after safety checks.

## Validation actually performed

- GCC and Clang C++20 `-Wall -Wextra -Werror -pedantic`: **232,386 checks PASS** each. All 8,192 flag combinations retain 32 old allowed patterns and add exactly 16 explicit patterns (48 total).
- The fixture executes the **actual extracted new emitter and existing sampler helper** against a recording context over 24,000 index/capacity/sampler/LOD/offset/result combinations. It checks preserved IDs and operands, NonUniform decorations, real sampler choice and null sentinel bounds. It is **not a complete SPIR-V validator or Vulkan execution test**.
- Clang ASan/UBSan: same 232,386 checks PASS.
- Mechanical Git patch forward/reverse and byte restoration PASS; exact source preimages checked before all writes; repeated apply refuses.
- IR probe: **10 unittest cases PASS**. Local mocked host runner: **6 cases PASS**, including no-match/no-mutation, successful conditional run, compile-failure rollback, changed ELF/source rejection, and external-save-root rejection. Mocked executions are not hardware tests.
- Initial synthetic IR parser fixture failed because it assumed whitespace before `=`. Corrected the parser to accept optional whitespace; all probe cases then passed. No user-host source or native build was affected.
- **No native full-emulator compilation, complete SPIR-V validation, GPU execution or recovered pixels for this new operation has been demonstrated.**

## Protected baseline and execution

Verified native baseline ELF: `18ed1b6f18a4cc9b466d4bc2a56e9a35dfd04bd0cafe54fa2d9714112d57d2e4`, from `got-c925-accepted-renderer-20261028T144320763760Z`. The runner checks the retained shader/cache and restored renderer manifests, saves a separate checkpoint, and never replaces the protected original executable or launcher. Build/test failure restores the candidate source and accepted ELF. Complete build logs and visible progress/heartbeat remain enabled.

The unchanged runtime helper is `tools/development/c925_indexed_revalidation/resume_c925_revalidation.py` at immutable commit `87d3eeacf828b0f990b6aeb11b3b5da46f1b0792`, SHA256 `df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250`; download it beside these scripts. It supplies the tested proxy-free/Docker-aware Ollama transport, conditional 8 GiB headroom, same-model context/expiry restoration, 118-second TERM plus two-second KILL for only the owned game, absolute-timestamp screenshots and no automatic input. Original saves/config, Sunshine/X11, SSH, GoW binaries and ES-DE are not rewritten. Profile reuse is the existing isolated copy.

Evidence: `~/projects/shadps4/logs/got-c925-indexed-explicit-lod-*.tar.gz`. It includes either source-probe-only evidence or the exact source patch, test/build logs and changed-shader runtime capture. At most eight selected shader IR files and 48 MiB of IR are copied locally; no raw game shaders, saves or ELF bodies are committed to GitHub.

This source is the unpublished local C925 lineage plus accepted changes, **not fork main**. Latest official main checked was `ce74386283f559f2279a7c31416bcdc9359be0b3`; no latest-upstream rebase or common-main renderer promotion is claimed. The adapter is reviewable engineering work, not a completed main C++ merge. Keep the draft separate until real matching-path native evidence and a selective integration are available.

The canonical shared journal is `docs/emulator` on fork main. Its PR97 search includes both historical JSONL and reviewed addenda. Do not repeat the failed Linear8 matcher, readback toggle, zero-stall SRT-parent diagnostic or unchanged PR96 run; this change is a different missing operation and is gated on evidence that it is relevant.
