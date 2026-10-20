# Dynamic resource residency candidate — 20 October 2026

The 09:19:11 capture still has missing menu and world elements. The prior scalar
condition correction is retained for its independently verified semantics; this
capture does not demonstrate a visual benefit from it.

## Evidence

The runtime reports 229 software GPU address-table page faults: 73 at
0x150260c000, 152 at 0x1502614000 and four other pages once each. These are
emulator address-table misses, not GPUVM faults. The second repeated page
contains the visibility shader 0x5065ed7b descriptor table at 0x1502614280.

A 16 KiB fault recovery request previously used ObtainBuffer with default flags.
A clean read-only request of that size can take the stream-buffer path, which
does not populate the GPU address table. Repeating the recovery request therefore
need not repair the failed dynamic read. Small directly bound buffers used by
DMA shaders can take the same path.

The capture also demonstrates successful GPU-selected sampling in 0x7eaf40ad;
its historical unresolved descriptor report is not proof that all its current
sampling remains unresolved. Early invalid descriptor permutations that resolve
later are not treated as persistent missing resources.

## Generic corrections

- Force persistent address-table residency for fault recovery and DMA-stage
  regular buffer bindings.
- Atomically record fault bits, so concurrent invocations cannot lose each
  other's missing pages.
- Initialize the device fault bitmap and synchronize that initialization before
  shader access.
- Flush the cleared host download area, synchronize GPU writes to host reads,
  invalidate before reading, and clamp the returned count to its allocation.
- Preserve unprocessed bits when the bounded fault list fills up.
- Increment shader binary cache version to 59; expand the isolated runner overlay
  to 98 files, including the fault manager and its host shader.

No shader hash, title, forced draw count, clipping bypass, or depth override is
used by these corrections. Production is unchanged by the diagnostic runner.

## Validation and limits

Full shadPS4 build passed. External checks of the actual fault-handler body cover
counts 0, 1, 1023, 1024, UINT32_MAX and UINT64_MAX, checking bounded reads,
persistent page requests, and host coherence operations. An external execution
of the parser's actual algorithm covers up to 2048 pages, including overflow
across three passes, without lost pages or damaged guards. The generated parser
SPIR-V validates for Vulkan 1.3. These CPU harnesses do not replace Vulkan testing.

All 784 captured SPIR-V files validate with the maintenance8 offset allowance
supported and enabled in this capture. An initial validation command omitted
that allowance; the resulting three offset reports were not bugs on this GPU.

The supplied OpenOrbis dynamic-pointer-pages probe has not been run on hardware.
No native game run of this candidate has been performed here. In particular,
this change does not replay a dispatch that already faulted; persistent damage
from a first-use miss may require a further correction. The capture must show
whether repeated misses disappear and whether the sword, wind and world geometry
actually return before attributing visible progress to this candidate.

Use the isolated runner with Continue selected: N at 150 seconds, capture through
300 seconds. Compare resource misses and the menu/world render outputs together.
