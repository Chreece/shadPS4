# GoW91: small formatted reads and cached image coherence

This is an experimental continuation, not a claim of rendered gameplay.

## Verified Phase90 input

Archive: gow90-original-profile-20261014-090252-2130273.tar.gz.
The process remained running until the 60.68-second limit; shutdown returned 0.
The prior texel-image alias assertion was absent. Four successful partial image copies
were logged: image 0x21ece7000, requested address 0x21ece70c0, 20288 bytes, image offset
192, image guest size 552960. Sixteen completed GPU readbacks (8 guest, 8 display)
were independently decoded and were entirely black. The log recorded 182 distinct
DMA missing-residency addresses with no repeats. All checkpoint and restoration
checks passed. All 514 SPIR-V binaries were collected; none was omitted.

Baseline binary: tests/gow90-build-20261014-090219-2130273/shadps4.
SHA-256: 6683102205ff21c3e0f20c14952663fa9d153eb561c9aa5490cb1c294a8e62bd.
Supplied buffer-cache source SHA-256:
d142015a3a92d3c94a960efe209600b9392d324a91bb70ac10a69b8712811227.

## Source defect and correction

ObtainBuffer's small read-only stream shortcut ignores is_texel_buffer. It can
return a copy of CPU backing before SynchronizeMemoryFromImage runs, although the
texture cache tracks GPU image modifications separately from buffer dirty ranges.
The caller passes desc.is_formatted as that flag.

The candidate routes nonempty formatted reads through the existing arena and
image/metadata synchronization path. Ordinary read-only streaming still uses the
same 64 KiB threshold and GPU-modified-region safeguard. Zero-length read behavior
is preserved. It does not force the is_written flag or change shader data/logic.
The Phase89 MakeDmaResident and Phase90 SynchronizeMemoryFromImage method bodies
are verified byte-identical before and after this patch.

This archive did not instrument the old shortcut. It does NOT establish that this
single defect caused all black frames. In particular, the four already-successful
Phase90 image copies must not be mislabeled as skipped copies. TEXEL_STREAM_ROUTE
logs in Phase91 report actual newly routed reads and successful image/metadata
synchronizations. Logs are bounded, so their counter maxima are lower bounds.

## Checks actually run locally

- Actual old/new ObtainBuffer method bodies compiled under ASan and UBSan with a
  cache/memory model. The old method reproduced a stale CPU-zero read for a modeled
  20288-byte GPU image alias. The new method read the model's current GPU image.
- 3174 corrected-method cases, including 3000 seeded randomized cases, passed.
- Eight Python source/preservation, Git-apply, rejection and parsing tests passed.
- A real five-generation Ninja/C++ overlay test retained metadata headers, presenter
  source, DMA headers/objects and the range fix across the next single-unit update.
- Published runner blob e0aef417d1e74883e5be136c2804837978cd209f matches the tested local file.

Full shadPS4 compilation and AMD GPU execution remain unverified. Use the fresh
GPU-image pixels, actual synchronization markers and crash/fault evidence to judge
this candidate. No shader skips, CMake regeneration, historical log input or
replacement of the Phase90 checkpoint is intended.
