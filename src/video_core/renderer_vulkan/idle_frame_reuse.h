// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include <queue>
#include <type_traits>
#include <utility>

namespace Vulkan {

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

[[nodiscard]] constexpr bool IsRealGuestFrameSubmission(
    bool has_game_content, bool is_reusing_frame, bool is_game_flip) noexcept {
    return has_game_content && !is_reusing_frame && is_game_flip;
}

} // namespace Vulkan
