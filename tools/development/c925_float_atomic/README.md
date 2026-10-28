# Float atomic typing and conditional side effects

## Confirmed native shader defect

The real offline audit `got-c925-spirv-offline-20261028T175021642972Z.tar.gz` examined 298 distinct modules with SPIRV-Tools v2025.1: 297 pass universal SPIR-V1.6, 294 pass strict Vulkan1.3 and scalar-layout Vulkan1.3. The single universal failure is guest shader `79ab8f30`, module SHA256 `694f56399556d5fcf0baedc0e05b9870964fbef6165a98b719b5946ad4e07fb5`: integer `OpImageTexelPointer` points into a float/R32f image. The current device reports `shaderImageFloat32AtomicMinMax:0`, so the fallback is required.

Source and binary inspection also show the fallback executes both sign alternatives before `OpSelect`. Selecting a result does not select which atomic side effect ran. The same eager pattern exists for both image and buffer float min/max. The module contains two such atomic pairs and a normal floating `OpImageWrite` to the same image: changing the entire descriptor to unsigned would introduce another typing error.

PR100's actual guarded-read module (`8fb990b3...`) passes all three real validator modes. Retain that capability. No validation result identifies the cause of the previously frozen presentation or promises complete scenery recovery.

## Production correction

The exact-source adapter changes four native C925 production files:

- `resource.h`: add separate emulated-float-atomic descriptor identity and an R32_UINT interpretation for that access only. The returned descriptor feeds both SPIR-V image declarations and Vulkan image-view creation. Addresses, dimensions, guest backing bits and normal floating descriptors are preserved.
- `resource_patching_pass.cpp`: keep atomic UINT aliases distinct during resource deduplication; select them only for float min/max when native image support is unavailable; fail explicitly on unsupported real non-32-bit formats rather than inventing a layout. Bounded generic logging identifies actual use.
- `emit_spirv_atomic.cpp`: replace eager result selection with real negative/positive branch blocks and a Phi for the one executed operation's old-value return. Apply to image and buffer float min/max fallback; preserve native feature paths and original operands/scopes. Inherited sign-bit ordering remains; this is not a new guest NaN policy.
- `vk_pipeline_serialization.cpp`: advance shader metadata and binary versions together because resource identity/representation and emitted instructions changed. Do not delete caches.

No shader-hash/title rendering conditions, sample-coordinate changes, fake table bounds, skipped draws or disabled validation. Shader hashes in the runner select diagnostic files only.

## Do not misclassify the other three diagnostics

`cfd30fa8`, `18c40c0e` and `7901b9d8` pass universal validation but fail the Vulkan Offset rule. Khronos maintenance8 permits Offset on these sampling/fetch operations; the native log enables `VK_KHR_maintenance8`. The original audit did not use the validator's `--allow-offset-texture-operand`. These are feature-conditioned results, not automatically three additional code-generation errors. The runner retains strict results, separately validates maintenance8 mode and copies current native `vk_instance.cpp/.h` for checking the enabled feature chain. It does not remove offsets or assume an extension name is a complete feature-enable proof.

## Actual local tests

- Actual transformed branch helper and view helper extracted into a C++ recording/interpreting context: **449,562 checks PASS each** under GCC C++20 Werror, Clang C++20 Werror and Clang ASan/UBSan. Checks exactly one atomic side effect, original-old-value return, sign selection, non-NaN numerical ordering, infinities, preserved bits/address/layout and separate normal floating access. This is not native Vulkan execution.
- **18 Python tests PASS** with ResourceWarning treated as error: exact source transformation, Git forward/reverse byte restoration, altered/repeated source refusal, both actual atomic pairs, float-write preservation, separate alias declarations, immediate Phi predecessor sets, wrong-sign refusal; explicitly mocked native success, offline-validation rejection before mutation, build failure rollback, source/ELF checks, model restoration and regenerated-module failure propagation.
- Local failures/review corrections retained: initial ambiguous source anchor refused before writes; scoped to `PatchImageSharp`. Initial reference fixture expected one pair; actual module has two, both are now transformed. Early whole-image UINT reinterpretation in the reference fixture was rejected on review because a normal float write shares the image; final fixture creates a distinct alias, as production already does. No user-host run or source modification occurred in these preparatory failures.
- No `spirv-val` executable is available in the model environment. Network/package retrieval failed. **No full local validation of the corrected reference or native recompiled shader is claimed.** The user's already-audited tools are a mandatory pre-build gate.

## Host sequence

Download the four runner files from one pinned commit, plus the immutable existing `resume_c925_revalidation.py` and `resume_finite_raw.py` helpers (hashes checked in the runner). It verifies the existing PR100 ELF `9f0f6c9fe83f15aec6dea1dcf3f23226d944472daa0bdb6258cff13a95f219d3`, retained source manifests and accepted renderer hashes.

Before any source mutation, it re-expresses the actual invalid module into an **offline reference-only** file, using a separate UINT image alias and guarded atomics. Khronos assembler and validator must accept that reference in all available modes. The reference is never installed in a cache or executed by the emulator. If this gate fails, the archive is returned without modifying/building the emulator.

Then checkpoint the native candidate, apply the four C++ changes, run available GCC/Clang contract tests and incremental Ninja build with visible stage/progress/heartbeat and full log. Test/build failure restores the previous source and candidate ELF. Only a successful build proceeds to the private-profile, no-input menu test with the existing 8GiB VRAM gate, same-server Ollama restoration and 118s TERM +2s KILL owned-process limit. Original saves/configs, protected C925/GoW binaries, ES-DE, SSH and Sunshine/X11 are not rewritten or restarted.

After the game, validate the newly emitted modules again, preserving actual failing/affected bytes, feature-conditioned diagnostics, target IR, timestamps and pixel signatures. A clean process exit is not a graphics pass. No runtime shader is overwritten with the offline fixture. Outputs remain under `~/projects/shadps4/logs/got-c925-float-atomic-*`.

This is the exact unpublished C925 source lineage, **not a completed latest-upstream rebase or a renderer already integrated into fork main**. Official main checked at preparation: `b6f0c58dba9ee75b93477703032ae4f1e0de8f1c`. Full native build and GPU acceptance of this new correction are pending. The shared journal records the actual offline results separately from this implementation and its local tests.
