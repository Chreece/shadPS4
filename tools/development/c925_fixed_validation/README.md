# Recheck the native C925 capture with upstream-fixed synchronization validation

This is a **diagnostic tool correction**, not an emulator rendering patch, freeze investigation, or completed missing-material fix. The user explicitly requires focusing on menu fills and overlays; equal menu images are not a failure criterion.

## Evidence and uncertainty

The native PR109 capture `got-c925-sparse-host-limit-20261029T075648223982Z.tar.gz` compiled/linked cleanly and exercised legal sparse buffers. It stopped at 10.204 seconds on `SYNC-HAZARD-WRITE-AFTER-PRESENT`, specifically present-to-present reuse of swapchain image 3, under validation layer 1.4.309. No menu screenshots were reached. The retained native ELF is `89ffa07ab49d0df36b3a4c4295e0cff8f42bb7e167cbced3d5879b1e5c2f5aec`.

Previously captured presenter/scheduler code already waits on image acquisition and the source-frame timeline, signals the acquired image's present-ready binary semaphore, and presents waiting on it. This static source audit is not proof of every live synchronization edge. The runner rechecks the exact source hashes before relying on it.

Khronos has independently confirmed a present-to-present false positive with timeline semaphore chains: [issue 13117](https://github.com/KhronosGroup/Vulkan-ValidationLayers/issues/13117), fixed by [PR13248](https://github.com/KhronosGroup/Vulkan-ValidationLayers/pull/13248), merge `da7669ad94110c7e7e89ab36defabff4baa507c0`. The change propagates `last_synchronized_present` when resolving semaphore dependencies. The upstream reproducer uses multiple swapchains and is not this game. **The user's report is not yet proven false.** A newer checker is warranted before inventing a renderer barrier, adding empty submits, disabling validation or ignoring the message. The latest published SDK 1.4.363.0 predates the fix; version text alone is insufficient.

## What the runner does

- Verifies the current native ELF, all retained shader/renderer postimages and the exact audited presentation source. Changed source is captured and refused, not overwritten.
- Builds **only the validation layer and its official dependencies** at the exact Khronos fix commit, privately under `~/projects/shadps4/scripts/validation-tools/`. Uses the pinned `known_good.json`, records resolved dependency revisions and library hashes, and the actual pinned CMake target `vvl` with three parallel workers. Full logs and 15-second quiet-command heartbeats remain visible.
- Does not run sudo, install system packages, replace the Vulkan loader/driver, rebuild shadPS4 or edit its sources. Existing Xlib development tools are required; absence stops instead of installing them. The tool build requires 6 GiB free disk space.
- Creates a private absolute-path layer manifest. An instance-only probe and the actual game loader log must both identify the built library. Runtime core and synchronization checks must remain enabled; GPU-assisted validation stays off.
- Copies the previous isolated profile/saves again, retaining game settings. Uses the same verified emulator and no input. First actual validation error still stops only the owned process; otherwise the same 118-second TERM plus two-second KILL cap and timestamped menu captures apply.
- Keeps 8 GiB free VRAM and the existing conditional same-server Ollama context/expiry restoration. Protects original saves/configuration, all accepted source/binary hashes, SSH, Sunshine/X11, services and launcher.
- Archives every outcome, including tool-build or layer-load failure, to `~/projects/shadps4/logs/got-c925-corrected-layer-*.tar.gz`. No game runs after a failed preflight/tool build.

The recorded current candidate is never overwritten. This is neither a latest-upstream emulator rebase nor main production-C++ integration.

## Local validation actually performed

`python3 -W error::ResourceWarning -m unittest -v test_recheck_present_validation`: **24/24 PASS**, final rerun 5.064 seconds.

Tests cover private environment isolation without ICD changes, restoration on exception, correct/wrong layer manifests, loader-log JSON trailers, old/missing version rejection, source/symlink guards, actual subprocess success/failure/timeout cleanup, actual temporary Git/hash and source-origin checks, cached library tamper rejection, and mocked host orchestration. The mocked host cases cover unchanged binary/source/save preservation, rejected changed ELF, tool build failure, failed layer gate, model restoration after memory failure, unchanged first-error stopping, actual-layer verification failure and final VRAM drop.

An initial local timeout-test used a 0.3-second Python startup budget and then tried to parse an empty PID line. The test scaffold was changed to a shell-owned sleep with a one-second timeout; it verifies actual child termination and parent survival. This was not a host build or GPU failure. Source review also corrected the initial tool target name from the output-library name to the actual pinned `vvl` target before publication. Final tests reran successfully. Published runner and test blobs match the local tested bytes.

**The real pinned validation-layer build, real layer loading, and GoT execution have not run in the model environment.** Local tests are not proof that the user's message is a false positive or that menu materials are recovered. The next actual capture decides that.

The canonical history is `docs/emulator` on fork main; the reviewed PR109 result is `reviews/20261029T075648223982Z-sparse-host-limit-native.json`. Preserve both positive size-correction evidence and this independent validation uncertainty.
