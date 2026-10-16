// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once
#include <algorithm>
#include <chrono>

namespace Libraries::Kernel {

// Compute an actual wake-up deadline from the expiration times of all pending
// high-resolution equeue timers. A newly inserted/modified timer signals the
// queue's condition variable; the waiter then recomputes this deadline.
// This helper is independent of game titles, scheduler policies and the host
// CPU platform. It never invents or rounds a guest timer expiration.
template <typename TimerMap>
[[nodiscard]] std::chrono::steady_clock::time_point NextHighResolutionWaitDeadline(
    const TimerMap& timers, std::chrono::steady_clock::time_point timeout) noexcept {
    auto earliest = timeout;
    for (const auto& [id, timer] : timers) {
        earliest = std::min(earliest, timer.added + timer.interval);
    }
    return earliest;
}

} // namespace Libraries::Kernel
