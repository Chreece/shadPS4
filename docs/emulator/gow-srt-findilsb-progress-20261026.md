# Generic SRT FindILsb32 recovery - GoW evidence, 2026-10-26

## Objective

Move Ragnarök beyond the loading screen by repairing resource-table offsets within the emulator itself, not by resetting host display services or using game IDs/shader IDs, visibility hacks, fake descriptor writes, shader skipping or forced readback modes.

## Quantified unresolved resource operations in prior runtime archives

The bounded excerpts in the previous three GoW runs show, respectively:

| Candidate | Unsupported `FindILsb32` | Dynamic `Phi` | Failed host SRT walker offsets |
|---|---:|---:|---:|
| Phi-only, game ran 32.508 s | 2,351 | 635 | 2,987 |
| Auto BDA, game ran 36.867 s | 2,340 | 635 | 2,976 |
| Fault/warning repair, game ran 29.191 s | 2,357 | 635 | 2,993 |

These are occurrences in uploaded **bounded excerpts**, not uniquely identified shaders or full-program diagnostic counts. Different runtimes may compile a different subset of shaders. Their count does not measure graphics correctness.

**Concrete recompiler omission:** `FlattenExtendedUserdataPass::ComputeOffset` logged `Unexpected instruction for offset computation, FindILsb32`, because it did not recognize the opcode. `IsAllowedOffsetInstruction` also did not include it, so dependency analysis could omit reachable host-available SRT leaves. `SrtGvnTable` did not identify equivalent `FindILsb32` expressions.

## New generic candidate

- Add host SRT `FindILsb32` with identical SPIR-V behavior: least-significant one-bit index, `0xFFFFFFFF` when the source is zero. x86 BSF leaves the result undefined on zero; CMOVZ supplies the correct sentinel.
- Allow `FindILsb32` in the permitted CPU SRT dependency graph; retain recursive failure for genuinely GPU-dependent `Phi`. Never choose a control-flow side on the host.
- Include pure unary `FindILsb32` in SRT value numbering to deduplicate equivalent resource offsets.
- Add seven focused tests, including an **executable Xbyak JIT test** that calls the production lowering on 2,081 inputs. Prior 18 tests remain; total planned 25 (on x86-64).
- Preserve the existing branch's unrelated pending GPU fault/BDA fixes and first-party compiler warning work, but do not attribute crash changes to FindILsb until compared by exact game observations.
- The remaining 635 `Phi` errors need separate GPU-side treatment, likely dynamic texture/sampler descriptor discovery; this candidate does **not** claim to handle them.

## Acceptance and next comparison

Native shadPS4 must compile and all 25 focused tests must pass **before** launching the emulator. Use an immutable new source worktree, the existing native observer, >=8 GiB VRAM headroom, and no automatic game inputs. Preserve the stable 120-second baseline SHA256 `7604bae00c78b72e163138327f61a2f92bf549ac66446487d1ab7370a2f9b4d1` and production executable SHA256 `8de2106a3e0d410904d9c8d6a2903ba6f1f65986af3c848b0bf274a4f4b45c8e`.

Count `FindILsb32`/dynamic `Phi`/flatbuf warnings after game exit and inspect the actual image frames. A drop in `FindILsb32` diagnostics only demonstrates successful lowering; it is not a claim of playable graphics. Keep all trial outcomes (including failures) in `~/projects/shadps4/diary/trials.jsonl`.

## Experiment: 20261026-160302-916073 - build failed, no gameplay

- Exact source: `60384c87bd83a90a8b41141d60a64a8dc579436e`.
- Evidence: `shadps4-srt-findilsb-20261026-160302-916073-build.tar.gz` and matching worker log.
- Tests: **25/25 passed**, including `SrtFindILsb32.ExecutableJitMatchesScalarReference` (2,081 inputs). The missing SRT operation therefore passed its isolated unit tests.
- Native compilation: **FAILED** at `[2261/2633] core/file_sys/fs.cpp.o`; compiler produced three error sites at `fs.cpp:719-721`: cannot convert `shared_ptr<Core::Devices::Logger>` to `shared_ptr<Core::Devices::BaseDevice>`.
- Root cause in existing source: `class Logger final : BaseDevice` declared **private** inheritance. An earlier standard-handle cleanup replaced an unsafe `reinterpret_cast` with type-safe `std::shared_ptr` polymorphism but failed to make the inheritance public. The defect is unrelated to `FindILsb32` or Vulkan shaders.
- No emulator ELF was linked, no game was run, no video/audio/FPS outcome exists for this experiment. The 120-second baseline and production executable hashes remained unchanged; SSH remained open. `DECISION=BLOCKED`; keep the tested `FindILsb32` work but **do not promote or claim improved rendering**.
- Generic correction: make `Logger` publicly inherit `BaseDevice`, and assert `std::is_convertible_v<Logger*,BaseDevice*>` when compiling `fs.cpp`. Both edits are now committed on the isolated branch (commit `14975c44f2ab4840a2d73f15e60ef72314f6cef5`).
- Next: resume the **existing isolated cached build directory** after verifying its prior source commit and clean tracked state, then compile `fs.cpp.o` as a fail-fast target before completing the remaining native build. Preserve the failed build log/archive and append a distinct trial entry for the new run. If linked, run the same bounded native observer and count full-console `FindILsb32` and `Phi` diagnostics, checking actual rendered frames.
