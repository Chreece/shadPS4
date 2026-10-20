# Missing geometry investigation: 09:55:21 capture

The tested source was 2dacdb5da2cf309db83cf090499b67fbf4aa473b. Capture completed
300 seconds, sent N at 150 seconds, preserved production and reported no GPUVM
faults. Missing menu elements and missing world graphics remain unresolved.

The resource-residency correction has observable evidence: software address-table
misses fell from 229 to 13; the visibility-table page 0x1502614000 fell from 152
misses to one. Retain this correction, but do not attribute a visual fix to it.

Frame 2 still contains zero-vertex indirect draws for the c1123f0f/c8caf263 and
438dda67/eb5093c3 pipelines. Frame 6 contains direct mesh draws that submit
geometry yet produce only a handful of fragment invocations. These observations
do not establish whether a zero-count command is incorrect or intentionally
culled. A sword silhouette in an intermediate normal attachment is absent from
the current color and depth attachments; retained normal contents are not proof
that the current frame rendered the sword.

The prior producer diagnostics used eight fixed command addresses from an older
run. The current zero-count commands include 0x1546e1d300, which that list does
not cover. The previous trace cannot establish the producer of these commands.

## Next diagnostic build

This revision is instrumentation, not a claimed rendering correction:

- With SHADPS4_VERTEX_OUTPUT_FEEDBACK=1, vertex shaders record up to 64 actual
  invocation outputs per captured draw: vertex ID, instance ID and final clip
  position. Counters record total invocations. Storage is a separate host-owned
  buffer and writes are bounded; no guest output or draw count is substituted.
- Capture is limited to existing frame checkpoints and 2048 draws per checkpoint.
  Outside those frames the shader reads a disabled flag and does not record.
- Save GPU-visible flattened constants and first-seen vertex bindings, within
  existing per-frame byte budgets. Binding IDs 4096 and above denote vertex
  attributes; regular storage bindings retain their original IDs.
- Match sampled positions to frame/draw pipeline queries in
  info/vertex-output-summary.csv. NDC ranges describe only the sampled vertices,
  not complete mesh bounds. The binary retains every captured raw position.
- Learn up to 32 command addresses from actual selected zero-count GPU readbacks.
  Subsequent producer diagnostics match these observed ranges, using atomic
  registration across scheduler and command threads. No fixed historical address
  determines the live producer trace. Commands must first have been observed;
  this cannot retrospectively capture their earlier producers.

Default emulation is unchanged except for the shader-cache version bump to 60.
The isolated runner enables diagnostics, keeps the 150/300-second input/capture
schedule, and uses a verified 100-file overlay. GPU instrumentation can change
timing; compare its outputs with the uninstrumented capture before inferring a
synchronization defect.

Validation: full build passed; feedback-enabled and feedback-disabled vertex
SPIR-V validate for Vulkan 1.3. The enabled shader includes atomic counting and a
64-record bound; the disabled variant has no feedback storage. External checks
cover sample parsing (NaN, zero/negative W, clipping, draw-query matching), and
concurrent address registration, overlap boundaries, overflow and full capacity.
No game execution or visual improvement is claimed for this revision.
