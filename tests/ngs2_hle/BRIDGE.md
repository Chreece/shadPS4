<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Public waveform, control and render bridge

`hle/waveform_abi.cpp` and `hle/voice.cpp` replace the corresponding public stubs.
These are general HLE paths, with no title IDs or game-specific sound substitution.
They share the lifecycle registry and guest-memory checks from `ngs2_impl.cpp`.

## Supported path

ParseWaveformData fills the waveform format, payload range, frame sizes, delay,
duration and forward-loop block descriptions for PCM16 and ATRAC9. FrameInfo and
CalcWaveformBlock validate their complete outputs before writing. Invalid formats,
unsupported codecs and arithmetic overflow return errors rather than empty success.

ATRAC9 `configData` is a bit-packed scalar in codec order: observed guest value
`0xfe4005f0` maps to decoder bytes `fe 40 05 f0` (mono, 24 kHz). Both public
parsing and incoming format validation use that order explicitly, independently
of host endianness. The earlier bridge reversed both directions, so parser-to-
setup round trips passed while guest-authored scalar configurations were rejected.

VoiceControl copies and validates a signed-relative linked parameter list before
publishing its changes. A malformed later parameter rolls back the whole batch.
Headers are bounded and cycles rejected. Supported controls are:

| Control group | IDs implemented |
| --- | --- |
| Generic | 1 matrix levels, 2 port volume, 3 port matrix, 4 zero delay, 5 patch, 6 event |
| Sampler `0x10000000` | 0 setup, 1 queue/continue/clear blocks, 4 exit loop, 5 pitch, 8 UserFx, 9 peak enable, 10 identity direct filter |
| Submixer `0x20000000` | 0 setup, 4 UserFx, 5 peak enable, 6 identity direct filter |
| Mastering `0x30000000` | 0 setup, 4 gain, 5 output ID |

Events are ordinal: play 0, stop 1, immediate stop 2, kill 3, pause 4, resume 5.
Playing flags are 3, paused 5, failed 16, and an empty sampler reports 32. Stop
preserves the current queued blocks for restart; kill clears them. There is no
release envelope yet, so both stop variants stop immediately. A queued block is
removed only when its sample duration finishes. State includes source samples
consumed, completed-block byte totals, current block metadata and measured peak.

Sampler payloads are copied into decoder-owned storage at append time. Guest
mutation/unmapping after append cannot change the decoded audio. Limits are host
budgets: 64 MiB per voice, 256 MiB per system, 256 blocks, 64 ports/matrices and
16 million graph scratch samples. Explicit rack block/port/matrix limits also apply.

Waveform-block flag bit 0 keeps the queue open for further submissions; it does
not discard queued audio. When that queue empties, the voice stays playing/empty
until more data arrives or a submission without bit 0 closes it. Bit 1 continues
ATRAC9 encoded data with the prior decoder's transform history. Bit 2 clears the
queued data, retained history and pending exit-loop request before adding the new
descriptions. Clear preserves run state and cumulative sample/byte counters;
setup and play retain their existing reset behavior. All controls are staged, so
a malformed later block or control also rolls back open/close and clear changes.
The earlier flag-1-as-replacement inference was incorrect for streaming. The new
interpretation follows the public reference below and matches the observed
flag-1/flag-3 sequence; native-console equivalence is still unverified.

Raw `numSamples=UINT32_MAX` resolves to this block's encoded capacity minus skip,
after checking alignment, skip and storage bounds. It never authorizes reading
beyond the copied payload. Ordinary oversized counts remain errors. Continuations
currently require complete ATRAC9 superframes, matching configuration, zero skip
and repeats, and an unspecified count (0 or UINT32_MAX). The preceding block must
reach its full encoded extent without looping. History is transferred only when
rendering consumes that prior block, never while a control transaction is staged.
Retained history also retains one bounded payload, counted in storage limits.

Render validates all output buffers and active routing dimensions before advancing
voices. An idle or paused voice may have incomplete routing during reconfiguration;
its unused matrix does not block the rest of the graph.
It processes an acyclic graph in dependency order, mixes every incoming patch,
applies source-major matrices and port levels, then writes exactly one grain into
each supplied PCM16 or float output. PCM16 saturates; float retains headroom.
Direct routes preserve matching channel indices and leave unmatched outputs zero.
No implicit mono duplication, stereo reduction or physical speaker permutation is
performed. Master LFE gain currently targets channel index 3 when there are at least
six channels; physical layout needs guest/device verification.

UserFx gets a planar copy of the grain and its three user-data values. It executes
outside the runtime mutex. Queries can reenter; a nested render on the same system
returns an error. Configuration (including channels, matrices and routing) is
snapshotted for the grain. Concurrent or reentrant controls no longer discard a
grain just because they replace a live voice entry. Queue progress and counters
remain shared under the runtime mutex; appended blocks and events update that
progress. A setup instead owns independent progress so the old grain cannot
advance a newly configured waveform. Failed control batches still publish nothing.

Destroying a snapshotted voice aborts that render before output writes. Decoder
state already advanced before a lifecycle abort is not rolled back. Output
permissions are rechecked after callbacks. Negative callback results
or nonfinite samples mark the voice failed and return an error.

## Explicit HLE assumptions

These choices have synthetic tests but are not claimed as verified console ABI:

- Block offsets are added to the control's `data` pointer. For parser-produced
  blocks, supply the payload base (`data + info.dataOffset`). The captured caller
  also supplies a container base with the payload offset included in each block.
  CalcWaveformBlock sample positions include delay; parsing adds the file's delay.
  Zero requested samples yields a zero-size block. ATRAC9 blocks retain the prefix
  from the first superframe and skip decoded samples, preserving transform history.
- `numRepeats` means extra traversals; `UINT32_MAX` means unlimited. RIFF play_count
  zero maps to unlimited, positive counts to count minus one. Loop ends are exclusive.
- FrameOffset/FrameMargin, nonzero setup flags, block flag bits outside 0x7,
  and nonzero port delay are
  unsupported. Matrix arrays are either packed source × destination or fixed 8 × 8.
  A mastering voice defaults to output 0 until explicitly assigned an output ID.
- Grain/rate settings and voice configuration are snapshotted for each render.
  Newly created or previously inactive voices join the following grain. Stop/pause
  can suppress a voice not yet processed in the current grain. Setup changes take
  effect on the following grain; the prior configuration can finish the current one.
  These callback/control timing choices remain HLE assumptions.

## Evidence

The user's saved bounded trace confirms PCM16 type `0x12`, 256 × 8 × 2-byte render
buffers, six-channel submix setup, eight-channel mastering setup, 48-level routing
matrices, generic controls 1/2/3/5/6, submixer UserFx `0x20000004` and an identity
sampler direct filter (`type 0x20`, i0 = 1, other coefficients zero). It also supplies
the ATRAC9 container metadata recorded in README.md. The original parser was a stub
during that capture, so its zero sample counts do not establish native semantics.
No game payload or raw pointer log is included in the repository.

Opt-in diagnostics (`SHADPS4_NGS2_DIAGNOSTICS=1`) include failure-specific block
metadata and validation reasons. These have independent bounded sampling and a
256-line reservation within the 2,048-line process limit, so ordinary request/state
traffic cannot consume the entire failure allowance. Capture limitations and the
latest unresolved cutscene evidence are recorded in [VALIDATION.md](VALIDATION.md).

Public ABI facts were cross-checked against these source snapshots; no proprietary
SDK or psOff implementation code was copied:

- [psOff types](https://github.com/SysRay/psOff_public/blob/a36de91aa9c87fcadc28e22a0468b4e535380446/modules/libSceNgs2/types.h): waveform numbers, control/event IDs, state masks and structure layouts.
- [Kyty Audio.cpp](https://github.com/InoriRus/Kyty/blob/4733b7e1c91b10554a52007903d74dc76c39a230/source/emulator/src/Audio.cpp): signed linked headers and ordinal voice events. Its dummy rendering behavior is not used as a DSP reference.
- [Public PS4 application](https://github.com/PhilNCL/PS4/blob/57379b0f4c73bd5f822cdc264444ccd705690779/GraphicsSkeleton/PS4AudioSystem.cpp): allocator creation, 7.1 mastering and game-owned output buffers.
- [KytyPS5 NGS2 streaming](https://github.com/KytyPS5/KytyPS5/blob/b7a1fac898be93bfe0c282752a7a386fd5202486/src/libs/ngs2.cpp): open-queue, encoded-continuation and queue-clear flag interpretation. This is an emulator reference, not console documentation; its implementation code was not copied.

## Remaining limitations and test scope

General filters, envelopes, compressor/distortion/limiter, generic voice completion
callbacks, address replacement, file/user waveform parsing and guest multi-call
lock semantics are unfinished. Unsupported VoiceControl commands return an error;
unmodified exports outside this bridge may still be stubs. Identity filter setup
is accepted because it has no sample effect; other filter parameters fail explicitly.
Peak-enable controls are accepted, with peak measurement always available.

Queued blocks now carry rate-conversion phase across transitions and starvation.
Their interpolation lookahead is still separate, so a transition at unequal rates
can hold a boundary sample instead of interpolating into the next block; this is
not yet a seamless streaming resampler. Internal loops retain phase. ATRAC9
seeks decode from the beginning, so late loop points may cost too much render time.
Linear interpolation has no anti-aliasing filter for downsampling.

End-to-end tests call public parse → setup → blocks → patch → play → render with
original nonzero PCM/ATRAC9 fixtures. They check eight independent channels, delay,
duration, matrices, two-source mixing, clipping, pause/pitch, guest buffer lifetime,
rollback, stale/cyclic patches, callback reentry/destruction and permission revocation.
They establish host behavior, not native ABI equivalence or audible game success.
