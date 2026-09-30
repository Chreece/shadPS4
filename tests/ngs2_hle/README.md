<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# NGS2 public audio path

The branch now connects PCM16/ATRAC9 waveform parsing, sampler controls, routing,
submixing and mastering to `sceNgs2SystemRender`. It produces real interleaved
PCM16 or float output with up to eight independent channels. It is an experimental
HLE implementation awaiting in-game validation, not a confirmed RDR audio fix.

See [BRIDGE.md](BRIDGE.md) for supported controls, ABI evidence and limitations,
[RUNTIME.md](RUNTIME.md) for ownership, and [PLAYBACK.md](PLAYBACK.md) for decoding.
The game still submits these buffers to AudioOut; NGS2 opens no host audio device.

## Implemented

- Public waveform metadata/frame/block queries, transactional linked voice controls,
  owned sampler blocks, source-rate playback, finite/infinite loops and pitch.
- Acyclic sampler → submixer → mastering graphs, source-major matrices, port volume,
  multiple sources/outputs, mastering gain and PCM16 clipping. Eight-channel float
  processing is retained throughout; channel expansion requires an explicit matrix.
- Guest UserFx callbacks with planar channel buffers, reentrant queries and lifetime
  checks after callbacks. Invalid output descriptions fail before playback advances.

- Owned PCM16 and ATRAC9 streams decoded to interleaved float samples through the
  repository's pinned LibAtrac9, with delay trimming, audible duration, bounded
  superframe reads, sticky decode errors and history-preserving seeks.
- Host playback with rational rate conversion, linear interpolation, explicit
  finite/infinite forward loops, pause/resume/restart and duration-based EOF.
  Mono through eight-channel streams retain their individual channel samples.
- Bounds-checked RIFF/WAVE metadata parsing for PCM16 (including extensible PCM)
  and the observed ATRAC9 extensible-WAVE layout. Parsing never reads payload
  samples or changes the input; results contain values, not borrowed pointers.
- ATRAC9 configuration decoding, encoded superframe sizes, sample-frame counts,
  encoder delay and one integral forward `smpl` loop. Loop ends are converted
  from inclusive file positions to exclusive, delay-trimmed sample positions.
- Checked calculation of the enclosing PCM frames / ATRAC9 superframes for an
  audible sample-frame range. This **does not** define decoder seeking/preroll or
  the public `OrbisNgs2WaveformBlock` contract; that bridge is documented separately.
- A synchronized, typed system/rack/voice registry. Successful rack creation
  publishes all its voices together; invalid handles and wrong handle types are
  rejected; destroying a parent invalidates descendants. Tokens are not pointers
  and are not reused during a registry lifetime. Queries return copies.
- Public system/rack creation, allocator creation, destruction, info, user data,
  enumeration, rate/grain updates, voice handles and voice ownership now use the
  same tested registry. Allocator user data is initialized and retained, failed
  creation rolls back, and child handles are invalidated before cleanup callbacks.
- A guest-memory adapter checks full mapped ranges and CPU read/write/execute
  permissions. Metadata is copied before use; callback re-entry is supported.
- Source channel counts and masks are retained, with 1–8 channels supported.
  No stereo coercion, mono duplication, output downmix or speaker permutation is
  performed. An internal rack's channel capacity is not an audio routing matrix.

The default registry quotas are configurable **host resource budgets**, not
purported console limits. The supported system rates and grain granularity follow
`ngs2_impl.cpp`; requiring the active grain not to exceed its maximum is an internal
invariant now also checked against live rack capacities in the guest adapter.

## Evidence and boundaries

The supplied bounded RDR trace contains ATRAC9 config bytes `fe 40 05 f0`, mono
24 kHz input, 192-byte superframes, a 12-byte `fact` chunk and 128 delayed sample
frames. The first traced file has 34,534 audible sample frames, data offset 168,
13,056 encoded bytes and a loop covering the audible range. The output request is
separate: 256 frames × 8 channels × 2 bytes = 4,096 bytes, at 48 kHz.

The metadata tests construct synthetic containers from these values with **zero
placeholder payloads**. No game audio, game dump or firmware is included. These
fixtures validate parsing and byte ranges, not ATRAC9 decoding or audible output.
Separate decoder/playback fixtures now encode original nonzero ATRAC9 spectra,
including eight channels. Their first-frame samples are compared with an
independent DCT-IV/window formula; subsequent tests check overlap history, delay,
padding, negative coefficients, loops and rate conversion.

Public implementation references consulted for container/config layout:

- LibAtrac9, `C/src/decinit.c` and `C/src/tables.c`, revision
  `efca2e3af35562a09a9bb6deed90e45b4b824dc4`:
  https://github.com/Thealexbarney/LibAtrac9/tree/efca2e3af35562a09a9bb6deed90e45b4b824dc4/C/src
- vgmstream, `src/meta/riff.c`, revision
  `7dc938fa2f210943b37c7b6511852b516ef432ab`:
  https://github.com/vgmstream/vgmstream/blob/7dc938fa2f210943b37c7b6511852b516ef432ab/src/meta/riff.c

These are not references for every NGS2 ABI behavior. The parser rejects unsupported
codecs, other ATRAC9 `fact` layouts, multiple/alternating/backward/fractional loops
rather than silently guessing. Additional variants require separate evidence/tests.
Input pointers must designate a readable span; arbitrary guest pointer validation
belongs to the guest-memory adapter, not this parser. The public waveform entry points use that adapter.

## Run the focused tests

The standalone project needs a C/C++ compiler and the pinned LibAtrac9 submodule;
it does not require a game or GPU. Run from the repository root:

```sh
git submodule update --init externals/LibAtrac9
cmake -S tests/ngs2_hle -B build-ngs2-hle -DCMAKE_BUILD_TYPE=Debug
cmake --build build-ngs2-hle --parallel 4
ctest --test-dir build-ngs2-hle --output-on-failure
```

For address and undefined-behavior sanitizers (Clang/GCC):

```sh
cmake -S tests/ngs2_hle -B build-ngs2-hle-asan \
  -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
  -DCMAKE_BUILD_TYPE=Debug -DNGS2_SANITIZERS=ON
cmake --build build-ngs2-hle-asan --parallel 4
ASAN_OPTIONS=detect_leaks=1 UBSAN_OPTIONS=halt_on_error=1 \
  ctest --test-dir build-ngs2-hle-asan --output-on-failure
```

The seven test executables contain 109 named cases: 23 parser/range tests, 14
registry tests, 26 public lifecycle API tests, four memory-access tests, 13 decoder
tests, 15 playback tests and 14 public audio tests. They include all 13,224 shorter prefixes of the observed-format
fixture, 10,000 deterministic metadata mutations, published ATRAC9 rate/channel
indices, eight-channel layouts, resource limits, stale handles and concurrent
creation/lookup/destruction. The runtime suite also exercises callback re-entry,
allocator failure and rollback, parent removal/grain changes during allocation,
output permissions, extended rack options, bounded info writes and real public
handle invalidation. Checks remain enabled in Release/NDEBUG builds.

The standalone runtime suite links the actual `ngs2_impl.cpp` export implementations.
Only the guest memory provider is replaced with a strict mapped-range permission
model. The memory-access suite tests the exact production range-check algorithm.
The full emulator build and guest execution are separate validation gates.

## Test-build gate

The focused tests exercise the actual public exports with synthetic nonzero audio
and strict guest mappings. Full emulator compilation/linking is a separate CI gate.
A successful build is suitable for an isolated first game test; it does not establish
native ABI equivalence, correct physical speaker order or game compatibility.
[VALIDATION.md](VALIDATION.md) records the completed checks. Preserve the existing
working sparse-queue installation when trying an experimental build.
