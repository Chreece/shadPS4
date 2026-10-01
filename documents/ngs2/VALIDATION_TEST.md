# RDR rendering validation session

The user's 2026-10-01 phone video (23.39 seconds) shows intermittent loss of large
building surfaces and parts of the character, particularly around 14–17 seconds,
while the HUD remains visible. The user already reproduced the defects after
freeing 16 GB of VRAM and restarting the game. VRAM pressure alone is therefore
unlikely to explain the remaining issue. The video does not establish which
renderer path fails, nor does it confirm that the preceding Precise readbacks
comparison actually used mode 2; that requires a current runtime log.

This session uses the existing main `10ff9e19` emulator. It enables only the RDR
per-game Vulkan core and synchronization validation flags and disables GPU-assisted
shader instrumentation for this short run. It preserves the current readbacks
mode, audio and every other setting. No emulator code or launcher is changed.
Validation errors can identify invalid resource/attachment use or synchronization
hazards; a clean log cannot establish correct PS4 rendering semantics.

Exit the emulator, then run `readbacks_test.py --validation --collector
/path/to/collect_graphics.py`. The collector must match the known SHA-256 in the
helper. Layer enumeration checks for `VK_LAYER_KHRONOS_validation` before changing
anything. If missing on Debian, install `vulkan-validationlayers` and retry.

Start the same NGS2 trace entry. Reproduce for about 20–30 seconds in the affected
scene, then exit normally. Validation can substantially slow the game. Run the
printed `FINISH` command: it collects the renderer logs/configuration and then
restores the exact pre-session per-game file, including its original absence.
Upload the `GRAPHICS_REPORT` archive. `RESTORE` provides a separate rollback if
the game cannot start. Restore refuses to overwrite later configuration edits.
As with the readback test, the per-game override affects any CUSA36843 launch that
shares this user directory while the session is enabled.

The capture must confirm the exact executable, `Enabled instance layers` including
the validation layer, and the actual readbacks mode. A configured flag alone is
not proof that validation loaded. Match runtime errors to the active game session;
the collector can also include older logs and marks their paths and timestamps.

No upstream renderer PR was imported for this test. PR 4751's small vertex-buffer
streaming condition and zero-address filtering are already present in this core;
PR 5140's flip notification ordering addresses a distinct guest timing issue and
has not been established as the cause of these dropouts.

Host checks: `python3 -m unittest test_readbacks_test -v`. Tests cover preservation
and rollback, missing-layer refusal, collector integrity, and rollback even when
collection fails. These checks do not run the game or prove a graphics fix.
