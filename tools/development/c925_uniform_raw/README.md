# Preserve raw texture selection in uniform-count loops

This is a rendering/source correction, **not another freeze, timeline, presentation or desktop-capture probe**. Identical sampled menu images do not establish a freeze. The latest progress archive shows continuing submissions, completed waits and flip handling; the shared journal explicitly withdraws the earlier freeze inference. Missing materials and scenery remain the target.

## Existing evidence

Fresh shader IR from the 15:57 archive shows two remaining raw-pointer sampling shaders, 0x58f22286 and 0xc7542465, using pretested scalar induction loops. The bound is an unconditional direct ReadConst from a root SRT; texture descriptor words depend on the loop index, while their sampler words come from actual uniform root reads. Existing final IR loses this selection and emits a fixed null-representative sample. The code below never checks either shader hash; those names identify the inspected private evidence only.

The actual runtime counts and the visible surfaces depending on these shaders are **not yet established**. This corrects an identified translation gap; native output and graphics acceptance remain pending. It does not repair the separate wave-selected raw pointer or the seven genuine zero-record material tables.

## Actual correction

The adapter changes eleven existing C925 production files and introduces one helper. It verifies the real CFG, Phi edges, seed, step, pretest comparison, true/false targets, unconditional root-bound source and genuine sampler source. It reads the actual uniform count through the existing scalar guest-memory/coherence path. Only a proven domain fitting the compiler expansion budget is expanded; the budget is not a fabricated guest allocation size. Larger, empty, unavailable or unproved cases retain the existing path.

Each alternative preserves the original descriptor type, coordinates, LOD and sampler. Selection remains on the GPU. A new guarded explicit-sample opcode puts the actual texture sample inside a conditional branch, not a speculative sample followed by result selection. No unselected texture access is intentionally emitted.

Compiled variants retain the real count dependency, including empty or unsupported counts. Live counts are compared **before the old expanded SRT walker is refreshed**; a changed or reduced count cannot reuse that variant first. The fixed persistent metadata is serialized with bounded count checks, and metadata/binary versions advance together. No cache deletion, forced counts, host-picked lane, title-specific toggle, skipped draws or changed rendering settings.

Existing PR95/96/99/100/102/103 capabilities and restored PR68/69 renderer behavior are retained. This package is an exact-source adapter for the local C925 lineage, **not a completed fork-main C++ integration or upstream rebase**. Latest upstream checked was b6f0c58dba9ee75b93477703032ae4f1e0de8f1c.

## Actual local validation

* Actual planner header against a representative IR-interface fixture: **5,741 checks PASS** under GCC and Clang C++20 -Wall -Wextra -Werror -pedantic.
* Actual extracted root-read/count-dependency methods: **16 checks PASS** under both compilers, including shrinking, zero, unavailable, restored and oversized counts.
* Actual extracted guarded emitter against a recording context: **1,024 cases PASS** under both compilers. Branch placement, selected-only sampling, original operands and returned value are checked.
* All three focused fixtures PASS under Clang ASan/UBSan. These are not full SPIR-V validation or Vulkan GPU execution.
* Ten Python source/runner-component tests PASS, rerun in 1.919s with ResourceWarning treated as error: exact source transformation, changed/repeated refusal, real Git forward/reverse byte restoration, cache ordering/version checks, guarded branch contract, profile read-only/external-save/symlink checks, source+ELF rollback fixture, and real child-process success/failure handling. These are component tests, not a full end-to-end native host execution.
* Initial local transform encountered an ambiguous GetSharp anchor and refused before writes. The anchor was narrowed to the actual atomic-view block; all final tests rerun successfully.
* The private validator discovery pattern was corrected to the already-established spirv-tools-private/unpacked tool location before publication. No host command used the earlier local pattern.
* All four published source blobs match locally tested bytes.
* **Full native emulator compile, new generated SPIR-V validation, runtime binding and visible recovery are pending.** No main promotion or kept-runtime claim is made for this new change yet.

## Host execution

The one-paste runner requires restored PR103 ELF b572e6f78facd0175b4a18ca3074909c91e6c2923c2cfe6398d40912a021d641, the latest progress trial's restoration record, its accumulated source manifest, and all eleven precise preimages. It checkpoints the baseline, runs the focused fixtures, builds incrementally with complete logs and a 15-second quiet-command heartbeat, and never launches after a failed build. On test/build failure it restores the changed sources and baseline ELF and removes the new helper.

On success it performs one menu-only 118+2-second owned-process run, no input, with the existing isolated profile, 8GiB VRAM gate and same-server Ollama context/expiry restoration. Original saves/settings, protected binaries, SSH, Sunshine/X11 and ES-DE remain untouched. It collects fresh relevant IR/SPIR-V, validates those bytes with the already-installed/private validator when available, and records full diagnostics. It neither installs a validator nor classifies identical menu frames as frozen.

Evidence is archived under ~/projects/shadps4/logs/got-c925-uniform-raw-samples-*.tar.gz. Raw saves and executable bodies are excluded. Outcomes, including failed source/build checks and unchanged materials, must be reviewed in the one shared GitHub journal.
