# Mixed-component interpolation and remaining menu evidence

Input: got-frame-history-evidence-20261020-073600.tar.gz. Tested source: 8099336f8e704dee606db3b71e4da82cc06513c4.

The five-minute menu capture completed with 39 indexed feedback rows, 39 table dumps, 138 indirect compute argument readbacks, 1,080 draw queries and 331 pass images. No GPU faults were reported. Gameplay remains untested.

## Rendering defect

Shader 836253ca reads PARAM3.y using V_INTERP_MOV P0 and PARAM3.x using smooth sample interpolation. The recompiler stored one interpolation mode per four-component parameter. The P2 instruction for x replaced the MOV mode required by y. The captured SPIR-V consequently declared the entire PARAM3 vector as Sample, with DenormFlushToZero enabled.

The preceding vertex shader 7472760d exports the material selector as raw integer bits in PARAM3.y. The captured vertex input includes half-float material value 2, which the shader converts to integer 2 before exporting its bits. These bits are a subnormal when interpreted as a float. The native fragment feedback reported selector zero for every recorded sample of its three material resources, even though all 49 entries in each table were valid. This is a concrete interpolation mismatch; whether fixing it restores the observed grass colour must be checked on the native GPU.

The generic fix records interpolation per component. Mixed-mode fragment inputs use separate scalar variables with Location and Component decorations. Homogeneous inputs keep vector declarations. AMD explicit interpolation, KHR per-vertex arrays and the existing flat fallback retain their separate access paths. Flat/per-vertex components do not inherit Sample or Centroid. The shader cache version increases to 56.

## Validation

Full local emulator compilation and linking passed. The captured fragment binary was replayed through the production translator and SPIR-V backend with reconstructed scalar memory and fragment input/export configuration. Assertions verified PARAM3.x remains smooth and PARAM3.y retains MOV semantics for AMD, KHR and fallback profiles. All three resulting modules passed spirv-val for Vulkan 1.3. A fourth replay with manual KHR interpolation also passed. An instruction-decoder scan of all 52 captured fragment binaries found this MOV/P2 mixed-parameter pattern only in 836253ca; it is not presented as an explanation for every missing element. This replay verifies compilation, not native image sampling or visual correctness.

OpenOrbis hardware probe source is in documents/homebrew/component-interpolation. Its Bonaire vertex and fragment shaders assemble successfully. The application has not been linked with the OpenOrbis SDK or run on PS4; hardware validation remains outstanding.

## Missing elements remain unresolved

The four indirect compute commands for 04950cf6, 55d17c16, 52b6e003 and 5c4c2b6f contain group dimensions (0, 1, 1) at captured checkpoints. These dispatches execute no work. Previously recorded downstream draws also have zero instance counts. This starts before rasterization and is independent evidence from the material interpolation defect. It does not establish that every empty command should produce visible geometry or identify which command belongs to the missing sword.

The sword-bearing normal attachment and its later fullscreen reconstruction still do not prove a fresh sword draw reaches the main colour/depth attachments. No forced counts, clipping/depth changes, texture overrides or title-specific rendering conditions are introduced. The native follow-up must check material selector feedback, grass colour and missing elements separately; a successful build alone does not establish that the menu or gameplay is fixed.
