// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <queue>
#include <type_traits>
#include <utility>

namespace Vulkan {

// Only host idle redraws use non-blocking acquisition; guest flips retain the
// existing synchronized frame acquisition. Caller holds the free-frame mutex
// while checking the real Vulkan present-done fence. If GPU work remains in
// flight, the frame stays at the head of the free FIFO and is never recycled.
template <typename FramePtr, typename IsReady>
    requires std::is_pointer_v<FramePtr>
[[nodiscard]] FramePtr TryPopReadyIdleFrame(std::queue<FramePtr>& free_frames,
                                            IsReady&& fence_ready) {
    if (free_frames.empty()) {
        return nullptr;
    }
    FramePtr frame = free_frames.front();
    if (!std::forward<IsReady>(fence_ready)(frame)) {
        return nullptr;
    }
    free_frames.pop();
    return frame;
}

} // namespace Vulkan
