# Resource access-range correction — 2026-10-21

This is a pinned **source-correction package** on candidate
`94ed29b7c5767718ead87164558d6ec7523bcc9a`, not a claim of restored game graphics.
The launcher applies five hash-checked source transformations in the host
builder's staging directory, adds two production headers, tests them, builds
shadPS4 and preserves the candidate separately. The original checkout bytes,
executable, configuration and saves are not replaced. Submodules are untouched.

## Corrections

1. Preserve the existing unsigned 32-bit byte-address wrap when the buffer
   address optimization shifts divisible operands before multiplication.
   The optimized expression is masked back to the appropriate element-index
   width. It must agree with the unoptimized expression for every input.
2. Compute shader-buffer declaration sizes in 64 bits before mapping-range
   clamping. A mathematical stride/count product must not silently wrap to a
   smaller range or zero.
3. For declarations exceeding the 32-bit size representation, narrow host
   coverage only when final live shader IR proves a maximum read address.
   Ordinary-size descriptors retain their previous coverage. Guest base,
   stride, record count and bounds are never changed. Writers, atomics,
   table spans, DMA shaders, unsupported expressions and unknown/cyclic
   addresses are not narrowed.
4. Invalidate old shader binaries and resource metadata: binary version 64,
   metadata version 29. The existing pipeline-key format is unchanged.

The range analyzer handles conservative unsigned intervals and the [0,1]
result of comparison sampling. It does not assume ordinary depth reads are
[0,1]. Only finite nonnegative values and exactly representable power-of-two
scaling are admitted through the float-to-integer bound path.

The comparison result guarantee comes from the Vulkan specification,
**Sampling / Depth Compare Operation**:
https://docs.vulkan.org/spec/latest/chapters/textures.html#textures-depth-compare-operation

## Evidence and limits

The supplied captured shader's six raw buffer reads were fed to the production
analyzer through an offline SSA adapter. The two read-only bindings end at 32
and 272 bytes respectively. The frequently clamped large binding is the second
one. These limits are derived from operations, not shader hashes or a fixed
272-byte production cap. The captured source is not included in this repository.

The last runtime's final binding diagnostics reported **zero live unresolved
buffer instructions**. Earlier "Sharp source was not flatenned" warnings alone
are not proof that a final live buffer is null: later dynamic/indexed lowering
can resolve a preliminary descriptor attempt. Image/sampler diagnostics and
remaining visual defects are not declared solved by this correction.

## Validation actually performed

- The production range analyzer and address arithmetic compile and pass under
  GCC and Clang; the Clang run uses AddressSanitizer and UndefinedBehaviorSanitizer.
- 200,000 deterministic randomized cases cover range containment and optimized
  address equivalence. The old address optimization disagrees in 105,927 cases;
  the corrected expression agrees in all tested cases.
- 1,048,577 comparison-result fractions, including both endpoints, obey the
  computed lookup range. Ordinary depth, NaN, unsupported scaling and cyclic
  cases remain conservative.
- Ten package-fixture tests check exact-version rejection, ambiguous anchors,
  missing files, symlinks, generated Bash/Python syntax, build ordering and
  source-manifest propagation. These are fixture tests, not a full emulator build.
- The full emulator has **not** been rebuilt in the assistant container for
  this package. The launcher requires the server's full host build to succeed
  and verifies the preserved binary against its report before starting the game.
- Restoration of missing world geometry is **not yet verified**.

## Reproducibility

Run `scripts/got-resource-ranges-build-and-run-20261021.sh` with the exact package
commit SHA. It prepares the previous host/runtime scripts only after checking
their Git blob hashes. Source edits are confined to the existing staged overlay.
The resulting `resource-correction.json` records the patcher SHA256 and SHA256/Git
blob identities of all seven corrected/new production files; the unified diff
and manifest are also copied into the runtime archive.

The gameplay runner retains manual input, isolated saves, bounded captures,
continuous VRAM checks and conditional Ollama restoration from the last tested
candidate. It is not a clean-up command and does not remove older builds.
