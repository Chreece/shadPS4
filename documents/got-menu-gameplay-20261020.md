# Menu and gameplay capture, 081228 evidence

The uploaded capture ran source 28fe97972a35869e737ddb0b8dff666a625c8496 for the complete 300-second window. The AMD interpolation crash did not recur. No GPUVM fault or texture-pool exhaustion was reported. Production remained unchanged and Ollama was restored after the low-VRAM unload.

Grass fragment shader 836253ca now reports material index 2 for all three resources at its first captured binding, where the preceding working build reported 0. Across its nine feedback rows, 11,679,558 samples are finite and in range. The 130-second screenshot has green/grey foreground grass, a remaining pink patch at left and no sword. This establishes material-selector progress, not complete menu correctness.

The user manually entered gameplay. The 224-second screenshot shows largely absent world geometry. Six checkpoint frames yielded 3,466 completed draw-query rows and 316 pass images. Menu remains the first debugging priority.

Menu frame 2 still contains zero-work dispatches for 04950cf6, 55d17c16, 52b6e003 and 5c4c2b6f, and empty indirect draw paths. This does not establish that every empty command should be nonempty, nor map one to the sword or wind. Regular buffer-binding range correlation did not identify a confirmed writer for their current command addresses. No count override, clipping/depth change or speculative rendering correction is justified by that observation.

## Repeatable phase transition

The runner now attempts one N press after 150 seconds from launch. It verifies the X11 client PID belongs to the test process group and its executable is the isolated candidate, requires a unique matching client, confirms focus, and releases the key. Missing/ambiguous ownership fails without sending input. X11/XTest availability is checked before launch. The input log records elapsed time, PID/window and key-send status; key delivery is explicitly not proof that gameplay loaded. No retry or second press is issued. Existing checkpoint pairs at 60, 120 and 210 seconds provide menu/menu/expected-gameplay samples.

Python parsing, shell syntax and mocked process-ownership gate cases pass. Native X11 key delivery is not tested in this environment. This update changes capture orchestration and diagnostic readback only. Rendering behaviour remains the same as source 28fe97972a35869e737ddb0b8dff666a625c8496. It does not claim a new sword, wind or world-rendering fix.

## Missing depth-prepass evidence

The frame capture returned early whenever a pass had zero colour attachments, even when a depth target was attached and collected. Consequently the depth-only geometry prepass was absent from pass images. The sword is visible in the later normal attachment, but this does not establish fresh geometry or its depth. Capture now accepts depth-only targets under the existing byte/image budgets and logs depth-present and depth/stencil-clear flags. This is a readback correction, not a change to guest depth tests, clears or draw counts. The next capture can distinguish prepass depth contents from the later grass pass.

Full local emulator compilation/link passed after the depth-readback change. The extracted runner transition block was exercised at 149, 150, 151 and 299 seconds for both successful and failing input: exactly one attempt occurred at the threshold, with no retry.
