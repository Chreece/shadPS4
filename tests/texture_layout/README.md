# Tiled texture layout regression

This branch integrates upstream [PR #5196](https://github.com/shadps4-emu/shadPS4/pull/5196),
merged on 2026-10-01 as `7e77898756a50e2aa0bae0031790eb81b392e9b2`.
The six production tiling files retain the upstream changes. The only additional
production change is an optional, bounded `micro-mip-detile` diagnostic emitted
after dispatching an image that contains macro-to-micro mip transitions.

The 2026-10-02 capture from main `6e00d2cc` contains tiled-image uploads but no
raw-buffer/image synchronization events. The user reports missing elements
on entering gameplay until the scene settles. That observation makes texture
loading relevant, but does not establish the cause. The capture does not expose
per-image mip layouts. The new event identifies use of the corrected path;
even a positive event does not prove that it caused the visible problem.

## Local checks

On Linux with the repository's dependency submodules initialized:

```sh
cmake -S tests/texture_layout -B build-texture-layout -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build-texture-layout
ctest --test-dir build-texture-layout --output-on-failure
```

The fixtures execute production `ImageInfo::UpdateSize` and AMD tiling helpers.
They cover a thin macro mip chain, compressed-block mip tails, shared slice
padding for thick arrays, eight-to-four slice thickness on small mips, and the
3D PRT mapping. Unused Vulkan constructors are discarded at link time; no GPU
or Vulkan driver is initialized. `SOURCE_ROOT` can select an older source tree
for a negative control, and `EXTERNALS_ROOT` can select existing dependency headers.

Validation on 2026-10-02:

- GCC 13.3: all five fixtures fail against `6e00d2cc` and pass with this change.
- Both enabled/disabled graphics diagnostic checks pass.
- Glslang 15.2: all twelve macro/micro, 8/16/32/64/96/128-bit shader variants
  compile for Vulkan 1.3.
- The changed tile manager passes a C++23 syntax check using those generated shaders.

These checks do not execute the detiler on a GPU or establish an RDR graphics fix.
The full Linux build and visual test are performed locally through the pinned
Docker helper. Existing audio, sparse queue, depth and presentation changes remain
in main; this branch adds no audio, configuration or host-session changes.

## Visual test

Keep the existing 7.1 configuration. Launch the NGS2 test entry once, load the
same save, then traverse the same route twice in that session. Capture graphics
while elements are missing. Compare initial loading, revisiting the area, indoor
light, cutscene progress and dialogue separately. Avoid enabling validation for
this normal-speed comparison. Use the installer's verified RESTORE command if
the new revision regresses.

AI assistance: integration review, local regression fixtures, bounded diagnostics
and this validation record were prepared with Codex. Upstream authorship remains
with the linked project commit. No upstream PR submission is made here.
