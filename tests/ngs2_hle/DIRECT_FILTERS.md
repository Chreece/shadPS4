<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Direct filters

Sampler command `0x1000000a` and submixer command `0x20000006` now implement
direct filters (`type=0x20`) at location 0. The previous implementation accepted
only identity coefficients. The captured game issued 260 rejected requests with
index 0, location 0, type 0x20 and mask 0, including these coefficient sets:

| i0 | i1 | i2 | o1 | o2 |
| --- | --- | --- | --- | --- |
| 0.933768392 | 0 | 0 | 0.0662315786 | 0 |
| 0.671312332 | 0 | 0 | 0.328687638 | 0 |

For each channel, the recurrence is:

```text
y[n] = i0*x[n] + i1*x[n-1] + i2*x[n-2] + o1*y[n-1] + o2*y[n-2]
```

Feedback coefficients are added with the supplied signs. Coefficients are not
normalized or negated. A set bit in the low byte of `channelMask` bypasses that
channel and clears its delay history. Mask 0 filters all voice channels. There is
no cross-channel mixing, bass synthesis, output-layout change or LFE redirection.
Stages run in ascending filter index after sampler decoding/input bus mixing and
before UserFx, port matrices and mastering gain.

## Static reference

The reference was a publicly hosted ELF module, inspected statically without
executing it or installing it on the user's system:

- Repository: `Vlad112003/PS4-Firmware-sys_modules11.00`
- Snapshot: `3954d3436f50735b0a2f201018397d60d5a628bc`
- Path: `sys_modules/libSceNgs2.sprx`
- Size: 372112 bytes
- SHA-256: `fbe55d7da893743a6267798acff1a2093ab7ac5f1606b48f8f3d3c5a8d7e74f6`

The repository labels it firmware 11.00; that attribution was not independently
authenticated. No native binary, disassembly, SDK header or lifted implementation
is included in this change. The HLE is an independently written scalar recurrence.

Addresses below are virtual addresses in this specific ELF:

| Address | Observation |
| --- | --- |
| `0x15531`, `0x160d8` | Sampler/submixer filter controls require 48-byte parameters and validate the index against rack capacity. |
| `0x1b880` | Shared setter stores location, type and mask as bytes and copies all five direct coefficients. |
| `0x1bb0b`–`0x1bb5c` | Type 0x20 copies coefficients unchanged into the DSP state. |
| `0x1bf4e`–`0x1c09a`, `0x1c1d0`–`0x1c23f` | Vectorized recurrence combines input and output history with positive multiply/add feedback. |
| `0x1c124`–`0x1c13f`, `0x1c0b0` | A set channel-mask bit copies input through and clears that channel's four history values. |
| `0x157d6`, `0x16316` | Location 0 runs after source/input mixing and before the later processing stages and UserFx. |

This establishes the selected static behavior, not bit-exact SIMD rounding or a
successful native-console comparison. Non-direct types and locations 1–17 remain
unsupported; the common native pipeline's 16-bit stage mask bounds HLE slots to 16.

## HLE state and error handling

Each voice/filter/channel has separate input/output history. Coefficient changes
preserve it; configuration is snapshotted for each render grain. Reentrant controls
apply new coefficients on the next grain without overwriting progress made by the
current grain. Setup owns fresh progress, preventing an in-flight old setup from
polluting the new history. Linked parameter batches remain transactional.

Pause/resume preserves history. Existing play/restart, kill and setup semantics
clear it. Filter settings remain configured across setup. These lifecycle/timing
choices follow the current HLE model and have synthetic coverage; they have not
been compared against native event execution. Explicit rack `maxFilters` is
respected, including zero. Base-only/default rack options use a 16-slot HLE limit.

Nonfinite coefficients and nonzero reserved fields are rejected without changing
the voice. Finite coefficients that overflow while rendering fail the voice, clear
its history, silence its grain and skip that voice's UserFx. This is defensive HLE
behavior, not a claim about native overflow handling. Other voices continue.

The existing sampler completion policy is unchanged: a closed, exhausted queue
becomes idle. Its last grain is filtered, but filter tails do not extend playback
beyond that grain. Open queues continue to process silence while starved. Long
tails, native lifecycle equivalence and other processing locations need separate
evidence before extending this implementation.

## Local test

Build main using the existing Docker installer, with the game and ES-DE closed.
The installer checks the new binary before switching the default and retains a
rollback core. Saves, settings, physical 7.1 mapping and Sunshine are unchanged.

Launch the ordinary game entry. Compare a familiar dialogue/cutscene and outdoor
gameplay sequence with the working build, including pause/resume and a second
launch. Listen for unexpected muffling, level jumps, silence or new distortion.
This adds the game's requested filtering; it does not establish a bass-channel
fix or guarantee an audible improvement. Game audio remains experimental.
