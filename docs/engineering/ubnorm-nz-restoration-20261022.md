# Restore shared UbnormNz texture handling — 22 October 2026

## Observed failure

The supplied 07:41 runtime stopped after 32 seconds at `SurfaceFormat` with `Unknown data_format=39 and num_format=11`, returning -11. Those encodings are BC5 and UbnormNz in AMD's Sea Islands ISA Table 8.13 and this source's enums. The uploaded log contains no GPU-context-loss message, but the earlier termination does not establish that the previous GPU hang is resolved. The one screenshot is a startup frame, not proof of playable world rendering.

The full native-minimum build passed on the host. Its real Sirit emitter test recorded one native UMin operation and no XOR shuffles. This restoration keeps that correction, its dependency gitlink, and the retained SRT/clean-upstream changes.

## Source correction

Restore two omitted case labels in `pixel_format.h`:

- `RemapNumberFormat(UbnormNz, ...)` uses the existing Unorm host format.
- `MapNumberConversion(UbnormNz, ...)` selects the existing UnormToUbnorm shader conversion, not an unconverted texture.

The complete resulting header is byte-identical to historical blob `2c12d8fd03fe8e734023276929a8944eb2400f0c` at `94ed29b7c5767718ead87164558d6ec7523bcc9a`. The clean preimage is `eb2f52df4409d49ec8b280571762aac9f4d7ae26`; its entire difference is two added lines. No other experimental files are imported. Existing read conversion computes `2*u - 1`; the existing BC5 Unorm Vulkan mapping is retained. No null/fallback texture, assertion suppression, guessed descriptor, game ID, shader identity or driver setting is added.

The host-generated candidate also increments both shader binary and metadata cache versions from `0x43520004` to `0x43520005`. Apart from those versions, only the two format cases differ from the successfully built native-minimum source tree `5291c84e01096b9da3bef585c4d544a950eff25d`. The runner checks that complete tree before extending it and checks the exact incremental two-file change set afterward.

## Build and runtime

`restore_ubnorm_nz_20261022.py` validates and imports the immutable complete native-minimum package from distribution commit `28d632b9245e8c83366f5624ebd767261425982f`. It does not rewrite that package or invoke a guessed standalone test path. It adds a native mapping regression before the existing real-Sirit emitter test, production-object compilation and complete link. Original source, dependency checkout and build executable are restored before the preserved candidate is launched. The existing copied-save/configuration, conditional Ollama restoration and owned-process cleanup policy is unchanged. No RADV overrides or per-title renderer configuration are introduced.

The distribution commit is not the local emulator candidate revision. The host records its actual full-tree candidate commit, source manifest and binary hash. The format delta is appended to the same correction manifest, diff and native-test log already carried into the runtime archive.

## Validation performed here

- The full local preimage and restored header match their published Git blob hashes.
- GCC 14.2 and Clang 17 compile and execute the exact extracted production mapping functions. The Clang run uses AddressSanitizer and UndefinedBehaviorSanitizer. Both reproduce the old missing BC5 mapping as a failing executable negative control.
- Across the 64 x 16 enum-value grid, the 64 UbnormNz selector pairs take the restored route and the other 960 retain their previous mapping/conversion result, including unchanged rejection cases. This is a dispatch test, not a claim that all enum combinations are legal image formats.
- The existing shader bias expression is executed through a numeric emitter adapter for all 256 8-bit and 65,536 16-bit input codes, plus endpoints and an interpolated midpoint. This checks arithmetic, not GPU BC5 decompression, filtering, swizzling or texture contents.
- Fourteen new source/loader/real-Git integration tests pass, including the actual C++ regression invoked through the extended prepare step, refusal of unexpected source trees, exact change-set/provenance checks and an injected compile failure leaving the active checkout untouched.
- The unchanged complete package's 18 packaging tests and 12 real-Git/dependency restoration tests pass again. Compiler processes in those workflow tests are mocked; they are not a full emulator build.

A complete new emulator build and GPU gameplay run have NOT been performed in the assistant container. Loading and all world graphics remain unverified. This change addresses the demonstrated missing format route without claiming that other logged SRT, texture-copy, stencil or buffer issues are solved.

Primary source: AMD, Sea Islands Instruction Set Architecture, Table 8.13 (page 8-20). The retrieved PDF confirms the encodings; its table alone does not validate the complete texture-sampling implementation.
