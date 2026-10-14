# GoW93: New Game reaches animated scene; 3D rendering remains incorrect

Evidence: `gow93-original-profile-20261014-151144-3030897.tar(1).gz`.
Archive SHA-256: `379e9bb45fb187e4bed4b8b2ee02a291c54dfd10b6ca950be29089517b39be39`.
This is God of War Ragnarök CUSA34384, not Ghost of Tsushima.

## Verified result
The automated sequence got beyond the main menu into an animated scene. At 55.89 seconds,
the main menu is visible with New Game highlighted. At 70.96 seconds, the menu is gone and
an opening scene is visible. Subsequent screenshots show changing character poses and camera
views. Two characters appear by 191.02 seconds, readable dialogue subtitles at 220.95 and
265.95 seconds, and another changed scene at 295.82 seconds.

This is beyond publisher logos and beyond menu-only progress. It is NOT proof of playable
movement/combat or correct rendering. Characters are predominantly black silhouettes;
backgrounds have severe repeated-edge/smearing/block-like artifacts. Some character/weapon
detail is visible in the final frame, but the scene remains badly rendered. No audio recording
was provided, so subtitles do not prove correct audio playback. Frame rate and correct game
speed were not measured.

## Input and runtime
- 24 scheduled screenshots, each independently SHA-256 checked against the report.
- 48 mapped Cross key-down/up pairs reported completed: one before and one after each snapshot.
- Input token: `n`, read from the active binding map. No input maps were edited.
- No rejected/failed input attempts; all 24 before/snapshot/after event sequences verified.
- Input record exactly matches the copy inside result.json. This confirms input-tool completion,
  not a separate guest acknowledgement for every press.
- Runtime: 300.77 seconds. Natural return code: null; final return code: 0 after runner termination.
  The process was still running at the time limit. This is not natural game completion.
- No GPU hang, device-lost, segmentation-fault or renderer-assertion strings in the retained console.
- No RADV dump in this report. Absence during this session is not a general stability guarantee.
- All nine host-side source/executable pin checks passed before and after the run.
- Per-game override and prior shader dumps restored; global configuration hash unchanged.
  Normal game saves/autosaves were not audited or rolled back by these checks.

## Exact tested configuration
Executable:
`~/projects/shadps4/GodOfWarRagnarok/tests/gow91-build-20261014-112830-2480722/shadps4`

Executable SHA-256:
`b7db5807b4b04920b0e8f72d94a19fa74a29c70787c60264d8dfd06f1104ea79`

No compilation or linking took place in this run. The manifest describes the existing 15-object
overlay. CPU identity mode remained auto with the verified DynamoRIO launch. DMA was logged
as enabled and sparse queue family 4 was selected. The SRT option
`SHADPS4_GOW_SRT_AUTO_ROOTS=2` was applied after launcher environment cleanup and confirmed
in the log. For fragment shader 0x7f710602, the root order is 2 -> 8 -> 6. Across this capture,
1180 planner records and 389 reordered plans were retained; none reported ambiguous, cyclic
or capped dependency planning.

Preserve the Phase80/84 fixes, per-permutation metadata ownership, DMA-residency routing,
image/buffer range handling, and the tested SRT dependency ordering. For a normal ES-DE
build, automated Cross input and framebuffer/screenshot probes are diagnostic behavior,
not compatibility fixes. This archive does not validate a newly rebased/main-based build.

## GPU images and shader evidence
All 16 GPU PNGs were decoded independently and their pixel counts, RGB sums and maxima
matched their sidecars. There are eight guest and eight display readbacks. Their final pair
contains 1,899,897 nonblack pixels each at 1920x1080, showing the menu-era output. The probe
is capped at eight early samples per stream: the later cinematic is documented by window
screenshots, not late-scene GPU framebuffer samples.

All 2959 retained shader files matched their recorded size and SHA-256. These comprise
1189 SPIR-V binaries, 1189 original binary dumps and 581 text files. SPIR-V stage counts:
551 vertex, 485 fragment, 144 compute, 5 local and 4 hull. All SPIR-V binary headers and
four-byte lengths checked. This is not a full SPIR-V semantic validation.

The 80 MB collection limit omitted 4178 text files, but zero SPIR-V or original shader
binary dumps. Do not report that every generated diagnostic file was retained.

## Remaining evidence and diagnosis limits
- 342 distinct soft DMA missing-page addresses; zero repeats in retained log messages.
  Duplicate filtering means this is not a count of every GPU fault/access.
- 315 partial image-range synchronization messages, without the old alias assertion.
- Small-formatted-read observation counter reached 4,194,304; image/metadata-sync counter
  remained zero in those bounded observations. This run still does not identify that shortcut
  as the remaining black-shading cause.
- 45 unflattened descriptor-source warnings.
- Unsupported SRT offset messages: 13,055 Phi; 5,742 ReadFirstLane; 4 SelectU32;
  3 GetAttributeU32. Descriptor-size clamps are also frequent.

These warnings remain investigation leads, not proof that a particular instruction or buffer
causes the silhouettes or repeated-edge artifacts. There is no new shader/runtime patch in
this handoff. Further rendering work should begin from this exact successful input/scene
baseline, not restart the build, input-guard or menu-recognition investigations.

This handoff is documentation only. It does not run the server, modify emulator sources,
change the user's settings, or schedule another test. Old log directories remain disposable.
