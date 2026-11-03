# Bounded graphics visibility capture

Set `SHADPS4_GRAPHICS_DIAGNOSTICS=1` for one diagnostic launch. Without it,
diagnostics do not increment counters or write output. Six event counters each
emit their first eight records and subsequent powers of two. Each record uses
a fixed 768-byte field buffer, with one `GRAPHICS_DIAG` line written to stderr.
Counters are shared across call sites and template instantiations.

Events report diagnostic initialization, pixel-pipe counter dumps, predication
packets, conditional execution, image-copy layer/type mismatches, and rejected
texture containment. They do not change query results, guest memory, draw
decisions, image selection or copy regions. Addresses are guest surface/query
addresses; no guest data payload is copied into the trace.

The 2026-10-01 14:25 local capture runs main 038bb3d8 with one core, eight audio
channels at 48 kHz, and 16 image-copy layer warnings. The previous capture had
15 similar warnings. Neither contains renderer errors or device loss. These
warnings can also occur while preserving layers during an image expansion and
are not proof of the reported graphics defect. Normal log filtering hides
pixel-pipe debug events, so that capture cannot establish whether RDR uses
the synthetic visibility counters.

This instrumentation is the next evidence step, not a graphics fix. Upstream
PR #4610 remains a draft occlusion/predication candidate without RDR validation.
Run past the affected trees and enter the building with visible outdoor light,
then close the game and collect the current diagnostic trace and renderer log.
An enabled record plus a pixel-pipe record establishes use of the stubbed
counter path; it does not alone prove the cause of the visible artifact.

The standalone checks verify disabled behavior, bounded sampling, and shared
event counters. Full emulator compilation and actual GPU behavior are checked
in the local Docker/game test, not by these CPU-only tests.
