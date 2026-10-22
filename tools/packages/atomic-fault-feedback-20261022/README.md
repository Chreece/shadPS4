# Bounded atomic fault-feedback continuation — 22 October 2026

## Observed result and limits

The uploaded 11:37 runtime still failed with RADV GPU context loss after 53 seconds. The implicit-dependency build passed and executed but did not restore loading. Its two screenshots are different startup/loading frames, not gameplay. The final game process status is a cleanup result, not successful emulation. Kernel log access was limited. Source, Sirit checkout, active executable, original configuration and Ollama restoration checks passed. No current shader dumps were collected because dumping was disabled.

This package corrects demonstrable bugs in a shared fault-recovery path. The archive does not prove that these bugs caused the observed hang, or that all missing world graphics are explained by them. Missing guest memory reads still follow the existing zero-return behavior until recovery; this change does not replay the failed invocation.

## Four production files

1. `spirv_emit_context.cpp`: replace non-atomic load/OR/store recording with device-scoped 32-bit relaxed `OpAtomicOr`. Concurrent reports for different pages sharing one word can no longer overwrite one another through that sequence.
2. `fault_buffer_process.comp`: reserve actual available output slots with bounded compare-and-swap. The count includes only reserved records; the header slot is excluded from capacity. Clear only the bits successfully queued. Overflow stays in the bitmap for later scans rather than being discarded. No fabricated/clamped output count is used.
3. `fault_manager.cpp`: initialize the internal device bitmap; flush the CPU-cleared feedback region; make compute writes visible to host reads and invalidate the host cache after completion. Include read/write access scopes at parser boundaries, validate the returned count before indexing, and service faulted pages with the existing `IgnoreStreamBuffer` backing-selection flag. This is an internal allocation requirement, not a per-game option: a temporary 16-KiB stream copy does not populate the BDA page table.
4. `vk_pipeline_serialization.cpp`: invalidate binary and metadata shader caches, changing version `0x43520006` to `0x43520007`.

The complete parent remains `e314b11ba791669aed4755bd443ce98675db0404`, tree `c347c62e06147159bba26a8c21904de61186ac21`. The continuation checks that parent and all four exact source preimages, creates a complete child source commit, and records source/binary provenance. No game ID, shader-hash selector, loop limit, depth/culling change, guest-data guess, range shortening or driver override is introduced. No unrelated older experimental tree is imported.

## Compiler evidence and containment

Only the disposable profile enables CPU-side shader dumping. The original profile and rendering settings are not changed. No RADV tracing or GPU readback instrumentation is enabled. Original binaries and SPIR-V are prioritized over IR/text in the archive, up to 48 MiB total and 4 MiB per file. Up to 8,192 files receive inventory records; budget exclusions and additional unrecorded entries are reported explicitly. An empty SPIR-V capture fails explicitly. A 256-MiB dump-directory size guard stops through the existing owned-process cleanup. Dumping adds disk I/O and can change timing. The artifacts alone do not identify which shader was bound at a GPU hang.

The existing five-minute manual gameplay workflow, copied saves/configuration, 8-GiB conditional Ollama reclamation/restoration, 2-GiB abort guard and display-health checks are retained. No automatic game input or capture-trigger key is used. Restoration of original source, Sirit checkout and build executable is required before launching the preserved candidate. A user-space runner cannot guarantee recovery from a system-wide GPU failure.

## Tests actually performed locally

- GCC 14.2 and Clang 17 execute the exact replacement recording sequence through a numeric adapter. Clang uses AddressSanitizer and UndefinedBehaviorSanitizer. Both reproduce the old lost-bit schedule; the replacement covers 1,024 bit pairs and 263,168 concurrent updates across 257 words and eight CPU workers. This is not GPU execution.
- The exact original and corrected queue-shader bodies execute through C++ adapters. The old body reproduces an over-capacity counter. The corrected body drains 3,220 page reports over 12 capacity/bitmap cases, including repeated scans, concurrent reservation, a native 1,024-slot queue, empty/full queues and retained overflow. GCC and Clang sanitizer runs pass. This is not a native GLSL/GPU test.
- Twenty-three source, real-local-Git, compiler, artifact-collection and build-gate tests pass. They cover exact candidate changes, unchanged active checkout, dependency identity, initialization/cache-handoff/residency source checks, failed compilation, inventory bounds, symlink refusal, copied-profile settings preservation and missing-capture handling. The inventory-cap branch is exercised using an explicitly reduced fixture threshold.
- Seven immutable-installer tests pass. The unchanged prior package's 18 packaging and 12 real-Git/dependency-restoration tests also pass. Emulator compiler steps inside those workflow tests are mocked.

The complete emulator, native GLSL queue compilation, real Sirit-linked helper and GPU behavior were NOT tested in the assistant container. Before gameplay the server runner must compile the whole helper against pinned Sirit, inspect the assembled SPIR-V for exactly one Device/Relaxed atomic OR and no ordinary stores in that helper, build the real `host_shaders` target, compile the changed production translation units and fully link the emulator. `spirv-val` is used when installed; absence is explicitly recorded and is not called validation.

## Installation and provenance

The distribution commit is not the resulting local emulator source revision. `install.py` validates the compressed payload and readable runner hashes, checks Python syntax and installs atomically without overwriting a changed file. It requires the already-installed verified implicit-dependency/descriptor/format/native packages; it does not edit them or call a nonexistent standalone test file. `run_native.py` remains the protected workflow underneath this continuation. The new runner also accepts `--build-only`.

Compressed runner: 11,376 bytes, SHA-256 `480869d8cd34f94099cc17c8ee9a618610582276e2c82559b866b993d289771d`.
Readable runner: 34,389 bytes, SHA-256 `25026a77a6216b70af249772efd309addf17438233c665d9dcc6fc30235c0ffa`.

## Sources

Inspected source: shadPS4 `spirv_emit_context.cpp`, `fault_manager.cpp`, `fault_buffer_process.comp`, `buffer_cache.cpp` and the host-shader CMake target at distribution ref `f94c4f09e6111c0f9ff9fd1c5125c762ae88eb07`. The latest host manifest confirms the three unchanged preimages; the cache preimage reflects the previous dependency continuation.

Vulkan host-read synchronization: https://docs.vulkan.org/guide/latest/synchronization_examples.html#_cpu_read_back_of_data_written_by_a_compute_shader
SPIR-V atomic semantics: https://registry.khronos.org/SPIR-V/specs/unified1/SPIRV.html
