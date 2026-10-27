// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <cstdint>
#include <random>
#include <gtest/gtest.h>

#include "video_core/buffer_cache/formatted_stream_route.h"

namespace {

TEST(FormattedStreamRoute, NonemptyFormattedBufferNeverBypassesImageSynchronization) {
    constexpr std::uint32_t sizes[] = {1, 4, 192, 20288, 65536, 131072, 0xffffffffu};
    for (const auto size : sizes) {
        EXPECT_FALSE(VideoCore::MayBypassImageSyncForStream(true, size)) << size;
    }
}

TEST(FormattedStreamRoute, OrdinaryBufferStillEligibleForStream) {
    constexpr std::uint32_t sizes[] = {0, 1, 4, 192, 20288, 65536, 0xffffffffu};
    for (const auto size : sizes) {
        EXPECT_TRUE(VideoCore::MayBypassImageSyncForStream(false, size)) << size;
    }
}

TEST(FormattedStreamRoute, EmptyFormattedReadPreservesZeroLengthBehavior) {
    EXPECT_TRUE(VideoCore::MayBypassImageSyncForStream(true, 0));
}

TEST(FormattedStreamRoute, SeededFlagAndSizeMatrix) {
    std::mt19937 rng{0x91c0ffeeu};
    for (unsigned i = 0; i < 100000; ++i) {
        const bool formatted = (rng() & 1u) != 0;
        const std::uint32_t size = rng();
        EXPECT_EQ(VideoCore::MayBypassImageSyncForStream(formatted, size),
                  !formatted || size == 0);
    }
}

} // namespace
