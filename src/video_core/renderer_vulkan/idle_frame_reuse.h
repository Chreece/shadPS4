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

// Re-presenting a previous frame may race with GPU-thread allocation from
// the SAME free-frame FIFO. Reuse it only when it is still at the front,
// then take exclusive ownership by removing it from the FIFO. A mismatch
// means the frame is either in use or behind a different (older) free frame.
// Both cases must fail closed, never submit a frame owned by guest graphics.
template <typename FramePtr, typename IsReady>
    requires std::is_pointer_v<FramePtr>
[[nodiscard]] FramePtr TryPopMatchingReadyIdleFrame(std::queue<FramePtr>& free_frames,
                                                    FramePtr expected_frame,
                                                    IsReady&& fence_ready) {
    if (expected_frame == nullptr || free_frames.empty() ||
        free_frames.front() != expected_frame) {
        return nullptr;
    }
    return TryPopReadyIdleFrame(free_frames, std::forward<IsReady>(fence_ready));
}

// A previously presented guest frame can be redrawn by the host while
// displaying the loading overlay. That is NOT a new game flip and must not
// advance first-game-frame or visible-frame statistics.
[[nodiscard]] constexpr bool IsRealGuestFrameSubmission(
    bool has_game_content, bool is_reusing_frame, bool is_game_flip) noexcept {
    return has_game_content && !is_reusing_frame && is_game_flip;
}

static_assert(IsRealGuestFrameSubmission(true, false, true));
static_assert(!IsRealGuestFrameSubmission(true, true, true));
static_assert(!IsRealGuestFrameSubmission(true, false, false));
static_assert(!IsRealGuestFrameSubmission(false, false, true));

} // namespace Vulkan
