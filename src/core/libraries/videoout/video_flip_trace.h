// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <cstdint>

namespace Libraries::VideoOut {

// Observe the first VideoOut boundary crossing and only powers of two after
// that. Diagnostics never replace an event, schedule an extra flip, select
// a shader/game, or change the guest's GPU timeline.
[[nodiscard]] constexpr bool ShouldTraceVideoFlip(std::uint64_t count) noexcept {
    return count > 0 && (count & (count - 1)) == 0;
}

static_assert(!ShouldTraceVideoFlip(0));
static_assert(ShouldTraceVideoFlip(1));
static_assert(ShouldTraceVideoFlip(2));
static_assert(!ShouldTraceVideoFlip(3));
static_assert(ShouldTraceVideoFlip(4));
static_assert(!ShouldTraceVideoFlip(5));
static_assert(ShouldTraceVideoFlip(1024));

} // namespace Libraries::VideoOut
