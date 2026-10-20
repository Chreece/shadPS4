# Missing menu elements and capture repair

Input: `got-indexed-sample-feedback-runtime-20261020-065838.tar.gz`.
Source: `95349a15b95f2d543e6e63b37185706fb4094795`.

The capture completed at the five-minute deadline, with no reported GPUVM faults. It contains 1,076 draw query results and 300 pass images. Gameplay was not tested.

## Proven diagnostic regression

The previous runner supplied fourteen hashes to `ParseShaderAuditHashFilter`, whose fixed array held eight. It fails closed on overflow. Consequently the archive contains zero indexed-table dumps, zero completed indexed feedback rows and zero new GPU buffer snapshots. This was a configuration defect introduced in the last diagnostic runner, not evidence that the shaders did not execute. The previous report's shader-evidence completeness flag only described the shader-file collector.

The filter now accepts up to 64 entries. A preflight reads the capacity from the exact pinned source and validates the configured lists before emulator launch. A separate material-evidence result now requires both active-material sample feedback and GPU buffer snapshots. The actual twenty-entry runner lists, all capacity boundaries, malformed values and uint64 overflow were tested with the production C++ parser externally to the repository. Shell syntax and the actual embedded Python preflight passed.

## Missing geometry is separate from magenta grass

Native readbacks of three representative menu draw commands:

| Argument address | Index count | Instance count | Fragment / vertex shader |
| --- | ---: | ---: | --- |
| `0x1548c1da00` | 402 | 0 | `d0355f00` / `b0f0dc90` |
| `0x1548c1dc00` | 12 | 0 | `c1123f0f` / `c8caf263` |
| `0x1548c1d500` | 36 | 0 | `c1123f0f` / `c8caf263` |

The corresponding draw paths report no input vertices. These commands cannot draw geometry; forcing clipping/depth/material changes cannot repair their zero instance counts. This does not prove that every zero-instance draw should have visible output, nor identify a particular missing object with one of these commands.

Before those draws, the frame includes indirect compute dispatches for `04950cf6`, `55d17c16`, `52b6e003`, and `5c4c2b6f`. Their actual GPU group counts were not captured. This revision adds bounded, timeline-completed readbacks of the three dispatch dimensions, plus regular buffer-binding ranges and single-image binding metadata across checkpoint shaders. This allows producer/consumer correlation using addresses rather than hardcoded guest ranges. A writable binding is explicitly not reported as proof of a completed store.

## Sword evidence

The newly supported RGBA16_UNORM attachment shows a sword silhouette in frame 2, pass 6, attachment 1 at guest `0x1520bf0000`. The accompanying main depth and colour images do not show the sword. Shader `f27da132` subsequently samples that normal attachment and produces the sword-bearing 960x540 intermediate. Thus that intermediate is a reconstruction from existing normal data, not proof that a fresh sword draw reaches the main scene. It remains necessary to distinguish stale attachment contents from an incomplete set of geometry/material/depth writes. Image-binding provenance is included for this purpose.

## Scope

This revision repairs diagnostics and broadens evidence to missing elements. It does not claim a new rendering fix. It preserves native draw counts, depth, clipping, colours and texture selection, and retains the scalar coherence improvement. No title-specific rendering override is added. Production remains unchanged.

Full local project compilation/link and CLI startup passed after the final changes. GPU dispatch readback and image/buffer provenance changes are compiled but await native execution.
