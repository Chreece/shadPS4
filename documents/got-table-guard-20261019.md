# GoT resource-table lifetime repair, 2026-10-19

Current candidate: [large GPU-selected buffer-table repair](got-buffer-spans-20261019.md). The latest native run completes without the previous crash, but the menu remains white and the world missing. The new candidate preserves a confirmed missing compute write at large table counts; local checks pass, native visual recovery remains unverified. Earlier evidence and pins below are historical.

The new capture **crashed before completing gameplay**. A local reproduction now matches its fatal descriptor-format error, and the candidate prevents stale table layouts from reading retired records. It also resolves four captured zero-record image/sampler cases. The full build and local shader checks pass. **Menu and world rendering remain unverified.**

Source: `71faef4a8d76282a1931445198571082f0e3c70e`. Paired host/runtime scripts: `b76637bc5f55f9dde664a859b0fcb9a9c4d5862e`. Branch: `diagnostic/got-indexed-sample-feedback-20261019`.

## Evidence from the uploaded run

Input: `got-bounded-buffer-tables-evidence-20261019-195934.tar.gz`. The host built source `e255fa8f608c3af98397436e13b6b5a89ec3128f` successfully and restored the active executable. Runtime ended with `GAME_EXIT=139` and `STATUS=GAME_ABNORMAL_EXIT`, not the planned 300-second timeout.

| Evidence | Result |
|---|---|
| Fatal event | `pixel_format.h:363 MapNumberConversion`, `data_fmt = 11` |
| Bounded buffer lowering | 26 records across `0x00a4f19a`, `0x8ced785b`, and `0x5065ed7b`; live buffer descriptors resolve |
| Material count changes | `0x5065ed7b` uses 13, then 10, then 11 records |
| Latest distinct shaders | 353; four have unresolved image/sampler resources associated with valid zero-record tables |
| Executed indexed samples | 175 fragment samples in `0x7eaf40ad`, including 171 nonzero RGBA results; one compute sample in `0x33a001dc`; no out-of-range or nonfinite samples |
| Geometry | Three direct control queries complete with nonzero execution; no high-volume indirect checkpoint completes before the crash |
| GPU faults / view-pool exhaustion | None reported |
| Screenshots | Only 12, 23 and 33 seconds; the late image is black with the overlay. No restored world is established. |

All 66 distinct captured SPIR-V modules validate against the captured device's enabled features. Two require the validator's `--allow-offset-texture-operand` option: the capture explicitly enables `VK_KHR_maintenance8`. Treating those two modules as baseline-Vulkan failures would misdiagnose the target device. No image-offset rewrite is included.

## Confirmed failure and correction

Cached shader selection previously ran the old expanded SRT walker and constructed resource specialization **before** comparing the current table's bounds. A cached 13-record layout could therefore read records 10–12 after the guest table shrank to ten. Those bytes no longer belong to the current descriptor table.

Using the previously captured 13-record shader/table, reducing its count to ten and replacing a retired record's format word with `32_32 / SNORM_NZ` reproduces the same `MapNumberConversion` fatal error. This establishes the stale-layout bug and a matching failure mechanism. The native crash did not capture its exact offending descriptor or call stack, so that specific runtime cause remains to be confirmed by the next run.

The compiler now preserves the original, unexpanded SRT walker and the bounds used for expansion. Cached selection evaluates those bounds first. A count or stride mismatch rejects the cached layout before expanded descriptor reads or format conversion; the existing selection path finds or compiles a compatible permutation. Both walkers and the bounds survive disk-cache serialization. Shader binary version is 52 and metadata version is 26.

The format conversion itself is unchanged. The correction does not reinterpret retired bytes as a valid format, alter guest memory, or remove bounds checks.

Four other captured programs (`0x04950cf6`, `0x52b6e003`, `0x55d17c16`, `0x5c4c2b6f`) have valid table descriptors with zero records. They now use a one-element image/sampler array containing only the existing null sentinel. Every GPU-selected index maps to that sentinel, and the binder reads no guest records. A one-element sampler array is distinguished from a scalar sampler when emitting SPIR-V. Nonempty tables retain their GPU selector, bounds checks and matching samplers; count changes still select the corresponding specialization.

These are generic resource-compiler changes with no game or shader-hash conditions. Existing synchronization, LDS/Phi, image/sampler, specialization and view-capacity corrections remain. GDS, DMA, depth and raster experiments stay disabled.

## Validation and limits

- Full shadPS4 compilation and link pass, including a final build after formatting.
- The original crash reproduction fails with the old path; the corrected path rejects it. Count transitions 13 → 10 → 0 → 11 → 14 → 13, changed stride, and an inaccessible retired memory page pass before and after a real shader-metadata serialization round trip.
- All seven fresh shader replays compile with zero unresolved buffers, images and samplers and pass Vulkan 1.3 SPIR-V validation. They use captured data reconstructed from the saved SRT walkers and user data. No replacement descriptor fixtures are needed for these seven cases.
- The three buffer cases now have actual captured descriptor words that were missing from the preceding archive. Their fresh results therefore replace the earlier fixture-only evidence for those words.
- All 82 earlier image/sampler replays still compile and validate. Their four previously documented missing-input fixtures remain; they are not native gameplay evidence.
- Seven pointer-table, six induction and seven bounded-buffer cases pass, together with finite-selector/cycle rejection. Existing host preflight checks pass, including LDS/Phi and synchronization checks.
- Bash syntax, embedded Python, all 86 source-overlay paths and the pinned source/script relationship are checked before publication.

The [OpenOrbis buffer probe source](homebrew/bounded-buffers/README.md) now exercises shrinking and growing tables through 13 → 10 → 11 → 0 and poisons retired descriptors. Its explicitly bounded GPU accesses must leave retired records untouched. The GCN assembly is unchanged from the previously assembled Bonaire shader. The application has **not** been linked with the OpenOrbis SDK or run on PS4 here; it does not provide a hardware result for the empty image/sampler path. No native Vulkan gameplay run is available in this environment.

## Next capture

The isolated launcher pins this source and the paired scripts above, checks the host build, then captures 300 seconds. It preserves/restores the source, active executable and original profile, and retains the conditional Ollama unload below 8 GiB free VRAM with restoration afterward. Production is unchanged.

The focused capture includes the three buffer shaders, the four empty-table shaders and `0x7eaf40ad`. `ResourceTableBoundsChanged` records rejected cached layouts; `RESOURCE_TABLE_BOUND_REJECTIONS` counts the emitted reports, which are rate-limited. The upload is named `got-table-guard-evidence-<timestamp>.tar.gz`.

Enter gameplay during the capture. The immediate checks are surviving the previous crash point, resolving the four empty-table cases and retaining the buffer writes. Acceptance for the game itself still requires visibly improved menu/world rendering and actual scene geometry. Compiler success and valid resources alone do not establish that GoT is fixed.
