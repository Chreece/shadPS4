// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <array>

#include <gtest/gtest.h>

#include "shader_recompiler/resource.h"

namespace {

using Sharp4 = Shader::SharpFetch<std::array<u32, 4>>;
using Sharp8 = Shader::SharpFetch<std::array<u32, 8>>;

TEST(SharpFetch, MixedImmediatesRetainTheirValue) {
    Sharp4 sharp{};
    sharp.immediates[0] = 0xabcdef01;
    sharp.offsets[1] = 1;
    sharp.offsets[2] = 2;
    sharp.offsets[3] = 3;
    sharp.load_mask = 0b1110;
    EXPECT_FALSE(sharp.TrySetSingleLoad(4));
    EXPECT_EQ(sharp.summary, Sharp4::Summary::MultiLoad);

    const std::array<u32, 4> flatbuf{101, 102, 103, 104};
    std::array<u32, 4> value{};
    ASSERT_TRUE(sharp.Fetch(flatbuf.data(), &value));
    EXPECT_EQ(value, (std::array<u32, 4>{0xabcdef01, 102, 103, 104}));
}

TEST(SharpFetch, AllImmediateDoesNotReadFlatbuf) {
    Sharp4 sharp{};
    sharp.immediates = {7, 11, 13, 17};
    EXPECT_FALSE(sharp.TrySetSingleLoad(4));
    const std::array<u32, 4> flatbuf{101, 102, 103, 104};
    std::array<u32, 4> value{};
    ASSERT_TRUE(sharp.Fetch(flatbuf.data(), &value));
    EXPECT_EQ(value, (std::array<u32, 4>{7, 11, 13, 17}));
}

TEST(SharpFetch, ConsecutiveScalarDwordsUseSingleLoad) {
    Sharp4 sharp{};
    sharp.load_mask = 0b1111;
    sharp.offsets = {2, 3, 4, 5};
    ASSERT_TRUE(sharp.TrySetSingleLoad(4));
    EXPECT_EQ(sharp.summary, Sharp4::Summary::SingleLoad);
    const std::array<u32, 6> flatbuf{0, 1, 42, 43, 44, 45};
    std::array<u32, 4> value{};
    ASSERT_TRUE(sharp.Fetch(flatbuf.data(), &value));
    EXPECT_EQ(value, (std::array<u32, 4>{42, 43, 44, 45}));
}

TEST(SharpFetch, StridedScalarDwordsUseMultiLoad) {
    Sharp4 sharp{};
    sharp.load_mask = 0b1111;
    sharp.offsets = {1, 3, 5, 7};
    EXPECT_FALSE(sharp.TrySetSingleLoad(4));
    const std::array<u32, 8> flatbuf{0, 10, 0, 20, 0, 30, 0, 40};
    std::array<u32, 4> value{};
    ASSERT_TRUE(sharp.Fetch(flatbuf.data(), &value));
    EXPECT_EQ(value, (std::array<u32, 4>{10, 20, 30, 40}));
}

TEST(SharpFetch, InvalidSourceCannotBeCoalesced) {
    Sharp4 sharp{};
    sharp.summary = Sharp4::Summary::Invalid;
    sharp.load_mask = 0b1111;
    sharp.offsets = {0, 1, 2, 3};
    EXPECT_FALSE(sharp.TrySetSingleLoad(4));
    std::array<u32, 4> value{};
    const std::array<u32, 4> flatbuf{};
    EXPECT_FALSE(sharp.Fetch(flatbuf.data(), &value));
}

TEST(SharpFetch, HalfWidthDescriptorPreservesDwordCount) {
    Sharp8 sharp{};
    sharp.load_mask = 0b00001111;
    sharp.offsets = {0, 1, 2, 3, 0, 0, 0, 0};
    ASSERT_TRUE(sharp.TrySetSingleLoad(4));
    std::array<u32, 8> value{};
    const std::array<u32, 8> flatbuf{10, 20, 30, 40, 50, 60, 70, 80};
    ASSERT_TRUE(sharp.Fetch<4>(flatbuf.data(), &value));
    EXPECT_EQ(value, (std::array<u32, 8>{10, 20, 30, 40, 0, 0, 0, 0}));
}

} // namespace
