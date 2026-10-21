# SRT lane / required-read correction package

This distribution package applies a four-file, exact-preimage-checked correction to the user's locally rebased commit `04d891dc33bb1c8c89789b5ff039049901f95a41` on upstream `c2814041e546bf75c12756dbec9da911c721ce1c`. The distribution commit is not the emulator source revision produced by the host build. The host records a separate full-tree candidate commit, corrected-source manifest and preserved binary SHA256.

Concatenate payload.00.b64 through payload.12.b64 in numerical order, remove whitespace and base64-decode. The resulting ZIP must be exactly 25,806 bytes with SHA256 `32016c6f4a71026c451a8a491eb18934e06fed2d7eb952e983f2cf8bfd076cd7`. It contains seven readable files: srt_lane_fix.py, verify_srt_lane.py, runtime_adapter.py, run_srt_lane.py, test_package.py, README.md and MANIFEST.json. The checksum and the manifest are required before executing any package code.

## Emulator changes

1. Lower ReadLane / ReadFirstLane resource-table offsets on the host only when their complete source dependency is proven wave-uniform. Unknown instructions, GPU attributes, cyclic values and Phi remain rejected by the host evaluator. No lane-zero guess is introduced.
2. Roll back partially emitted SRT machine code when a pointer or load offset cannot be generated. This prevents rejected translations from leaving an orphan stack push or partial computation.
3. Keep genuinely unflattened live ReadConst operations on the existing shader-side guest-address path even when the optional direct-memory setting is off. The prior default-off emitter could read flat-buffer slot zero instead of the requested guest address. Flattened accesses retain their existing path.
4. Invalidate old shader binary/metadata caches (versions 0x43520002).

Only flatten_extended_userdata_pass.cpp, shader_info_collection_pass.cpp, emit_spirv_context_get_set.cpp and vk_pipeline_serialization.cpp are changed. No game IDs, shader hashes, guest-address selectors, forced counts, draw skipping, depth/culling overrides or range shortening select the correction. This package does not prove that other shader-resource or world-rendering problems are resolved.

## Validation completed locally

GCC 14.2 and Clang 17 compiled and ran the uniform-source proof and read-routing fixtures; the Clang run used address and undefined-behavior sanitizers. Each compiler covered 240,000 expression cases (38,754 accepted and 201,246 rejected). The old default-off read route fails its negative control; the corrected route passes with the setting both off and on. These are explicitly fixture tests, not complete guest shaders or GPU execution.

All 18 packaging/regression tests passed, including isolated-profile behavior, process identification by /proc/PID/exe, full-stream error counters, real local Git alternate-index commits, checkout/binary restoration on successful and failed mocked builds, and refusal to reset unexpected external edits.

The full emulator and native GPU runtime have NOT been tested in the assistant container. The actual pinned-Xbyak generated-code rollback regression is also deferred to the server, where it is mandatory before production compilation. The server compiles all four production translation units first, then requires the complete emulator to link successfully.

## Host operation

Close any already-running emulator normally before starting. The launcher refuses concurrent shadPS4 processes by executable path, independent of their thread names. It reuses the clean build's configured compiler and pinned submodules. Its temporary candidate checkout is restored before launching, as is the previous build executable; a separate candidate is preserved by commit and SHA256. Unexpected external edits stop restoration rather than being forcibly discarded.

The five-minute runtime uses a copy of ~/.local/share/shadPS4, excludes stale caches, external shader patches and per-title overrides, retains renderer settings, sends no game input and does not alter ES-DE. It reuses the exact pinned runtime's owned-process cleanup, conditional 8-GiB Ollama VRAM recovery, 2-GiB abort guard and model/context restoration. It records screenshots, bounded logs and whole-stream error counts; it does not enable the old experimental capture or rendering environment flags.

A successful build is not a verified fix for the loading stall or missing world. Review the resulting archive and the actual picture before promotion.
