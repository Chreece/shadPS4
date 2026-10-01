# RDR precise-readback comparison

The 2026-10-01 graphics capture at 16:39 local time identifies main
`10ff9e19a7d94340aaedd1e333f1a11abeeb9e75`, one emulator, RADV 25.0.7,
98.86% VRAM use and `GPU.readbacks_mode=0`. Its renderer log confirms precise
occlusion queries and 48 kHz/eight-channel audio. No device-lost or allocation
failure is recorded. Copy-layer warnings describe colour-array expansion;
they alone do not establish the cause of disappearing geometry.

The user then freed 16 GB of VRAM, started the game afresh, and still observed
missing scenery and character elements. Simple VRAM shortage is therefore
unlikely to explain the persistent rendering defects.

`readbacks_test.py` selects only `GPU.readbacks_mode=2` (Precise) in the
CUSA36843 per-game JSON, using the existing build. The branch's
`GpuReadbacksMode` enum and `MemoryTracker`/`RegionManager` code establish that
mode 0 skips GPU-to-CPU readback handling, whereas mode 2 enables read tracking.
Stale CPU-visible GPU buffers are a hypothesis for this game, not a confirmed
cause. This comparison does not change linear-image readbacks or add a GPU patch.

Exit the emulator normally before applying or restoring. The helper checks the
selected revision, preserves any existing per-game keys, backs up exact bytes,
and prints a self-contained restore command. It refuses to overwrite later user
configuration edits. The per-game override applies to CUSA36843 launches using
this shared user directory, including the normal launcher entry. Global settings,
audio settings, executable, launcher, and saves are untouched.

Use the NGS2 trace entry and compare the same route, character details, indoor
light and frame pacing. Precise readbacks may reduce performance. A successful
configuration change is not a successful game test. Restore if rendering is
unchanged or performance/stability regresses. Collect the current renderer log
if the defect persists; it must show `GPU readbacksMode: 2` for this comparison.

Run the host-side preservation checks with:
`python3 -m unittest test_readbacks_test -v` from this directory.
