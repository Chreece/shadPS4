<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Bounded NGS2 audio diagnostics

The first user test of f00bef80 reported no intro noise, continuing but silent
cutscenes, and audible gameplay with dialogue crackles. The supplied log confirms
that revision and 48 kHz eight-channel SDL output. It does not identify the cause
of either remaining symptom. Initial unresolved-import warnings precede dynamic
NGS2 HLE loading and do not establish failed runtime calls.

Set SHADPS4_NGS2_DIAGNOSTICS=1 for the test emulator process and capture stderr.
It is disabled by default, independent of the normal log filter, and does not
change audio, GPU, game configuration or decoded samples. No guest audio payloads
or file paths are logged. This is diagnostic instrumentation, not a sound fix.

NGS2_DIAG records include elapsed milliseconds since the first diagnostic call:

- Validated voice command IDs, rack type, result, queue length and channels.
- Public voice-control/render failures, waveform parse failures and format details.
- Decoder errors and queue exhaustion. A queue ending can be normal completion;
  it is not by itself evidence of an underrun.
- Render peaks per channel, pre-conversion clipping/non-finite sample counts,
  graph size, grain size and output sample rate. Peaks do not prove correct
  physical speaker mapping. Clipping counts mean magnitude greater than one.
- Render duration in microseconds, including diagnostics and guest callbacks.
  This is not a host audio underrun counter.

Each event key emits its first four occurrences and then powers of two. Render
and timing records instead emit the first four and every 256th occurrence.
Storage is fixed at 64 keys in each of eight categories, so command churn cannot
consume render/error slots. Output is capped at 2048 lines per process, with
480-byte message buffers. Mutex serialization prevents interleaved diagnostic
lines from concurrent callers. stderr I/O can still affect timing; compare an
untraced run before attributing performance effects to the emulator.

The next isolated deployment must capture stderr separately from shad_log.txt,
keep the normal ES-DE fallback and existing 7.1 settings, and retain rollback.
Do not substitute an older binary: focused and complete emulator CI must pass
for the diagnostic revision before it is offered as ready for a game test.

Standalone checks cover logarithmic suppression, periodic sampling, global output
bounds and category storage isolation. The existing audio runtime cases also run
with diagnostics enabled to check that sample and return-value expectations hold.

Local validation: 112 unique cases passed in Debug, Release and ASan/UBSan.
Local sanitizer leak detection was disabled due to the previously documented
execution-environment limitation; remote Clang leak checks remain a readiness gate.
The 14 audio runtime cases are additionally registered with tracing enabled in CTest.
Full emulator builds remain a remote CI gate, not a local validation claim.
