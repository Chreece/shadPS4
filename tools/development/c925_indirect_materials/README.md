# Bounded indirect material descriptor selection

This exact-source adapter starts from the native PR106 candidate `25dd2de5d6981667387c3e1ff0af84d859c45349128e3ec4371a600d4e2932b2`. It preserves that demonstrated uniform texture-loop capability and the accepted stencil, readback, explicit-LOD, finite-image and atomic corrections. It is **not** a completed latest-upstream rebase or an automatic production-main renderer integration.

## Evidence and the specific missing path

The existing uploaded pre-resource IR for the remaining menu compute shader `5065ed7b` contains a genuine GPU `ReadLane` selector, formed from a value shifted right16. The selector addresses a504-byte record inside a real scalar buffer. Two adjacent scalar-buffer words form a pointer; the pointed-to structure supplies eight texture descriptor words and four sampler words. The old final IR loses these raw descriptor sources. The matcher was replayed against36 actual dependency nodes from that upload and recognizes the source while retaining the original GPU selector. The shader hash is **only evidence provenance**, never a production-code condition.

This is distinct from PR106's root-count loops. The upload does **not** contain the live parent-buffer extent or prove that it is small and fully mapped. No claim is made that this shader supplies the sword or every missing menu overlay.

## Production correction

Four existing files plus two headers change. Recognize only normalized nondepth, nonarray explicit-LOD/LZ samples with consistent texture/sampler pointer chains and a direct uniform parent V#. Prove that the selector-to-byte arithmetic cannot wrap; never choose a host wave lane or assume a maximum index from observed gameplay.

Read the actual parent descriptor byte extent, require complete records, and resolve the real texture/sampler words only through checked mapped ranges and the existing coherent guest-memory read path. A16-entry expansion budget is a compiler/resource limit, **not a guessed guest count**. Empty, partial, oversized or unmapped tables remain unhandled and produce a deferred reason; no prefix is silently accepted, no zero-record size is grown and no valid descriptor is fabricated.

For a valid complete table, retain the original GPU selector and use the already implemented/validated guarded normal typed sampling path. Only the selected texture is sampled. Preserve coordinates, LOD and the actual sampler words. Snapshot the complete descriptor state as a cache dependency; compare it before refreshing an old SRT walker. Changed contents/counts/mappings therefore cannot reuse baked old descriptor assumptions. Metadata/binary versions advance together. This conservative snapshot strategy can recompile when descriptor addresses change; performance and native acceptance remain to be measured.

## Actual local verification

* Planner implementation: **28,160 checks PASS** each with GCC and Clang C++20 `-Wall -Wextra -Werror -pedantic`, plus Clang ASan/UBSan.
* Actual bounded snapshot implementation using the captured native Buffer bitfield declaration: **6,848 checks PASS** each under the same compilers/sanitizers. Tests actual read lengths, zero/partial/over-budget rejection, changed image/sampler contents, invalid mappings/pointers, address overflow, tagged48-bit pointers and stride-independent real bounds.
* Extracted actual Info snapshot/cache methods compile and pass a small interface-contract fixture in both compilers. This is not the full Core memory subsystem.
* Replaying the36 captured dependency nodes through the actual matcher: **PASS** with GCC and Clang; identifies root0/record504/image-word195/sampler-word205 and retains ReadLane. No live parent-table bound was present or invented.
* **10 Python component tests PASS**, 1.848s with ResourceWarning as error: exact four-file transform, changed/repeated source refusal, actual Git forward/reverse roundtrip and bytes, cache-check ordering, source+ELF rollback, kept-source/private-profile/symlink/external-save guards, and real child success/failure/timeout cleanup.
* The newly generated full emulator, live table acceptance, new final SPIR-V and visible menu recovery have **not** been tested in this environment. Component tests do not constitute end-to-end hardware execution. No new unsupported-operation removal or graphics success is claimed from counts alone.

## Host run

The runner verifies the exact native PR106 ELF, latest twelve-file manifest and eight additional kept-source invariants, checkpoints the candidate, applies the source correction, runs available compiler fixtures, and builds incrementally with full logs and15-second quiet-stage heartbeat. Failed fixture/build restores all changed files and the prior ELF; no game is launched after a failure.

On success it runs one no-input118+2s menu capture using the existing isolated profile. The original saves/configs, protected C925/GoW binaries, SSH, Sunshine/X11 and launcher are not overwritten. Keep at least8GiB VRAM; only if below that threshold use the previously pinned same-server Ollama unload/context/expiry restoration helper. Capture fresh relevant IR/SPIR-V and run an already available validator. No packages are installed. Identical menu pixels are not classified as a freeze.

Return every outcome, including deferred parent bounds, to the common GitHub journal. This test is intended to resolve actual menu material descriptors, not to repeat the prior presentation investigation. Official upstream main checked `0614ce324be092791b758d79f93d9f1ca5b10a81`; that newer source has **not** been integrated into this isolated C925 trial.
