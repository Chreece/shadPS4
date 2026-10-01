# Pixel-pipe occlusion query writeback

The RDR trace from diagnostic main d5c5acc0 records 16,384 pixel-pipe dumps in
104 seconds, with begin/end result addresses eight bytes apart. The existing
implementation increments every dump by 0x2ffffff, so an occluded probe still
receives a positive result. No SET_PREDICATION, conditional-execution or
containment-miss record was observed during that capture. Sampled copy-layer
records show same-address R32_SFLOAT colour-array expansion, not depth copies.

This branch adapts the query/readback part of cuesta4's upstream PR #4610,
commit 3559530df5aec4067a95912af13ef14896b65ada. It does not import conditional
rendering or shader-reduction machinery. Query slots cover individual real
draws, preserving scope across rendering changes. Precise counts are enabled
when supported; other devices provide Vulkan's zero/nonzero visibility result.
Slots are host-reset when supported, or command-reset outside rendering.

Completed results run on the scheduler's ordered priority callback thread.
They release slots directly, avoiding the draft's nested DeferOperation call
while this branch's non-recursive pending-op mutex is held. Slot availability
is atomic, query host access is locked, and callback state has shared lifetime.
Resets follow the same completion order as dumps. Guest fences wait for pending
counter writes, and a command-processor wait submits outstanding query work.
This conservative fence synchronization can affect performance.

Vulkan returns a device-wide sample count. It is partitioned over guest pipe
counters so the aggregate delta remains correct for both 8 and 16 pipes.
Missing/exhausted query results fail visible rather than incorrectly hiding
geometry. Writes require an accessible writable guest range. The null-GPU
fallback retains the previous behavior. Existing audio, sparse queue, startup
and texture fixes are retained.

CPU regression checks cover zero and positive query deltas, valid bits, total
count conservation and the alternating-qword write span. They do not prove GPU
rendering or synchronization correctness. Full Linux compilation runs in the
local Docker helper; then test the same indoor sun/light and outdoor trees.
The trace records `query-control` and `query-result` including sample counts
and whether the measurement completed without missing slots/results.

The active stub is confirmed by the trace. Its responsibility for the visible
indoor artifact is still a hypothesis until the local game test confirms it.
Source: https://github.com/shadps4-emu/shadPS4/pull/4610
Vulkan query semantics: https://docs.vulkan.org/spec/latest/chapters/queries.html
