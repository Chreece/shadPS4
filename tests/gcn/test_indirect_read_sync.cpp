// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#include <gtest/gtest.h>

#include "video_core/renderer_vulkan/vk_indirect_read_sync.h"

namespace {

using Vulkan::BufferAccessStage;
using Vulkan::TrackedBufferReadAccesses;

TEST(IndirectCommandSynchronization, IndirectCommandIsTrackedAsRead) {
    // If omitted, a following buffer write may miss a write-after-read
    // execution dependency for vkCmdDispatchIndirect / vkCmdDrawIndirect.
    EXPECT_TRUE(bool(TrackedBufferReadAccesses & vk::AccessFlagBits2::eIndirectCommandRead));
}

TEST(IndirectCommandSynchronization, DispatchIndirectArgumentsUseDrawIndirectStage) {
    EXPECT_EQ(BufferAccessStage(vk::AccessFlagBits2::eIndirectCommandRead, true),
              vk::PipelineStageFlags2{vk::PipelineStageFlagBits2::eDrawIndirect});
}

TEST(IndirectCommandSynchronization, DrawIndirectArgumentsUseDrawIndirectStage) {
    EXPECT_EQ(BufferAccessStage(vk::AccessFlagBits2::eIndirectCommandRead, false),
              vk::PipelineStageFlags2{vk::PipelineStageFlagBits2::eDrawIndirect});
}

TEST(IndirectCommandSynchronization, OrdinaryComputeShaderReadsKeepComputeStage) {
    EXPECT_EQ(BufferAccessStage(vk::AccessFlagBits2::eShaderRead, true),
              vk::PipelineStageFlags2{vk::PipelineStageFlagBits2::eComputeShader});
}

TEST(IndirectCommandSynchronization, GraphicsVertexReadsKeepGraphicsStage) {
    EXPECT_EQ(BufferAccessStage(vk::AccessFlagBits2::eVertexAttributeRead, false),
              vk::PipelineStageFlags2{vk::PipelineStageFlagBits2::eAllGraphics});
}

TEST(IndirectCommandSynchronization, CombinedAccessRetainsIndirectStage) {
    const auto access =
        vk::AccessFlagBits2::eIndirectCommandRead | vk::AccessFlagBits2::eShaderRead;
    EXPECT_EQ(BufferAccessStage(access, true),
              vk::PipelineStageFlags2{vk::PipelineStageFlagBits2::eDrawIndirect});
}

} // namespace
