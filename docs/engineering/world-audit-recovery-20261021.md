# World-graphics audit recovery, 2026-10-21

This package starts from `94ed29b7c5767718ead87164558d6ec7523bcc9a`, before the resource-range trial. It is NOT a verified world-rendering fix and is NOT a clean upstream-main build. The existing host workflow overlays selected files onto its retained experimental worktree.

## Decision

Withdraw the resource-range trial, including its address optimization change, from the recovery run. Do not retain a change merely because its numerical helper tests pass or a warning disappears. A locally proved explicit-buffer read range does not establish global residency requirements for other shaders using physical addresses. The trial shortened the range supplied to ObtainBuffer, changing residency and upload behavior as well as descriptor coverage. The reported visual worsening still needs controlled attribution; this recovery does not claim that the entire original missing-world problem was caused by that trial.

## Capture and provenance corrections

The previous automatic indirect snapshot was taken 60 seconds after rendering started. It did not contain intermediate render-target pixels. The new launcher waits for the user to reach the broken scene and press Enter in the SSH terminal. It sends no game input.

Only optional diagnostic headers are transformed, after checking their complete Git blob identities. The first armed frame gathers the existing draw census and image plan; the second captures sampled rows from supported color/depth render-pass attachments. The image-data budget is 16 MiB. This is NOT a capture of every texture, compute output, or every pixel. Copies and synchronization can perturb the selected frames. A screenshot is requested before arming, followed by about 30 seconds without further image captures. An unarmed run times out after ten minutes and is marked incomplete.

The automatic 60-second snapshot, compute-buffer captures, shader dumps and vertex-output instrumentation are not enabled by this launcher. Detailed frame/resource messages are retained separately from the bounded general log, with an 8 MiB limit. The existing profile isolation, owned-process cleanup, 8 GiB VRAM recovery threshold, 2 GiB abort guard, and conditional Ollama restoration are reused from the pinned runner.

The host workflow hashes the actual staged src tree, root CMakeLists.txt and .gitmodules, then verifies the same file set and contents after compilation. It records the worktree HEAD separately from the package revision. It does not claim to inventory all third-party libraries or the entire compiler environment. The actual-source manifest and diagnostic-header manifest accompany the runtime evidence. No original source contents, save files or third-party binaries are published by this package.

## Validation performed

Eight Python/Bash fixture tests pass. They exercise exact-hash and symlink rejection, no partial header writes, bounded capture configuration, actual-source mutation detection, shell syntax/build ordering, runtime environment preparation, output collection and archive-metadata rejection. These tests use explicit fixtures rather than claiming a full host build.

The exact generated C++ manual-gate function compiles with GCC and Clang. Under each compiler it passes default-unarmed-configuration behavior, directory rejection, manual file creation after polling delay, and the latched-ready behavior after removal. The complete emulator and the Vulkan image-copy path have NOT been executed in the assistant container for this package. The server must complete and verify the host build before the isolated runtime is launched.

## Use

Fetch this package without recursive submodule fetching, export its exact scripts/got-world-audit-recovery-20261021.py, and run:

```bash
python3 got-world-audit-recovery-20261021.py build-run <exact-package-commit>
```

When the broken scene is visible, hold the camera still and press Enter in the same SSH terminal. Wait for cleanup and Ollama restoration, then return the UPLOAD_THIS archive. Both the capture completeness status and the resulting images must be reviewed; a successful build or runner exit is not evidence that world graphics were restored.
