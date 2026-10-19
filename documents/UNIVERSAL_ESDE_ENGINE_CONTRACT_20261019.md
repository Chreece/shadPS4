# Universal ES-DE shadPS4 — source integration and release gates

**Scope:** one general Linux x86-64 shadPS4 executable for ES-DE, built from
upstream shadPS4 and verified engine-wide corrections from the two development
streams. No `CUSA` conditionals, shader-hash overrides, hardcoded game
addresses, per-title executables or forced workaround settings may enter
production emulation code.

## Source contract

- **Upstream:** `f68923fe807f657348e6719ce658bcbd0a2b1c72` (official main,
  October 19, 2026).
- **Previously compiling universal core:** binary SHA-256
  `48ae4ac2ec8f42e2155425521c8699359b466dd6459905948762de539865b5bc`.
  Its ES-DE Home/PS confirmation and startup loading GUI remain; do not restore
  older frontend files that lose these features.
- **GoW provenance:** `f909c3abb1ac1e7c8d87de833d3659f7d0b8fc87`,
  plus the SHA-verified full-geometry GPU/UFFD source reconstruction from
  `tools/development/gow_restore_graphics_renderer_20261017.py` and
  `gow_preserve_uffd_support_20261017.py`.
  The 23:14 previously rendered binary is
  `8b0cbcba030859291e8573bc2003878184ce820908c63334d72e811e552740ef`.
- **Ghost pre-feedback engine:** `e9f470b359845a456139a865aa1d7b3001f0dbfd`,
  including shader-resource IR, true guest T#/S# handling, nonuniform
  Vulkan image selection, SRT parsing and PM4/texture semantics.
  The saved Oct17 Ghost test archive was **missing** in the latest uploaded
  triage report, so its binary SHA is not newly verified. Its former
  horse/rider partial-world observation is historical evidence, not a
  current proof of a corrected game.
- **New generic correctness commits:**
  - `27bc4e1ef8d885376fa177999ebb176619c144e4`: GPU watcher-word initialization.
  - `645d6cf9586eb577ab92a1869c925e7cc6305617`,
    `69c3c02d2aa5d31e3d2d4ee92c8a26bbeaa6263c`,
    `c036336bf4b92ff3ac11e1422ba73341c5bb2a9a`: Vulkan
    indirect-command reads and write-after-read hazard source stages.
  - `81e560f89212b3fd8b2cc6c19257294ef0e70460`: synchronized
    Audio3D PortAdvance queue consumption, not a claimed stutter cure.
  - `baceadf44d0a040384a6d0db3b8e89abef4a824a`: LDS workgroup
    singleton/Phi correctness.
  - `c8121ad971408e5fb28b2da28de09625fa53efe0`: dynamic
    texture-table layout witnesses and shader cache invalidation.
  - `b18cfc312904d8eaf01ed7788b85a97bcd114121`: finite but
    adequate independent image-view allocation capacity.
  - `aa775cd9965118910d44e675708e7fc0cc2a62a1` and
    `1b1ca11bfea7025c97bcde4810f892eee81c7227`: generic
    bounded pointer-selected texture/image/sampler lowering and
    uniform-sampler pairing. Their shader replay/SPIR-V tests passed,
    but gameplay effects remain unverified.

No commit is automatically assumed compatible with the other source
stream. A source diff present already is accepted only if the complete
reverse patch applies exactly. Overlapping code or a missing dependency
stops the build with a recorded conflict.

## Five mandatory runtime-backed GPU/UFFD sources

| File | SHA-256 from rendered GoW source |
| --- | --- |
| `src/video_core/renderer_vulkan/vk_rasterizer.cpp` | `bde3553d3c495fff2f34f177982a8f19e7a71542981d5c5194e8032e78328d0a` |
| `src/video_core/page_manager.cpp` | `8ddd21535bd66135f98fe8a855528e2e409048873a0fdcc41bcc5312f929ce21` |
| `src/core/libraries/gnmdriver/gnmdriver.cpp` | `12ae19b6effe8e020e9c5a1f47b886dbc790818d1610566be6c28facd572f62e` |
| `src/video_core/page_manager.h` | `f5a661ef28f7b00b5c513da33897a31487990a55f2646f2bb4cab7883c4c7511` |
| `src/video_core/renderer_vulkan/vk_rasterizer.h` | `889759b700915f2076ee95ce4af50c5fc8c0c90fee5901c03ec73bff1b165992` |

Reconstruct these exact historical post-images, then apply their **delta**
onto the contemporary upstream+Ghost combined tree via checked three-way
merges. Never wholesale replace a modern shader resource pipeline with an
old renderer; conflicts must be reviewed. For page-manager proof the builder
searches preserved source files, the reachable Git history and bounded
historical build archives by SHA. If the file cannot be proved, no
production-complete claim can be made.

## Test and promotion gate

1. Synthetic Git source replay / merge-conflict tests; CI workflow.
2. Merge latest official upstream and pre-feedback Ghost shader engine;
   record all paths of semantic conflicts before building. No
   `git merge -X ours` or `-X theirs`.
3. Port only the generic correctness commits listed above, with exact Git
   source and fail-closed cherry-pick checks.
4. Recover all five proven GPU/UFFD deltas, retaining current shaders/ABI.
5. Run native PM4 400k, guest T#/indexed source and LDS/Phi regressions,
   build the full Release binary under pinned Debian 13/Clang 19, validate
   ELF/dynamic dependencies and the public CLI.
6. Use **the same binary** to run representative GoW and Ghost scenes.
   GoW must no longer crash during module/asset initialization and must
   render world geometry. Ghost must progress past logos to rendered
   geometry; black-screen music, white menu or partial horse-only graphics
   are not successful release acceptance. Verify gamepad quit and return
   to ES-DE. Other games remain a broader compatibility goal, not proven
   from two titles alone.
7. Promote only after user-observed gameplay results. The script never
   automatically changes `~/Applications/shadps4/shadps4`,
   `~/.local/bin/shadps4-esde`, ES-DE config, games, saves,
   Sunshine, Ollama or SSH.

## Excluded from release logic

Experimental shader-hash feedback, game-specific CUSA selectors, fixed
frame-buffer addresses, guessed descriptor contents from missing capture
bytes, forced GPU readbacks, depth/raster override switches, manual
automatic game navigation and unverified RADV/driver settings are
diagnostic-only. The Oct19 recovered 82-shader transcript uses four
external fixture inputs and has **not** established correct live GPU
frame output. Carry that evidence as follow-up research, not as a
verified compatibility workaround.

## Integration executable

`tools/development/universal_esde_integrator_20261019.py` stages
everything inside an isolated `~/projects/shadps4/ESDE/universal-*`
directory and writes its full report/build log to
`~/projects/shadps4/logs/universal-*`. Run
`--self-test` before `--build`. A failed merge/preflight emits one
archive describing the complete conflict set and retains the source
checkpoint for a reviewed follow-up; no installed executable changes.
