# Pixel-pipe occlusion query writeback

## Follow-up: count control correction

The user tested combined main 2b82d291: outdoor light no longer appeared indoors,
but disappearing/reappearing geometry became worse. Its trace ends with exit_code=0
and contains 32,768 sampled-event progression for controls/results; sampled query
results include both zero and positive counts, all with complete=1. This confirms
query execution, not correct draw coverage or a complete graphics fix.

Review found that the PR #4610 adaptation incorrectly toggled counting on every
PIXEL_PIPE_STAT_CONTROL. AMD defines this event as selection of the counter to
dump/reset, not start/stop. Counting is controlled per draw by DB_COUNT_CONTROL
(0x28004 / register word 0xA001). Repeated control packets must be idempotent.

The correction maps that register, snapshots enabled ZPASS bank masks for each
draw, and keeps four independent totals. A control packet selects a dump/reset
bank. Resetting one bank retains other totals. Z-fail/stencil-fail/depth-bounds-fail
or single-slice counting cannot be measured by the existing Vulkan query path;
these are marked incomplete and conservatively positive rather than false zero.
The existing paired-qword 8/16-pipe output layout is retained; other dump stride
and instance-mask layouts are not implemented by this correction. Raw control
words and count-register/mask changes are included in the bounded trace.

The counter-control regression covers register masks, repeated selection, bank
isolation, selective resets and unsupported modes. It does not establish the
cause of all flicker; the next local test must check both geometry and indoor light.

Primary sources:
- AMD CIK 3D Registers v2, VGT_EVENT_INITIATOR and DB_COUNT_CONTROL:
  https://docs.amd.com/api/khub/documents/9fuBVmqajj07G~5~aeTUig/content
- Mesa GFX7 register fields:
  https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/src/amd/registers/gfx7.json
- Mesa pixel-pipe packet fields and preamble:
  https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/src/amd/common/sid.h
  https://gitlab.freedesktop.org/mesa/mesa/-/blob/main/src/amd/common/ac_cmdbuf.c
- AMD PAL documents control selection separately from begin/end dumps:
  https://github.com/GPUOpen-Drivers/pal/blob/dev/src/core/hw/gfxip/gfx9/gfx9OcclusionQueryPool.cpp

## Initial implementation and evidence

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
