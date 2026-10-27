// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <queue>

#include <gtest/gtest.h>

#include "video_core/renderer_vulkan/idle_frame_reuse.h"

namespace {

using Vulkan::TryPopReadyIdleFrame;
using Vulkan::TryPopMatchingReadyIdleFrame;
using Vulkan::IsRealGuestFrameSubmission;

TEST(HostIdleFrameReuse, EmptyPoolNeverQueriesGpuOrBlocks) {
    std::queue<int*> queue{};
    int calls{};
    const auto result = TryPopReadyIdleFrame(queue, [&](int*) {
        ++calls;
        return true;
    });
    EXPECT_EQ(result, nullptr);
    EXPECT_EQ(calls, 0);
}

TEST(HostIdleFrameReuse, PendingGpuFenceDoesNotLoseFifoFront) {
    int first{}, second{};
    std::queue<int*> queue{};
    queue.push(&first);
    queue.push(&second);
    int calls{};
    EXPECT_EQ(TryPopReadyIdleFrame(queue, [&](int*) {
                  ++calls;
                  return false;
              }),
              nullptr);
    EXPECT_EQ(calls, 1);
    ASSERT_EQ(queue.size(), 2u);
    EXPECT_EQ(queue.front(), &first);
}

TEST(HostIdleFrameReuse, OnlyTheOldestReadyFrameMayBeAcquired) {
    int first{}, second{};
    std::queue<int*> queue{};
    queue.push(&first);
    queue.push(&second);
    EXPECT_EQ(TryPopReadyIdleFrame(queue, [&](int* frame) { return frame == &first; }),
              &first);
    ASSERT_EQ(queue.size(), 1u);
    EXPECT_EQ(queue.front(), &second);
}

TEST(HostIdleFrameReuse, SpecificLastFrameBehindAnotherFrameFailsClosed) {
    int first{}, previous{};
    std::queue<int*> queue{};
    queue.push(&first);
    queue.push(&previous);
    int queried{};
    EXPECT_EQ(TryPopMatchingReadyIdleFrame(queue, &previous, [&](int*) {
                  ++queried;
                  return true;
              }),
              nullptr);
    EXPECT_EQ(queried, 0);
    ASSERT_EQ(queue.size(), 2u);
    EXPECT_EQ(queue.front(), &first);
}

TEST(HostIdleFrameReuse, AlreadyOwnedGuestFrameCannotBeReused) {
    int guest_owned{}, available{};
    std::queue<int*> queue{};
    queue.push(&available);
    EXPECT_EQ(TryPopMatchingReadyIdleFrame(queue, &guest_owned,
                                           [](int*) { return true; }),
              nullptr);
    ASSERT_EQ(queue.size(), 1u);
    EXPECT_EQ(queue.front(), &available);
}

TEST(HostIdleFrameReuse, ReadyLastFrameGetsExclusiveOwnership) {
    int previous{};
    std::queue<int*> queue{};
    queue.push(&previous);
    EXPECT_EQ(TryPopMatchingReadyIdleFrame(queue, &previous,
                                           [](int*) { return true; }),
              &previous);
    EXPECT_TRUE(queue.empty());
}

TEST(HostIdleFrameReuse, HostMustRequeueCompletedFrameAfterPresentation) {
    int previous{}, guest{};
    std::queue<int*> queue{};
    queue.push(&previous);
    auto* owned = TryPopMatchingReadyIdleFrame(queue, &previous,
                                              [](int*) { return true; });
    ASSERT_EQ(owned, &previous);
    queue.push(&guest);
    queue.push(owned); // Emulate Present(frame, false, false) returning ownership.
    ASSERT_EQ(queue.size(), 2u);
    EXPECT_EQ(queue.front(), &guest);
    queue.pop();
    EXPECT_EQ(queue.front(), &previous);
}

TEST(HostIdleFrameReuse, HostOverlayAndReusedImageNeverCountAsNewGameFlip) {
    EXPECT_FALSE(IsRealGuestFrameSubmission(false, false, false));
    EXPECT_FALSE(IsRealGuestFrameSubmission(false, false, true));
    EXPECT_FALSE(IsRealGuestFrameSubmission(true, false, false));
    EXPECT_FALSE(IsRealGuestFrameSubmission(true, true, true));
}

TEST(HostIdleFrameReuse, OnlyFreshSubmittedGuestImageCounts) {
    EXPECT_TRUE(IsRealGuestFrameSubmission(true, false, true));
    EXPECT_FALSE(IsRealGuestFrameSubmission(true, true, false));
}

} // namespace
