# Native shadPS4 build warnings and GPU fault audit — 26 October 2026

## Evidence

Source `11610d67064433179d7943d684b0632d470a3c7b` native GCC 14.2 build, exact log from the uploaded `shadps4-srt-bda-20261026-130836-3231483-build.tar.gz`.

- Build linked successfully, candidate SHA-256 `c45382d944b8e73364514c80691938ec36464feab9f3d598b94985f8a2451ba5`.
- 12 of 12 original invariant-Phi/BDA regressions passed.
- Compiler emitted **116 native warnings** (24 in project core source, 8 in system headers/inlined call sites, 84 in vendored dependencies), plus **3 GCN test warnings**. Warning counts are occurrences, not distinct issues.
- Dependency warnings: `miniz` 76 (unused functions in bundled miniz.h), `fdk-aac` 4 (array bounds), `glslang` 2 (possibly uninitialized data), `protobuf` 1 (deprecated API), `ext-boost` 1. These remain distinct vendored issues; do not suppress -Wstringop-overflow/-Warray-bounds globally.
- First-party warnings: libc_internal/printf.h 14, ajm_at9.cpp 6, np_common.cpp 2, module.cpp 2. The eight system-header inlined warnings include allocator patterns from ShadNet/Matching2 and a glslang/STL inlining warning.

## Runtime

Uploaded runtime `shadps4-srt-bda-20261026-130836-3231483.tar.gz`, SHA-256 `89bfc9226ec47e5f84dfbefd448358214e6ed40d4c70dd806a5328e1ba7137c3`.

- Source-specific runtime 36.867 s, exit -11; RADV reported guilty context loss / soft recovery and Vulkan `ErrorDeviceLost`.
- Ollama unload yielded about 19.560 GiB free VRAM and restored model afterward; device loss is not explained solely by the low-headroom scenario.
- Frame at 21 seconds remained on "Loading game... Waiting for game video". No playable world was observed.
- The console still contains many unresolved host walker Phi offsets; BDA auto-read activated for 12 compiled shaders. No kernel reset lines were recorded in the readable selected journal interval.
- A one-game observation cannot establish whether the GPU or compiler change caused or repaired any earlier symptom.

## New generic corrections (not gameplay-validated yet)

1. `np_common.cpp`: bound OnlineId scan to the 16-byte payload; full-length IDs use their separate terminator.
2. `core/module.cpp`: stream symbol then NID salt into SHA-1, preserving the exact byte concatenation and avoiding an overflow-prone vector allocation. Explicitly avoid passing a null empty string_view to SHA1.
3. `libc_internal/printf.h`: avoid zero-digit length underflow in signed formats, signed minimum negation overflow, float precision array overrun, nonfinite signed conversion, and INT_MIN negative width overflow.
4. `shadnet/client.cpp` and `np_matching2_mm.cpp`: allocate sized packet storage once, enforce bounded serialized data size; replaces vector reserve/push construction that GCC warned about.
5. `ajm_at9.cpp`: replace implementation-defined multichar RIFF literals with constant little-endian FourCC values, and copy byte-aligned wire headers into local objects before parsing.
6. `tests/gcn/gcn_test_runner.cpp`: check Vulkan fence/command-buffer reset errors and report failed waitIdle, rather than discarding nodiscard results.
7. `buffer_cache.cpp`: zero the new device-local fault bitset prior to shader access.
8. `fault_buffer_process.comp`: single SSBO representation, saturating atomic queue count to the 1023 available 64-bit slots, requeue overflowed fault bits; use atomic exchange for word consumption.
9. `fault_manager.cpp`: host-cached result buffer flush before GPU use, invalidate after GPU completion, and clamp damaged count before any CPU array traversal.
10. `vk_rasterizer.cpp`: track the page-table and fault bitmap GPU resource accesses to insert the missing transfer-to-shader barriers.
11. `shader_info_collection_pass.cpp`: expected automatic BDA activation is an info event, not a false error.

Six additional unit tests exercise NP IDs, SHA-1 equivalence, printf zero-precision, fault count limits (total 18 with earlier SRT tests).

No emulator executable has been replaced. This candidate must pass the 18 regressions, full native link, and repeat GoW/GoT runtime tests before any merge or declaration of gameplay correctness. The remaining GPU-dynamic sharp-indexed texture/sampler rendering work is **not** solved by these fixes.
