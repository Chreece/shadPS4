# Restore accepted renderer invariants in native C925

This is a **source-preservation repair**, not a newly invented stencil fix or a claim that two changes restore every missing GoT surface. The generic algorithms already exist in merged fork PR69 (`86ebcf6249fae2a709b98790760de639e6978bef`) and PR68 (`b8db275e6605b4183aa27c445bb39661d991cdd1`). They are missing from the archived native C925 files used in the recent candidate lineage.

## Current evidence

The native PR96 resume `got-c925-revalidation-resume-20261028T135245496700Z.tar.gz` (SHA256 `f3fd6f92bd8c12b541df3b9e5661baa77a5f9dd98d51b119aff6a46efb44d91c`) reached the menu for its bounded run. It rendered text/particles/fog and black incomplete foreground, but not complete scenery. It logged **zero indexed-table capacity revalidations**, the same seven zero-record material rejects, 269 unflattened-source warnings, and no Vulkan device loss. This is **negative visual acceptance of PR96**, not a reason to rerun unchanged cache code.

The old stencil whole-op_val warning reaches occurrence **1024** in this run. The archived rasterizer contains the old choice rather than accepted masked compare/write composition. The archived texture constructor also omits the Precise-mode linear-image-readback fallback; the current host file must match its recorded SHA before it is changed. Runtime `readbackLinearImages=false` alone does not prove the internal constructor state.

Earlier PR70 demonstrated exact stencil merges but **no complete menu recovery**. The later temporary linear-readback toggle likewise did not recover full scenery. Restore these accepted general correctness invariants, but do not repeat either as a newly discovered single root cause. Keep GPU-indexed typed 2D/3D/Cube routing and the PR95/PR96 source intact. Remaining compute-resource reports and actual draw/fragment consumption still need causal evidence.

## Production changes

Only two source files change. `restore_kept_renderer.py` restores the exact accepted PR69 reference-selection algorithm in `vk_rasterizer.cpp` and the accepted PR68 constructor predicate in `texture_cache.cpp`. No guest title/hash conditions, arbitrary bounds, fake records, skipped draws, saved settings changes or eager full tiled-image copies. Both source preimages are SHA-256 checked before either is written. The exact before/after patch and source bytes are archived.

## Validation completed locally

- The C++ fixture extracts the **actual restored lambda and constructor expression**, rather than testing an unrelated replacement implementation.
- GCC and Clang C++20 `-Wall -Wextra -Werror -pedantic`: **556,283 checks PASS** each. Clang ASan/UBSan: same fixture PASS.
- Includes the earlier native-observed read-only comparison `test=0x20, op=0, compare_mask=0x20, write_mask=0`, clear/Always/Never/no-Replace cases, 500,000 deterministic random masked reference combinations, and all six saved-option/readback-mode predicate combinations.
- Eight Python source/runner fixtures PASS: exact two-file change, changed-source rejection before writes, repeat-apply refusal, mocked successful build/run/archive, mocked compile-failure source+ELF rollback, changed-ELF rejection, changed-source rejection, external-save-root rejection. **Mocked runs are not native emulator or GPU tests.**
- Initial local C++ fixture compile failed because its generated test scaffolding omitted `<initializer_list>`. The fixture generator was corrected; GCC, Clang and sanitizer runs then passed. No user-host build occurred during that failure.
- **No new full native emulator compile or GPU capture has been performed yet for this restored combination.** Prior native acceptance belongs to the existing PR69/68 algorithms, not automatically to this integrated candidate.

## Host runner

`run_restore_kept_renderer.py` imports the previously tested proxy-free/Docker-aware runtime helper from immutable commit `87d3eeacf828b0f990b6aeb11b3b5da46f1b0792` and verifies its SHA256 `df7617549006c8733049a684f22f6ac2bf6b96701bf2fe1da5ee9265df568250`. Download that helper as `resume_c925_revalidation.py` beside these two scripts.

It verifies native PR96 ELF `21a26822390451d4b21bde8feae4712b2bd66bf068096ad246055db914122fde`, all six retained shader/cache source hashes and both renderer preimages. It checkpoints the prior ELF, patches only the two renderer files, compiles the extracted fixture, and incrementally rebuilds the existing Ninja tree with live stage output and a 15-second heartbeat. On build/fixture failure it restores the two source files and PR96 ELF; it never launches after a failed build.

A successful build retains all six PR95/PR96 hashes and proceeds to one menu-only 118+2-second owned-process test using the existing isolated profile. Original saves/config, protected C925 ELF, GoW binaries, ES-DE, Sunshine/X11 and SSH are not rewritten. The 8 GiB VRAM requirement and conditional same-server Ollama unload/context+expiry restoration remain unchanged. Images have actual capture timestamps. All outcomes go to `~/projects/shadps4/logs/got-c925-accepted-renderer-*.tar.gz`; no raw saves/profile/ELF bodies enter the evidence archive.

Latest official main checked was `caaa5419726c912013ffd5154dd4a9bac461cd94`. This is explicitly **not an upstream rebase** or a production-main promotion. The common GitHub journal contains the reviewed negative PR96 result at `docs/emulator/reviews/20261028T135245496700Z-pr96-runtime.json`. Preserve every failed, rejected and unchanged visual outcome.
