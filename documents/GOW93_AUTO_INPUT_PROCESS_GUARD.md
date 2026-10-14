# GoW93: notice visible, automatic Cross blocked before input

Evidence: `gow93-original-profile-20261014-125945-2700865.tar.gz` (SHA-256 `5586d70fdfd5363039d6b419851c0464aa20573ad5e05a5582d2902d19575843`).

The matcher recognized the Dark Odyssey notice at 32.10 and 45.09 seconds. At 45.09 seconds, `game_window` raised `Input target is not the verified emulator process`. ZERO press attempts were made. The final screenshot at 295.19 seconds still shows the notice. The emulator stayed running until the runner stopped it at 300.79 seconds; no GPU hang or assertion was reported.

All nine before/after checkpoint checks passed. DMA and SRT ordering=2 were confirmed, settings and old dumps restored, and the global config remained unchanged. Independently decoded GPU PNGs: ten black and six containing the notice (three guest, three display), each nonblack image having 1,495,368 nonblack pixels. All 24 window screenshots match their recorded SHA-256. No gameplay beyond the notice is demonstrated.

## Correction

The old guard compared `/proc/<pid>/exe` directly to the application binary. The CPU-ID launcher execs the DynamoRIO runtime; that runtime can be the kernel's executable identity instead of the application. The archive did not record the resolved executable filename, so its exact value is not claimed as measured evidence.

`tools/gow93-auto-process-guard.py` (commit `1d1667a699480274a2eaa605d379ed8bc3801e4d`) registers the actual Popen child, checks its start time, owner, parent, process group and session, and requires either the native application inode or the recorded runtime inode plus a readable offset-zero mapping of the exact pinned application device/inode. It records the executable and relevant application-mapping proof. It does not rely solely on process/window names.

The existing visual matcher, Cross mapping, two-attempt limit, stop on changed/unknown screen, focus checks, key release, five-minute capture, SRT flag placement and settings recovery are unchanged. No emulator compilation/linking, shader change, CPU-mode change or historical-log dependency.

## Validation limits

19 new tests and 16 existing tests passed. A real native Linux exec/mmap fixture reproduces the old rejection and exercises the corrected process validation. Three full launcher simulations use real child processes and the real restoration chain but mock GPU/window/input operations. Current captures were replayed through the unchanged matcher. This is NOT a test using the server's actual DynamoRIO or the game's response to injected Cross.

Script SHA-256: `4f7a209be51b62f3d336e7b8fdddcc1b5b230320cc381a1eb0343c2897b04007`.
Verified Git blob: `bd9f6a48a77b1e6db4e513d0e019bb23a36c1b66`.

Next run: same `GOW93_REPORT` artifact, now including `automatic-process-identity.json` and before/after confirmation screenshots. Preserve the known rendering baseline; do not rebuild because the automation's process guard failed.
