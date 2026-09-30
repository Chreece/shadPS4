# NGS2 audio foundation — implementation milestone 1

This is **internal implementation code, not an enabled game-audio fix**. The existing
`sceNgs2*` entry points, decoder, renderer, GPU code, audio device settings and ES-DE
launchers are unchanged. In particular, passing these tests does not establish
working in-game audio, correct speaker mapping or cutscene timing.

## Implemented

- Bounds-checked RIFF/WAVE metadata parsing for PCM16 (including extensible PCM)
  and the observed ATRAC9 extensible-WAVE layout. Parsing never reads payload
  samples or changes the input; results contain values, not borrowed pointers.
- ATRAC9 configuration decoding, encoded superframe sizes, sample-frame counts,
  encoder delay and one integral forward `smpl` loop. Loop ends are converted
  from inclusive file positions to exclusive, delay-trimmed sample positions.
- Checked calculation of the enclosing PCM frames / ATRAC9 superframes for an
  audible sample-frame range. This **does not** define decoder seeking/preroll or
  the public `OrbisNgs2WaveformBlock` contract.
- A synchronized, typed system/rack/voice registry. Successful rack creation
  publishes all its voices together; invalid handles and wrong handle types are
  rejected; destroying a parent invalidates descendants. Tokens are not pointers
  and are not reused during a registry lifetime. Queries return copies.
- Source channel counts and masks are retained, with 1–8 channels supported.
  No stereo coercion, mono duplication, output downmix or speaker permutation is
  performed. An internal rack's channel capacity is not an audio routing matrix.

The default registry quotas are configurable **host resource budgets**, not
purported console limits. The supported system rates and grain granularity follow
`ngs2_impl.cpp`; requiring the active grain not to exceed its maximum is an internal
invariant to check again when the guest adapter is added.

## Evidence and boundaries

The supplied bounded RDR trace contains ATRAC9 config bytes `fe 40 05 f0`, mono
24 kHz input, 192-byte superframes, a 12-byte `fact` chunk and 128 delayed sample
frames. The first traced file has 34,534 audible sample frames, data offset 168,
13,056 encoded bytes and a loop covering the audible range. The output request is
separate: 256 frames × 8 channels × 2 bytes = 4,096 bytes, at 48 kHz.

The tests construct synthetic containers from these metadata values with **zero
placeholder payloads**. No game audio, game dump or firmware is included. These
fixtures validate parsing and byte ranges, not ATRAC9 decoding or audible output.

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
belongs to the future guest-memory adapter, not this parser.

## Run the focused tests

No third-party downloads, host package changes, game or GPU are required by this
standalone CMake project. Run from the repository root:

```sh
cmake -S tests/ngs2_hle -B build-ngs2-hle -DCMAKE_BUILD_TYPE=Debug
cmake --build build-ngs2-hle --parallel 4
ctest --test-dir build-ngs2-hle --output-on-failure
```

For address and undefined-behavior sanitizers (Clang/GCC):

```sh
cmake -S tests/ngs2_hle -B build-ngs2-hle-asan \
  -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_BUILD_TYPE=Debug -DNGS2_SANITIZERS=ON
cmake --build build-ngs2-hle-asan --parallel 4
ASAN_OPTIONS=detect_leaks=1 UBSAN_OPTIONS=halt_on_error=1 \
  ctest --test-dir build-ngs2-hle-asan --output-on-failure
```

The two test executables contain 37 named cases: 23 parser/range tests and 14
registry tests. They include all 13,224 shorter prefixes of the observed-format
fixture, 10,000 deterministic metadata mutations, published ATRAC9 rate/channel
indices, eight-channel layouts, resource limits, stale handles and concurrent
creation/lookup/destruction. Checks remain enabled in Release/NDEBUG builds.

## Still required before a game-audio build

1. Map these values and lifetimes into the existing guest ABI, including caller
   allocators, callback context, exact error codes, option extensions and rollback.
2. Validate the public waveform/block fields, codec seeking and loop/preroll rules.
3. Decode ATRAC9 with per-voice state; implement streaming/resampling and real
   playback positions, completion state and callbacks.
4. Implement the supplied sampler/submixer/master routing and render valid samples
   into the requested eight-channel buffers, then check channel mapping end-to-end.

Do not deploy this helper-only milestone to claim the missing sound is fixed.
Do not replace the noise with silence or fabricate completion flags as a substitute
for implementing those remaining stages. Preserve the working sparse-queue build.
