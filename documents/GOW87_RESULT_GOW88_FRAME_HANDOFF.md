# GoW87 result and GoW88 framebuffer provenance

Evidence: user archive `gow87-original-profile-20261014-053537-1617666.tar.gz`.

## Best retained candidate
- Executable: `~/projects/shadps4/GodOfWarRagnarok/tests/gow87-build-20261014-053350-1617666/shadps4`
- SHA-256: `0db6d6aec698ef16537aaa2e0ddca43f6b18e15c13fbf225f6ef22843056713c`
- Incremental metadata fix built 12 units and reused 414 original objects.
- Retains DS_ORDERED_COUNT/TG_SIZE, existing lane/EXEC corrections, sparse queue family 4, 64 KiB buffer threshold, CPU-ID translation and per-permutation resource metadata. Tested with reversible original-profile DMA settings.

## What the test proves
The process remained alive until the 60-second runner limit (60.92 seconds including stop handling). Natural return code was null; final return code 0 follows the runner's termination request, not natural game completion. No RADV GPU hang was logged and no RADV dump was produced. The previously observed graphics memory fault did not recur in this bounded run.

DMA and the target shader 0x04691bd5 were confirmed. All five before/after source/binary pins passed, the original per-game override and shader dumps were restored, and the global config SHA remained unchanged.

Eight window screenshots were captured. All game content remains black: the early images contain window decoration/cursor, not game scenes; the four captures from 24.02 seconds onward are entirely zero RGB. No visible gameplay is proven.

The log records 527 shader-permutation compilations, 281 graphics-pipeline compilations and CPU dispatch sequence 99281. These are CPU-side progress markers, not proof of completed GPU frames. Unresolved SRT warnings persist; their presence alone does not establish the current black-screen cause.

## Evidence omission repaired in Phase88
The former alphabetic copier consumed 79,999,992 bytes of its 80MB budget before graphics shader binaries. It copied 55 compute SPIR-V binaries and zero vertex/pixel SPIR-V binaries; 472 SPIR-V binaries were listed as omitted. Phase88 copies all SPIR-V first, then GCN binaries, then text IR, and excludes empty artifacts. It reports omissions explicitly.

## Phase88 implementation
`tools/gow88-frame-provenance.py` recompiles only the presenter into a private overlay and relinks with the 11 other retained Phase87 objects and the original unchanged objects. It must consume the Phase87 corrected metadata headers. No CMake regeneration or cloned build directory is involved.

`tools/gow88-frame-provenance.patch` adds a disabled-by-default, bounded GPU readback probe using the existing screenshot implementation. At most eight guest-output and eight displayed-output readbacks are requested at sampled times. The guest capture is before host scaling/post-processing; the display capture is from the swapchain. The existing deferred GPU-completion callback is used, with explicit transfer-to-host visibility and allocation invalidation. PNGs include RGB statistics in JSON, with alpha ignored. Automated captures suppress screenshot notifications.

The two streams are independently sampled; they are not guaranteed to describe the same frame. Nonblack pixels can include UI and require image inspection. No completed readback must never be classified as a completed black framebuffer.

Shader logic and renderer resource semantics are unchanged by this probe. It is diagnostic, not a claimed rendering fix. The source patch and its scheduling/pixel-count helpers passed local checks; the incremental overlay/relink preservation logic passed a real small-project compiler test. Full shadPS4 compilation and AMD GPU execution of Phase88 remain pending on the home server.

## Workspace contract
Historical logs are disposable output. Runtime uses automatically created `GodOfWarRagnarok/runtime/gow88`. Build provenance is stored under the retained test/build directory, never required from old logs. Protect Phase80, Phase84 and Phase87 checkpoints. Reuse the original profile with durable reversible settings/dump recovery; never substitute a new incomplete XDG profile.
