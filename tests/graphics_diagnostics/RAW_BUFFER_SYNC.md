<!--
SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Raw buffer image synchronization candidate — 2026-10-01

The user reports that missing geometry persists when revisiting the same area in
one `f96686b8` session. First-use shader compilation therefore does not explain
the entire problem. The captured shader compilation and sparse residency work
do not identify which resource is rendered incorrectly.

This branch adapts the read synchronization change from upstream
[PR #5120](https://github.com/shadps4-emu/shadPS4/pull/5120), reviewed at
`9c8d0fbb56082bcaaa0be7ab3bc1c1d09c066bc3`. Shader buffer reads now request image
synchronization for both raw and formatted descriptors. The existing integration
already invalidates aliased images after raw buffer writes. A duplicate image
copy in `ObtainBuffer` is removed because `SynchronizeMemory` performs that copy.

With graphics diagnostics enabled, `raw-buffer-image-sync` records a raw shader
buffer read for which image or metadata synchronization actually ran, including
the shader hash and range. `buffer-image-sync` records the copied image's size
and dimensions. Both use the existing bounded sampling. These events establish
whether the path is exercised, not whether it caused the visible defect.

The existing exact-base image lookup and small read-only buffer optimization are
retained. This is not a complete alias coherence implementation, and upstream's
Control test report does not establish a Red Dead Redemption fix.

Validation: GCC 13.3 C++23 syntax checks for the changed buffer cache and rasterizer;
graphics diagnostic checks with tracing enabled and disabled. A full Vulkan
emulator build and game test still require the user's local Docker test. No audio,
sparse queue selection, depth/presentation behavior or host settings are changed.
