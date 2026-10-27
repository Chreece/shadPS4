// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <cstdint>

namespace VideoCore {

// A nonempty formatted (texel) buffer may alias a GPU-written image or its
// metadata even when the CPU-side buffer dirty tracker reports it as clean.
// Route it through the arena/image synchronization path instead of copying
// potentially stale CPU bytes to the host stream buffer.
// Empty requests retain their previous zero-length behavior.
[[nodiscard]] constexpr bool MayBypassImageSyncForStream(bool formatted,
                                                         std::uint32_t bytes) noexcept {
    return !formatted || bytes == 0;
}

static_assert(MayBypassImageSyncForStream(false, 20288));
static_assert(!MayBypassImageSyncForStream(true, 20288));
static_assert(MayBypassImageSyncForStream(true, 0));

} // namespace VideoCore
