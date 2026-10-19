# GoT bounded GPU-selected buffer repair, 2026-10-19

The latest capture confirms that the earlier image/sampler repair is active, but the menu and world still render incorrectly. It also exposes three compute shaders whose GPU-selected **buffer** accesses were not preserved. This candidate repairs those accesses generically. It has passed a full build and shader validation; **visual recovery is not yet proven**.

Source: `e255fa8f608c3af98397436e13b6b5a89ec3128f`. Paired host/runtime scripts: `7ee9f617aaad1e25c09f7c9e742c308d5442d75e`. Branch: `diagnostic/got-indexed-sample-feedback-20261019`.

## Latest runtime evidence

Input: `got-uniform-sampler-tables-evidence-20261019-191916.tar.gz`, containing host archive `got-indexed-sample-feedback-host-20261019-190831.tar.gz` and runtime archive `got-indexed-sample-feedback-runtime-20261019-191405.tar.gz`. The host built source `84117bbd1e356f6440c4c7dcfc239d950f144d1d`, restored the active executable, and ran the isolated candidate for 300 seconds. Exit 124 is the configured timeout.

| Evidence | Result |
|---|---|
| Latest compile record for each distinct shader | 949 shaders; zero unresolved final images/samplers and zero translation failures |
| Distinct captured SPIR-V binaries | All 256 validate locally for Vulkan 1.3 |
| Fragment shader `0x7eaf40ad` | 335 executed indexed texture samples at one checkpoint, texture ID 9047; 330 nonzero RGBA results, zero out-of-range or nonfinite samples |
| Compute shader `0x33a001dc` | Nine image/sampler entries bound; one executed sample selects ID 4 at a checkpoint |
| Compute shader `0x5065ed7b` | 13/13, later 10/10, image/sampler entries bound; sampled checkpoints record no texture operations |
| High-volume indirect scene draws | Four captured draws have index count 12, instance count zero, and zero GPU vertices/fragments |
| Direct control draws | Three produce vertices, fragments and texture samples |
| GPU faults / texture-view pool exhaustion | None reported in this capture |
| Screenshots | White menu at 45 seconds; broken dark terrain/plane at 296 seconds; no correctly rendered world |

The large menu texture table has 27,651 entries, of which 27,612 are valid. Null entries are not counted as valid textures. Executed sampling proves that this resource path works at those checkpoints; it does not establish that the overall scene is correct. Four small-loop image cases that previously needed missing-input fixtures now have valid live bindings. The 27 low-level SRT resolution messages require interpretation against the final GPU path, rather than treating every message as a failed resource.

## Confirmed buffer failures and correction

| Shader | Captured resource construction | Corrected operation |
|---|---|---|
| `cs:0x5065ed7b` | Four V# groups inside GPU-selected 504-byte records | Preserve raw loads plus formatted byte loads/stores. The former unresolved formatted-store path dropped the write. |
| `cs:0x8ced785b` | V# pointer table at base + 16 + 16 × a bounded loop counter | Preserve the raw U32 store for each selected record. Captured loop bound is four. |
| `cs:0x00a4f19a` | V# pointer table at base + 32 + 16 × a GPU-selected value | Preserve the raw U32 store. The IR proves the selector belongs to the finite range zero through four. |

The compiler preserves the original GPU selector and binds every entry in a proven, bounded descriptor group. Each lowered load/store is guarded on the GPU by equality with its entry index. Unselected entries execute no memory operation. Out-of-range loads return zero and out-of-range stores execute no write. Formatted lowering retains the predicate, including byte stores. The selected descriptor retains its own address, stride and format.

Expansion requires four contiguous descriptor words, a common source and affine selector, a complete record table or a proven pointer-table bound, and enough device storage-buffer bindings for the entire group. Unknown/cyclic selectors, partial records, arithmetic overflow and excessive groups are refused. Descriptor-count witnesses remain part of shader specialization, including transitions through zero. Repeated operations reuse the group's reservation. This implementation covers compute non-atomic accesses represented by these captures; it is not a claim of support for every possible dynamic resource pattern.

The storage-buffer capacity is now 64, with push-constant buffer offsets covering all 64 entries. The push-data structure remains within Vulkan's 128-byte minimum. The device's per-stage and per-set storage-buffer limits constrain expansion. Shader binary version is 51 and metadata version is 25, so incompatible cached shaders are rebuilt.

There are no game or shader-hash conditions in the new compiler repair. Existing synchronization, LDS/Phi, image/sampler selection, specialization and texture-view capacity fixes remain. Production is unchanged; the runner builds and launches an isolated candidate and restores the source, active executable and original profile.

## Validation and limits

- Full shadPS4 compilation and link pass. Existing host preflight checks pass with all 86 pinned source-overlay paths.
- All six newly captured buffer-flagged programs compile and produce valid Vulkan 1.3 SPIR-V. The three affected vector-buffer programs have zero unresolved buffers in local replay.
- `cs:0x5065ed7b` uses the actual captured 6,552-byte, 13-record table. Its replay has 58 buffer resources and retains 13 guarded byte stores. Some descriptors intentionally describe zero-length buffers; these are not missing descriptors.
- Captures omit 20 descriptor words for `cs:0x00a4f19a` and 16 for `cs:0x8ced785b`. Their complete nonzero replays use explicit valid fixtures only for those missing words, preserving all captured data. These fixtures are not claimed to be game resources or a runtime result.
- `cs:0xb5500bd4`, `cs:0x735cf693` and `cs:0xbdfc491f` retain one unresolved static placeholder each for scalar reads already lowered through GPU memory. The emitted SPIR-V never accesses that placeholder's binding 7. This candidate does not hide those diagnostics or claim every resource-warning line is gone.
- All 82 earlier image/sampler replay cases still compile and validate. Their four previously documented missing-input fixtures remain. These image regressions run without the new buffer expansion enabled; the six buffer replays exercise it separately, including the high-binding push-offset path.
- Seven bounded-table cases cover valid selection, zero count, partial records, mismatched words, overflow, device budget and compute-stage restriction. Finite-selector and cyclic/unknown-selector refusal checks pass. Count-specialization changes 4 → 0 → 5 produce distinct state. Earlier seven pointer and six induction checks still pass.
- Bash syntax, embedded Python, overlay coverage and pinned source/script relationships are checked.

The [OpenOrbis hardware probe source](homebrew/bounded-buffers/README.md) exercises GPU-selected raw and formatted buffer loads/stores, varied strides, untouched unselected records, out-of-range selection and count transitions. Its GCN shader assembles with LLVM 19 for Bonaire. The application has **not** been linked with the OpenOrbis SDK or run on PS4 here. No new native GPU gameplay run was available in this environment.

## Next capture and acceptance

Run `scripts/got-indexed-sample-feedback-build-and-run-20261019.sh` from the latest diagnostic branch. It pins the source and paired scripts above, checks the build before launch, and captures 300 seconds. It retains the existing conditional Ollama unload below 8 GiB free VRAM and restores prior state. GDS, DMA, depth and raster experiments stay disabled.

Detailed capture covers the three buffer failures, the three scalar-fallback shaders and the two original indexed-sampling targets. It records `BoundedBufferTable` lowerings and recovers unresolved shaders. The final upload is named `got-bounded-buffer-tables-evidence-<timestamp>.tar.gz`.

Enter gameplay during the capture. Acceptance requires actual world geometry and improved menu/scene rendering, backed by nonzero GPU execution where geometry is expected. A clean compile, valid bindings or fewer warnings alone will not establish that GoT is fixed.
