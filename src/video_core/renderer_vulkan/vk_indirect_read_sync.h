// SPDX-FileCopyrightText: Copyright 2026 shadPS4 Emulator Project
// SPDX-License-Identifier: GPL-2.0-or-later

#pragma once

#include "video_core/renderer_vulkan/vk_common.h"

namespace Vulkan {

// vkCmdDrawIndirect* and vkCmdDispatchIndirect consume their argument buffers
// through INDIRECT_COMMAND_READ at DRAW_INDIRECT, not COMPUTE_SHADER or
// ALL_GRAPHICS. Track these reads so a subsequent GPU write has a proper
// write-after-read execution dependency.
inline constexpr vk::AccessFlags2 TrackedBufferReadAccesses =
    vk::AccessFlagBits2::eIndirectCommandRead | vk::AccessFlagBits2::eIndexRead |
    vk::AccessFlagBits2::eVertexAttributeRead | vk::AccessFlagBits2::eUniformRead |
    vk::AccessFlagBits2::eShaderRead | vk::AccessFlagBits2::eColorAttachmentRead |
    vk::AccessFlagBits2::eDepthStencilAttachmentRead |
    vk::AccessFlagBits2::eTransferRead | vk::AccessFlagBits2::eMemoryRead;

[[nodiscard]] constexpr vk::PipelineStageFlags2 BufferAccessStage(
    vk::AccessFlags2 access, bool compute) noexcept {
    if (access & vk::AccessFlagBits2::eIndirectCommandRead) {
        return vk::PipelineStageFlagBits2::eDrawIndirect;
    }
    return compute ? vk::PipelineStageFlagBits2::eComputeShader
                   : vk::PipelineStageFlagBits2::eAllGraphics;
}

static_assert(bool(TrackedBufferReadAccesses & vk::AccessFlagBits2::eIndirectCommandRead));
static_assert(BufferAccessStage(vk::AccessFlagBits2::eIndirectCommandRead, true) ==
              vk::PipelineStageFlagBits2::eDrawIndirect);
static_assert(BufferAccessStage(vk::AccessFlagBits2::eIndirectCommandRead, false) ==
              vk::PipelineStageFlagBits2::eDrawIndirect);

} // namespace Vulkan
