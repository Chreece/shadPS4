# Menu material evidence, 20 October 2026

Input: `got-frame-history-evidence-20261020-064521.tar.gz`.
Native source: `f995b566dc9b45dabaa949e2fef2acb1f63c51c9`.

The host build passed. The 300-second isolated run completed; timeout 124 is the capture deadline. Six menu frames have complete statistics for all 180 draws each. No GPUVM faults or texture pool exhaustion were reported. The user did not enter gameplay.

The scalar coherence change has native evidence: 96 scalar readbacks completed, and 275 differing CPU/GPU words were logged. In frame 2, vertex shaders `7472760d` and `9d9a6bd6`, previously fully clipped, now produce 41,663 and 51,125 output primitives, respectively. Their material shader is `836253ca`. This is real progress, not a claim that the menu or world is correct.

The late screenshot has a visible sky and magenta grass. The magenta is already present in frame 2, pass 7, attachment 0 (RGBA8), at draw 17. Thus the earliest captured colour defect precedes lighting and tone mapping. Pass 19 shows a sword in a different 960x540 target; that does not establish that the sword reached the main scene. Multiple indirect paths still submit zero input vertices, including `c1123f0f` and `d0355f00`; zero vertices alone does not establish that each draw should be nonempty.

The previous capture filters omitted indexed-sample feedback and texture-table dumps for `836253ca`. Its compiled shader uses three GPU-indexed texture resources, plus strided buffer material parameters. The present archive cannot establish which actual sampled values or GPU material parameters produced the colour. It also skipped RGBA16_UNORM and RG8_UNORM attachments (Vulkan formats 91 and 16).

This revision adds diagnostics, not a claimed rendering fix:

- Include the active grass shader and adjacent mesh/material paths in existing shader, resource, table and indexed-sample diagnostics.
- Add opt-in snapshots of the exact bound GPU buffers, before draw/dispatch, for selected shaders. One snapshot per shader/resource per selected frame, at most 4 MiB per buffer and 64 MiB per frame; writable buffers are excluded. Timeline completion precedes host access. Captures preserve guest base, stride, records and byte count. These are distinct from CPU-only user-data dumps.
- Capture the two omitted attachment formats and decode their previews correctly. The pass-image ceiling is 128 images/128 MiB per image checkpoint to accommodate them.
- Keep the entire five-minute run at the menu, so all checkpoints describe the same rendering problem.

No colour replacement, exposure clamp, forced geometry, draw suppression or title-specific rendering change is introduced. Production stays unchanged. The next native archive is required to distinguish incorrect texture selection/data from material-buffer values and shader arithmetic. Gameplay remains untested.

Validation: full local project compilation/link and CLI startup passed. Shell syntax and Python compilation passed. External one-pixel fixtures verified both new preview format conversions. The new GPU readback path is compiled but requires native execution; no PS4/gameplay or visual correctness claim is made.
