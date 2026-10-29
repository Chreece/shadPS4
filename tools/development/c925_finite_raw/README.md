# CFG-proven finite raw image reads

This is an exact-source adapter for the retained local C925 + PR95/96/99 + restored PR68/69 candidate. It is not a claim that fork main's production renderer is patched, rebased or hardware-validated. Latest upstream main checked: `ca36c7e31e9edb958db8d2b94a0721e58eca9daf`.

## New evidence

`got-c925-remaining-shaders-20261028T155744943557Z.tar.gz`, SHA256 `27cd902b1c89ad964bdad54702b51ca37dd54b14c14307e698ee6846ae9a440e`, contains fresh GCN/IR/SPIR-V for all eight requested shaders. The previous frozen presentation did not reproduce: all four frames changed and CPU/GPU activity continued. Scenery and materials are still incomplete; this diagnostic does not prove the intermittent freeze is repaired.

One remaining raw-pointer image-read chain has an actual two-edge induction Phi seeded at zero, incremented by one at its latch, and a true backedge guarded by `current_i < 5`. Therefore values 0 through 5, including the terminating iteration, form a provable finite descriptor set. Four `ImageRead` instructions use that root-SGPR descriptor pointer, but the existing translation loses the selected source and uses a fixed binding. This source evidence does not identify which visible object uses the reads.

Shader hashes identify private evidence only. The emulator correction has no title/hash predicate or literal six-entry guest table assumption. It checks the actual SSA incoming edges, CFG backedge, current-value comparison, increment and contiguous descriptor words. Runtime-bounded loops and wave-selected pointers remain unsupported rather than being assigned invented bounds.

## Actual production transformation

The adapter checks nine complete source preimages before writing anything, then changes those nine files and adds `finite_raw_image_plan.h`. A compiler expansion budget of 16 alternatives is not a guest allocation length: patterns exceeding it retain the old path.

For a proved range it constructs ordinary static descriptor sources for each possible index, retaining normal descriptor/type/coordinate conversion. The guest loop and its selected index stay on the GPU. Each actual image access is emitted inside a conditional branch; unselected image accesses are not speculatively executed and then discarded. Result selection uses the original GPU index. The new guarded IR opcode is emitted only after ordinary resource discovery and patching, and both shader metadata and binary cache versions advance together.

No changes to the seven zero-record V# guards, existing explicit-LOD capability, typed 2D/3D/Cube routing, genuine samplers, accepted stencil/readback implementations, saved graphics settings or guest input. This does not implement the other three runtime/wave-selected raw chains and does not claim to restore all scenery.

## Local validation actually completed

* GCC and Clang C++20 `-O2 -Wall -Wextra -Werror -pedantic`: actual new matcher header passes **819,474 checks** each against an IR interface fixture. Clang ASan/UBSan passes the same checks. Includes the observed descriptor expression shape, terminating iteration, signed/unsigned comparisons, wrong/backward CFG edges, extra predecessors, next-value rather than current-value comparisons, dynamic bounds, non-unit increments, wave pointers, multiple Phi sources, descriptor contiguity and exact 32-bit arithmetic.
* Actual extracted guarded emitter in a recording context: **16,384 cases PASS** with GCC, Clang and Clang ASan/UBSan. Verifies conditional image access, merge/result shape and preserved handle/coordinates/LOD/MS operands. These are contract tests, **not complete SPIR-V validation or Vulkan execution**.
* Exact source transformation, mechanical Git forward/reverse patch roundtrip and byte restoration PASS. Reapplication and changed source fail before writes. SSA guard definitions are emitted before their cloned users.
* Eight mocked runner tests PASS: success/archive, compile and fixture rollback, changed ELF/source/accepted renderer refusal, external save root refusal, repeated apply refusal. Mocked execution is not a host emulator build or GPU test.
* Local preparation failures retained: ambiguous two-occurrence source anchor refused before writing; strengthened context. Review caught guard ordering and moved its definition before cloned use. IR test scaffolding needed qualified `IR::Type`; emitter test scaffolding needed explicit nested-loop braces for Clang Werror. An initial portable packaging attempt altered embedded C++ indentation; byte comparison caught it and AST-preserving packaging corrected it. All stated tests were rerun afterward. These were not user-host compiler/game failures.
* All four published code files were checked by Git blob identity against the tested local bytes.

**Full native shadPS4 compile/link, complete SPIR-V validation and GPU/menu acceptance of this new transform remain pending.** Neither the host nor this environment has supplied a successful `spirv-val` result.

## One-paste host runner

Download these four sibling files from one immutable commit and the existing `resume_c925_revalidation.py` from commit `87d3eeacf828b0f990b6aeb11b3b5da46f1b0792`; verify their hashes before running `python3 -u run_finite_raw.py`.

The runner verifies current ELF `a293a496f42f7e85ef5e76bf0e25e9f3dccd269adae332e646d2234b378c5216`, accepted renderer hashes and all nine source preimages; checkpoints the current ELF; applies the source correction; runs available compiler contract tests and performs an incremental Ninja build with full failure logging, progress and quiet-command heartbeats. A failed test/build restores the old source and ELF and removes the new header. No game launches after a failed build.

A successful build uses the existing isolated diagnostic profile and its shader-dump setting, unchanged original saves/configuration, 8 GiB VRAM gate, conditional same-server Ollama context/expiry restoration, no input and an owned-process 118+2-second menu test. It captures timestamped images, the new finite-source diagnostics, retained capability events and fresh IR/SPIR-V for transformed shaders. Raw shaders remain private evidence; they are not published to GitHub. Protected binaries, SSH, Sunshine/X11 and ES-DE are not rewritten.

Outcomes are archived under `~/projects/shadps4/logs/got-c925-finite-raw-images-*.tar.gz`. Every native failure, unchanged image, real capability improvement or regression must be reviewed in the same `docs/emulator` journal before deciding KEEP/promotion. Existing PR99/96 capability evidence is retained; no source-only main promotion or graphics claim is made here.
