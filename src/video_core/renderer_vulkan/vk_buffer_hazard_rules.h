// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once

#include <vulkan/vulkan.hpp>

namespace Vulkan::BufferHazards {

// vkCmdDrawIndexedIndirect* / vkCmdDrawIndirect* consume their arguments
// at VK_PIPELINE_STAGE_2_DRAW_INDIRECT_BIT with
// VK_ACCESS_2_INDIRECT_COMMAND_READ_BIT. This access must participate in
// read-after-write AND write-after-read tracking. In particular, GPU-driven
// rendering can generate and consume many argument commands in one frame.
//
// Previously AccessBuffer omitted eIndirectCommandRead from its READ_MASK,
// so a later shader/transfer write to the same range could fail to insert
// the necessary execution dependency after an indirect command read.
inline constexpr vk::AccessFlags2 ReadMask =
    vk::AccessFlagBits2::eIndirectCommandRead |
    vk::AccessFlagBits2::eIndexRead |
    vk::AccessFlagBits2::eVertexAttributeRead |
    vk::AccessFlagBits2::eUniformRead |
    vk::AccessFlagBits2::eShaderRead |
    vk::AccessFlagBits2::eColorAttachmentRead |
    vk::AccessFlagBits2::eDepthStencilAttachmentRead |
    vk::AccessFlagBits2::eTransferRead |
    vk::AccessFlagBits2::eMemoryRead;

[[nodiscard]] constexpr bool IsTrackedRead(const vk::AccessFlags2 access) noexcept {
    return static_cast<bool>(access & ReadMask);
}

// VK_PIPELINE_STAGE_2_ALL_GRAPHICS_BIT alone is not a substitute for the
// explicit draw-indirect argument fetch stage. Keep the existing graphics/
// compute stage and ALSO include draw-indirect for command-buffer reads.
[[nodiscard]] constexpr vk::PipelineStageFlags2 StageForBoundBuffer(
    const vk::AccessFlags2 access, const bool is_compute) noexcept {
    vk::PipelineStageFlags2 stages =
        is_compute ? vk::PipelineStageFlagBits2::eComputeShader
                   : vk::PipelineStageFlagBits2::eAllGraphics;
    if (access & vk::AccessFlagBits2::eIndirectCommandRead) {
        stages |= vk::PipelineStageFlagBits2::eDrawIndirect;
    }
    return stages;
}

} // namespace Vulkan::BufferHazards
