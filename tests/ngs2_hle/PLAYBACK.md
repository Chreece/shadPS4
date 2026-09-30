<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Host decoder and playback engine

`hle/decoder.*` and `hle/playback.*` are built into the emulator and exercised by
the standalone tests. They are not yet connected to guest voice controls or
`sceNgs2SystemRender`; they do not make the current game produce audio.

## Decoding

AudioDecoder parses a bounded RIFF span, copies its encoded data payload and owns
one LibAtrac9 handle plus one decoded frame. No guest pointers are retained.
Read writes only the returned number of interleaved sample frames; EOF returns
zero successfully. PCM16 uses signed little-endian samples scaled by 1/32768;
ATRAC9 uses the pinned codec's float conversion (division by 32767). Neither path
changes channel count or order. Source channel masks remain metadata, not a claim
that the console's mastering speaker order has been established.

Every ATRAC9 call is bounded to the remaining bytes of one superframe. Consumed
bytes must be positive and within that bound even if the codec reports success:
the pinned bit reader can zero-extend exhausted input. Frames need not have equal
byte lengths. Only the final frame skips superframe padding. Nonfinite samples
and codec errors stop decoding without publishing that frame. Previously copied
valid frames may be returned alongside an error, which persists until a seek.

Encoder delay is discarded across as many frames as necessary, and output stops
at the audible sample count from `fact`. Seeking resets the codec and decodes from
the beginning to recover transform overlap history. This is deliberately exact
but linear-time; long assets and repeated late loops will need validated decoder
checkpoints before integration into a real-time render path.

The submodule is pinned at `946e05a9212976626a9f5e52f29c0a7202871f29`.
Its initializer writes shared transform/Huffman tables on every call. AJM and
NGS2 therefore share an exclusive initialization / shared decoding lock. This
permits parallel independent decoders while preventing table writes during decode.
Instances themselves still require external serialization.

The shared CMake helper builds a corrected copy of `utility.c`, leaving the
submodule checkout untouched. It fixes the initialization-time shift by 32 in
zero-bit reversal and the signed left shift used for negative sign extension.
Patch sites are checked so a dependency update cannot silently drop the fixes.
Both the production and focused builds use these corrections. No sanitizer
checks are disabled for the codec; its C sources are instrumented in sanitizer CI.

## Playback

Playback accepts source/output rates from 8 to 192 kHz and emits the source's
channel count. An integer phase tracks the exact rate ratio independently of
render grain sizes. Linear interpolation uses the following source frame, including
across a loop boundary; the last sample is held for its remaining fractional
duration. Downsampling currently has no anti-aliasing filter and needs a quality
resampler before claiming production audio fidelity.

Start rewinds, Stop clears playback state, Pause preserves fractional position,
and Resume only resumes a paused stream. A completed stream enters Finished based
on its duration; decode errors enter Failed. These are internal states, not guest
state flags or completion callbacks. Render reports the number of produced frames
and zeroes any unused tail. A span containing a partial channel frame is rejected
without modification or advancement.

PlaybackLoop uses exclusive, delay-trimmed source positions. Its explicit repeat
count means **extra traversals**, with nullopt for unlimited repeats. Finite loops
continue through the source tail afterward. RIFF play_count and guest numRepeats
are not automatically assigned this policy: their ABI mapping still needs evidence.
The caller must explicitly supply a loop; parsed `smpl` metadata alone does not
start looping.

## Evidence and remaining work

`audio_fixture.h` creates original PCM and nonzero mono/eight-channel ATRAC9 test
data using the pinned codec's published packet layout. It includes superframe
padding and independently varying frame/channel spectra. The first mono frame
is checked against a direct DCT-IV/window calculation rather than a second call
to the codec. Tests also verify negative coefficients, delay trimming, exact
replay after seek, chunk-independent rate conversion, loop interpolation, pause,
EOF, malformed packets and concurrent initialization/decoding.

These are focused deterministic tests, not a codec corpus, native NGS2 comparison,
speaker-layout validation or an in-game audio test. The remaining bridge needs
verified waveform/control IDs and block semantics, guest buffer validation,
voice ownership/budgets, routing matrices, mixing, callbacks and system render.
